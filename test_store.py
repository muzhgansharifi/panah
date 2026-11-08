"""Tests for store: the connection, the users/settings tables, JSON helpers, connections from different threads."""
import re
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from server import store  # noqa: E402

USER_COLUMNS = ['id', 'phone', 'name', 'blood_group', 'health', 'contacts', 'home_lat', 'home_lon', 'home_address',
                'last_lat', 'last_lon', 'created_at']


class StoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self._tmp.name) / 'nested' / 'data' / 'panah.db'
        self.db = store.connect(self.path)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def test_connect_settings(self):
        self.assertTrue(self.path.is_file(), 'folder created, database file exists')
        self.assertEqual(self.db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertEqual(self.db.execute('PRAGMA journal_mode').fetchone()[0].lower(), 'wal')
        self.assertGreaterEqual(self.db.execute('PRAGMA busy_timeout').fetchone()[0], 5000)
        self.assertIs(self.db.row_factory, sqlite3.Row)

    def test_tables(self):
        columns = [row['name'] for row in self.db.execute('PRAGMA table_info(users)')]
        self.assertEqual(columns, USER_COLUMNS)
        columns = [row['name'] for row in self.db.execute('PRAGMA table_info(settings)')]
        self.assertEqual(columns, ['key', 'value'])

    def test_user_defaults_and_unique_phone(self):
        self.db.execute("INSERT INTO users (phone) VALUES ('+93777123454')")
        self.db.commit()
        user = store.get_user(self.db, 1)
        self.assertEqual(user['phone'], '+93777123454')
        self.assertEqual((user['name'], user['blood_group'], user['home_address']), ('', '', ''))
        self.assertEqual((user['health'], user['contacts']), ([], []))
        self.assertIsNone(user['home_lat'])
        self.assertRegex(user['created_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO users (phone) VALUES ('+93777123454')")
        self.assertIsNone(store.get_user(self.db, 999))

    def test_user_json_fields(self):
        contacts = [{'name': 'همسر', 'phone': '+93700111222'}, {'name': 'پدر', 'phone': '+93700333444'}]
        self.db.execute('INSERT INTO users (phone, name, health, contacts) VALUES (?, ?, ?, ?)',
                        ('+93700000001', None, store.dumps(['دیابت', 'فشار خون']), store.dumps(contacts)))
        self.db.execute("INSERT INTO users (phone, health, contacts) VALUES ('+93700000002', NULL, 'not json')")
        raw = self.db.execute('SELECT contacts FROM users WHERE id = 1').fetchone()[0]
        self.assertIn('همسر', raw, 'Dari in the database as is, without \\uXXXX')
        first = store.get_user(self.db, 1)
        self.assertEqual(first['health'], ['دیابت', 'فشار خون'])
        self.assertEqual(first['contacts'], contacts)
        self.assertEqual(first['name'], '', 'NULL in a text field → empty string')
        second = store.get_user(self.db, 2)
        self.assertEqual((second['health'], second['contacts']), ([], []), 'NULL and broken JSON → []')

    def test_json_helpers(self):
        self.assertEqual(store.loads(store.dumps({'a': ['ب', 1, None]})), {'a': ['ب', 1, None]})
        self.assertEqual(store.dumps(['ب']), '["ب"]')
        for empty in (None, '', b''):
            self.assertEqual(store.loads(empty, 'd'), 'd')
        self.assertEqual(store.loads('{oops', []), [])
        self.assertEqual(store.loads([1, 2]), [1, 2])
        rows = self.db.execute("SELECT 1 AS n, '[1,2]' AS list, NULL AS empty_one").fetchall()
        self.assertEqual(store.row_dicts(rows, ('list', 'empty_one')), [{'n': 1, 'list': [1, 2], 'empty_one': None}])
        self.assertIsNone(store.row_dict(None))

    def test_settings(self):
        self.assertEqual(store.get_setting(self.db, 'missing', 7), 7)
        store.set_setting(self.db, 'usgs_last', {'time': '2026-11-06T00:00:00Z', 'place': 'کابل'})
        store.set_setting(self.db, 'count', 1)
        store.set_setting(self.db, 'count', 2)
        self.db.commit()
        again = store.connect(self.path)
        try:
            self.assertEqual(store.get_setting(again, 'usgs_last')['place'], 'کابل')
            self.assertEqual(store.get_setting(again, 'count'), 2)
        finally:
            again.close()

    def test_data_dir(self):
        self.assertEqual(store.data_dir(self.db), self.path.parent.resolve())
        memory = store.connect(':memory:')
        try:
            self.assertIsNone(store.data_dir(memory))
            self.assertEqual(memory.execute('SELECT count(*) FROM users').fetchone()[0], 0)
        finally:
            memory.close()

    def test_foreign_keys_enforced(self):
        self.db.execute('CREATE TABLE notes (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id))')
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute('INSERT INTO notes (user_id) VALUES (42)')

    def test_transaction(self):
        with store.transaction(self.path) as db:
            db.execute("INSERT INTO users (phone) VALUES ('+93700000010')")
        with self.assertRaises(RuntimeError):
            with store.transaction(self.path) as db:
                db.execute("INSERT INTO users (phone) VALUES ('+93700000011')")
                raise RuntimeError('boom')
        phones = [row[0] for row in self.db.execute('SELECT phone FROM users ORDER BY id')]
        self.assertEqual(phones, ['+93700000010'], 'success: saved; error: rolled back')

    def test_reconnect_keeps_data(self):
        self.db.execute("INSERT INTO users (phone) VALUES ('+93700000020')")
        self.db.commit()
        self.db.close()
        self.db = store.connect(self.path)
        self.assertEqual(self.db.execute('SELECT count(*) FROM users').fetchone()[0], 1)

    def test_connection_per_thread(self):
        errors = []

        def worker(n):
            try:
                db = store.connect(self.path)
                try:
                    for i in range(25):
                        db.execute('INSERT INTO users (phone) VALUES (?)', ('+937%02d%07d' % (n, i),))
                        db.commit()
                finally:
                    db.close()
            except Exception as exc:  # noqa: BLE001 — collect and show
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(self.db.execute('SELECT count(*) FROM users').fetchone()[0], 100)

    def test_connection_not_shared_between_threads(self):
        result = []
        thread = threading.Thread(target=lambda: result.append(_try_use(self.db)))
        thread.start()
        thread.join(5)
        self.assertEqual(result, ['refused'], "one thread's connection does not work in another: open your own")


def _try_use(db):
    try:
        db.execute('SELECT 1')
    except sqlite3.ProgrammingError:
        return 'refused'
    return 'used'


class ConnectOnceTest(unittest.TestCase):
    """The expensive part (WAL, tables) runs once per file per process; each connection gets its own settings."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self._tmp.name) / 'panah.db'

    def tearDown(self):
        self._tmp.cleanup()

    def test_schema_once_and_again_after_file_removed(self):
        calls = []
        real = store._ensure_schema
        with mock.patch.object(store, '_ensure_schema', side_effect=lambda db: (calls.append(1), real(db))):
            for _ in range(3):
                store.connect(self.path).close()
            self.assertEqual(len(calls), 1, 'tables are checked once per file')
            for name in ('panah.db', 'panah.db-wal', 'panah.db-shm'):
                (Path(self._tmp.name) / name).unlink(missing_ok=True)
            db = store.connect(self.path)
            try:
                self.assertEqual(len(calls), 2, 'file deleted: set up again')
                self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name = 'users'").fetchone()[0], 1)
            finally:
                db.close()

    def test_each_connection_keeps_own_settings(self):
        store.connect(self.path).close()
        db = store.connect(self.path)  # now without the file setup
        try:
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertGreaterEqual(db.execute('PRAGMA busy_timeout').fetchone()[0], 5000)
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0].lower(), 'wal')
            self.assertEqual(db.execute('PRAGMA synchronous').fetchone()[0], 1, 'NORMAL')
            self.assertIs(db.row_factory, sqlite3.Row)
        finally:
            db.close()


class PoolTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self._tmp.name) / 'panah.db'
        self.pool = store.Pool(self.path, size=2)

    def tearDown(self):
        self.pool.close()
        self._tmp.cleanup()

    def test_reuse_with_settings(self):
        db = self.pool.acquire()
        self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertIs(db.row_factory, sqlite3.Row)
        db.row_factory = None
        self.pool.release(db)
        again = self.pool.acquire()
        self.assertIs(again, db, 'a returned connection is used again')
        self.assertIs(again.row_factory, sqlite3.Row, 'row settings restored')
        self.pool.release(again)

    def test_release_rolls_back_unfinished_transaction(self):
        db = self.pool.acquire()
        db.execute("INSERT INTO users (phone) VALUES ('+93700000030')")
        self.pool.release(db)
        again = self.pool.acquire()
        self.assertFalse(again.in_transaction)
        self.assertEqual(again.execute('SELECT count(*) FROM users').fetchone()[0], 0)
        self.pool.release(again)

    def test_sees_writes_of_others(self):
        db = self.pool.acquire()
        db.execute('SELECT count(*) FROM users').fetchone()
        self.pool.release(db)
        with store.transaction(self.path) as other:
            other.execute("INSERT INTO users (phone) VALUES ('+93700000031')")
        db = self.pool.acquire()
        self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0], 1, 'no stale snapshot')
        self.pool.release(db)

    def test_other_thread_can_use_it(self):
        db = self.pool.acquire()
        result = []
        thread = threading.Thread(target=lambda: result.append(db.execute('SELECT 1').fetchone()[0]))
        thread.start()
        thread.join(5)
        self.assertEqual(result, [1])
        self.pool.release(db)

    def test_size_discard_and_close(self):
        taken = [self.pool.acquire() for _ in range(3)]
        self.assertEqual(len({id(db) for db in taken}), 3, 'a taken connection is not handed out twice')
        for db in taken:
            self.pool.release(db)
        self.assertEqual(self.pool.idle(), 2)
        with self.assertRaises(sqlite3.ProgrammingError):
            taken[2].execute('SELECT 1')  # the extra one is closed
        broken = self.pool.acquire()
        self.pool.discard(broken)
        with self.assertRaises(sqlite3.ProgrammingError):
            broken.execute('SELECT 1')
        late = self.pool.acquire()
        self.pool.close()
        self.assertEqual(self.pool.idle(), 0)
        self.pool.release(late)
        self.assertEqual(self.pool.idle(), 0, 'after close, a returned connection is closed')
        with self.assertRaises(sqlite3.ProgrammingError):
            late.execute('SELECT 1')

    def test_parallel_writers(self):
        errors = []

        def worker(n):
            try:
                for i in range(50):
                    db = self.pool.acquire()
                    try:
                        db.execute('INSERT INTO users (phone) VALUES (?)', ('+937%02d%07d' % (n, i),))
                        db.commit()
                    finally:
                        self.pool.release(db)
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(errors, [])
        db = self.pool.acquire()
        self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0], 400)
        self.pool.release(db)


class TimeFormatTest(unittest.TestCase):
    def test_created_at_matches_common_utcnow(self):
        from server.common import utcnow
        self.assertTrue(re.match(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$', utcnow()))


if __name__ == '__main__':
    unittest.main()
