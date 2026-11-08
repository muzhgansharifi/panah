"""store — PANAH database: SQLite, a single file data/panah.db.

connect(path) → sqlite3.Connection:
  - rows are sqlite3.Row: row['phone'], dict(row);
  - foreign keys on, WAL journal, waits up to 10 s for another writer;
  - on the first connection to a file it creates the shared tables users and settings.
  Expensive setup (WAL, checking and creating tables) runs once per file per process; after that connect only
  opens the connection and turns on its per-connection settings (foreign keys, synchronous, lock wait).
Other tables belong to their engines, created in their init(db) (CREATE TABLE IF NOT EXISTS ...).

Pool(path) — connection pool for web requests: acquire → work (one thread at a time) → release.

One connection per thread or request; it is never given to two threads at once:
  - in a request it is req.db: web takes it from the pool, commits at the end and returns it to the pool
    (rollback only if the handler failed with something other than ApiError);
  - in init(db) web provides the connection, and web also commits after init;
  - a background thread opens its own: store.connect(app.db_path) and does commit/close itself,
    or `with store.transaction(app.db_path) as db: ...`, which commits and closes by itself.
Slow work (network, Nominatim, USGS) goes before the first database write, not inside it: a write locks the database.

users: id, phone ('+937XXXXXXXX', unique), name, blood_group, health (JSON list of strings),
contacts (JSON list of {name, phone}), home_lat, home_lon, home_address, last_lat, last_lon, created_at (UTC ISO).
settings: key → value (JSON): get_setting / set_setting.
Helpers: dumps / loads for JSON fields; row_dict / row_dicts / user_dict turn rows into dicts; get_user; data_dir.
Helpers do not commit.
"""
import contextlib
import json
import os
import sqlite3
import threading
from pathlib import Path

BUSY_TIMEOUT_MS = 10000
USER_JSON_FIELDS = ('health', 'contacts')
USER_TEXT_FIELDS = ('name', 'blood_group', 'home_address')

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    phone        TEXT NOT NULL UNIQUE,
    name         TEXT DEFAULT '',
    blood_group  TEXT DEFAULT '',
    health       TEXT DEFAULT '[]',
    contacts     TEXT DEFAULT '[]',
    home_lat     REAL,
    home_lon     REAL,
    home_address TEXT DEFAULT '',
    last_lat     REAL,
    last_lon     REAL,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


_ready = set()                  # files where this process already turned on WAL and created the shared tables
_setup_lock = threading.Lock()  # one thread does the first setup of a file, the others wait for it


def connect(path):
    """Open the database file (its folder is created) or ':memory:' → sqlite3.Connection with users and settings."""
    return _open(path, check_same_thread=True)


def _open(path, check_same_thread):
    if str(path) == ':memory:':
        db = _raw(':memory:', check_same_thread)
        try:
            _ensure_schema(db)
        except BaseException:
            db.close()
            raise
        return db
    path = Path(path)
    key = os.path.normcase(str(path.resolve()))
    if key not in _ready or not path.exists():  # file was deleted: set it up again
        with _setup_lock:
            if key not in _ready or not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                db = _raw(path, check_same_thread)
                try:
                    db.execute('PRAGMA journal_mode = WAL')  # stored in the file itself
                    _ensure_schema(db)
                except BaseException:
                    db.close()
                    raise
                _ready.add(key)
                return db
    return _raw(path, check_same_thread)


def _raw(target, check_same_thread):
    """Connection with its per-connection settings: Row rows, foreign keys, synchronous, lock wait."""
    db = sqlite3.connect(str(target), timeout=BUSY_TIMEOUT_MS / 1000, check_same_thread=check_same_thread)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
        if str(target) != ':memory:':
            db.execute('PRAGMA synchronous = NORMAL')
    except BaseException:
        db.close()
        raise
    return db


def _ensure_schema(db):
    found = db.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name IN ('users', 'settings')"
    ).fetchone()[0]
    if found < 2:
        db.executescript(SCHEMA)


class Pool:
    """Connection pool for requests: acquire → work (one thread at a time) → release.

        db = pool.acquire()   # a ready connection, as from connect(); usable from any thread
        ...; db.commit()
        pool.release(db)      # an open transaction is rolled back; with more than size idle, it is closed
        pool.discard(db)      # broken: close it, do not return it
        pool.close()          # shutdown: close the idle ones; ones returned later get closed
    Nobody has to wait: if all are busy, it opens one more. Connections live long, so closing a connection
    at the end of a request no longer triggers a WAL checkpoint into the database file with a disk write.
    """

    def __init__(self, path, size=16):
        self.path = path
        self.size = int(size)
        self._idle = []
        self._lock = threading.Lock()
        self._closed = False

    def acquire(self):
        with self._lock:
            if self._idle:
                return self._idle.pop()
        return _open(self.path, check_same_thread=False)

    def release(self, db):
        try:
            if db.in_transaction:
                db.rollback()
            db.row_factory = sqlite3.Row
        except sqlite3.Error:
            self.discard(db)
            return
        with self._lock:
            if not self._closed and len(self._idle) < self.size:
                self._idle.append(db)
                return
        db.close()

    def discard(self, db):
        try:
            db.close()
        except sqlite3.Error:
            pass

    def idle(self):
        """How many idle connections are waiting in the pool."""
        with self._lock:
            return len(self._idle)

    def close(self):
        with self._lock:
            self._closed = True
            idle, self._idle = self._idle, []
        for db in idle:
            self.discard(db)


@contextlib.contextmanager
def transaction(path):
    """with store.transaction(path) as db: ... — own connection; commit on success, rollback on error, then close."""
    db = connect(path)
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def dumps(value):
    """Value → JSON text for a database field (Dari as is, no \\uXXXX)."""
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def loads(text, default=None):
    """JSON text from a database field → value; empty, NULL or broken → default. Already a list/dict → as is."""
    if text is None or text == '' or text == b'':
        return default
    if isinstance(text, (list, dict)):
        return text
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def row_dict(row, json_fields=()):
    """sqlite3.Row (or None) → dict (or None); fields in json_fields are parsed from JSON (empty → None)."""
    if row is None:
        return None
    data = dict(row)
    for field in json_fields:
        if field in data:
            data[field] = loads(data[field])
    return data


def row_dicts(rows, json_fields=()):
    """Several rows (cursor or list) → list of dicts."""
    return [row_dict(row, json_fields) for row in rows]


def user_dict(row):
    """users row → dict: health and contacts are lists (empty → []), text fields are never None (→ '')."""
    data = row_dict(row, USER_JSON_FIELDS)
    if data is None:
        return None
    for field in USER_JSON_FIELDS:
        if field in data and not isinstance(data[field], list):
            data[field] = []
    for field in USER_TEXT_FIELDS:
        if field in data and data[field] is None:
            data[field] = ''
    return data


def get_user(db, user_id):
    """Resident by id → dict (as user_dict) or None."""
    return user_dict(db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone())


def get_setting(db, key, default=None):
    """Value from settings (JSON), or default if there is no such key."""
    row = db.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    if row is None:
        return default
    return loads(row['value'], default)


def set_setting(db, key, value):
    """Write a value (anything that fits in JSON) to settings. The caller commits."""
    db.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, dumps(value)))


def data_dir(db):
    """Folder of the database file (Path), where logs and files go too (data/); None for ':memory:'."""
    for row in db.execute('PRAGMA database_list'):
        if row[1] == 'main':
            return Path(row[2]).parent if row[2] else None
    return None
