"""sos — درخواست کمک (SOS) and the resident's chat with rescuers: figma screens 10–13 (resident) and 18 (admin).

Contract:
  init(db)  — own tables sos_cases, sos_messages, sos_locations (calling again duplicates nothing).
  routes()  — resident (login not required: then the owner is req.device_id):
      POST /api/sos {kind: default|custom, text, lat, lon, accuracy, battery, signal} → {case, messages}
      GET  /api/sos/current                      → {case, messages} or {case: null, messages: []}
      GET  /api/sos/{id}/messages?after=ID       → {case, messages}  (messages: only id > after)
      POST /api/sos/{id}/messages {text}         → {case, message, messages}  (messages: new, incl. the auto-reply)
      POST /api/sos/{id}/location {lat, lon, accuracy, battery, signal} → {case, point}  (client sends every 30 s)
    admin:
      GET  /api/admin/sos?status=open|closed|all → {cases: [{case, user, last_message, place_name, kind}]}, newest first
      GET  /api/admin/sos/{id}                   → {case, user, messages, track}
      POST /api/admin/sos/{id}/messages {text}   → {case, message}  — from 'rescuer', an event to the resident
      POST /api/admin/sos/{id}/close             → {case}
  stats(db) → {open, today} — open now; filed today (the Kabul day, UTC+4:30).

Rules:
  - One open case per resident (user_id) or guest device (device_id): a repeated SOS adds a message to it.
    A guest with an open case logs in and presses SOS again: same case, and the resident is linked to it.
  - kind=default — text DEFAULT_TEXT (figma 10), text_key 'sos_default'. custom without text → treated as default:
    an SOS is never rejected for an empty field. Bad coordinates in POST /api/sos: the case is still created (no place).
  - Auto-reply (sender 'auto'; text in Dari, text_key for the client to translate into Pashto):
      'sos_auto_received' — right away on a new case (figma 12);
      'sos_auto_wait'     — once per case: on the resident's first message after the SOS, while no rescuer has answered
                            (figma 13: "ما پیغام شما را دریافت کردیم…", "در همین حال:", three points, number ۱۱۹).
  - Access: the owner (user_id matches; or device_id matches if the case is a guest's or a guest asks) and the admin
    (the admin only reads via the resident path; writes go via /api/admin/…). No case → 404, someone else's → 403,
    closed → 409 on write.
  - The network does not delay an SOS: the case is saved and sent at once as a 'sos_case' event with a fallback
    place label (the nearest known place, geo.nearest_known, no network; or empty). The real label (geo.place_name,
    Nominatim) comes in the background; once it arrives, the database is updated and 'sos_case' goes again: same id,
    is_new false, changed 'place_name'. Same when moving farther than 300 m (repeated SOS, a point every 30 s).
    wait_idle(timeout) — wait for the background labels (for tests).
  - live events (after commit): 'sos_case' — card {case, user, last_message, place_name, kind, is_new} to admins
    (on close and on a new place label, to the owner too); 'sos_message' {case_id, message} to admins, a rescuer's reply
    to the owner as well; 'sos_location' {case_id, lat, lon, accuracy_m, battery, signal, at, place_name} to admins.
  - Other engines (geo, live, auth) are imported lazily: no module → no place label, no event, the admin is taken
    from req.admin. An SOS never fails because of them.
battery — percent 0–100 (integer); signal — a short string or number (as the client sent it, up to 20 characters).
"""
import datetime
import logging
import math
import queue
import threading
from pathlib import Path

from server.common import ApiError, utcnow

log = logging.getLogger(__name__)

KINDS = ('default', 'custom')
STATUSES = ('open', 'closed')
MAX_TEXT = 2000
PLACE_REFRESH_KM = 0.3  # the place is labeled again after moving farther than this
ADMIN_LIST_LIMIT = 200
KABUL_TZ = datetime.timezone(datetime.timedelta(hours=4, minutes=30))
TIME_FORMAT = '%Y-%m-%dT%H:%M:%SZ'  # as in utcnow()

DEFAULT_TEXT = 'من در وضعیت اضطراری قرار دارم و نیاز به کمک فوری دارم.'
AUTO_RECEIVED = ('پیغام شما دریافت شد. تیم نجات در حال حرکت است. موقعیت زنده شما ردیابی می‌شود. '
                 'لطفاً در جای امن بمانید.')
AUTO_WAIT = ('ما پیغام شما را دریافت کردیم. لطفاً منتظر بمانید، یکی از اعضای تیم به زودی با شما تماس خواهد گرفت.\n'
             'در همین حال:\n'
             '✓ موقعیت زنده شما ردیابی می‌شود\n'
             '✓ در جای امن بمانید\n'
             '✓ باتری خود را حفظ کنید\n'
             'شماره تماس مستقیم: ۱۱۹')
TEXT_KEYS = {
    'sos_default': DEFAULT_TEXT,
    'sos_auto_received': AUTO_RECEIVED,
    'sos_auto_wait': AUTO_WAIT,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sos_cases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NULL,
    device_id   TEXT,
    kind        TEXT NOT NULL DEFAULT 'default' CHECK (kind IN ('default', 'custom')),
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    lat         REAL,
    lon         REAL,
    accuracy_m  REAL,
    place_name  TEXT,
    battery     INTEGER,
    signal      TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    closed_at   TEXT
);
CREATE INDEX IF NOT EXISTS sos_cases_status ON sos_cases (status, created_at);
CREATE INDEX IF NOT EXISTS sos_cases_user ON sos_cases (user_id, status);
CREATE INDEX IF NOT EXISTS sos_cases_device ON sos_cases (device_id, status);
CREATE TABLE IF NOT EXISTS sos_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id     INTEGER NOT NULL REFERENCES sos_cases (id) ON DELETE CASCADE,
    sender      TEXT NOT NULL CHECK (sender IN ('user', 'rescuer', 'auto')),
    admin_id    INTEGER,
    text        TEXT NOT NULL,
    text_key    TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sos_messages_case ON sos_messages (case_id, id);
CREATE TABLE IF NOT EXISTS sos_locations (
    case_id     INTEGER NOT NULL REFERENCES sos_cases (id) ON DELETE CASCADE,
    lat         REAL NOT NULL,
    lon         REAL NOT NULL,
    accuracy_m  REAL,
    battery     INTEGER,
    signal      TEXT,
    at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sos_locations_case ON sos_locations (case_id, at);
"""

CASE_FIELDS = ('id', 'user_id', 'kind', 'status', 'lat', 'lon', 'accuracy_m', 'place_name', 'battery', 'signal',
               'created_at', 'updated_at', 'closed_at')
MESSAGE_FIELDS = ('id', 'case_id', 'sender', 'admin_id', 'text', 'text_key', 'created_at')
POINT_FIELDS = ('lat', 'lon', 'accuracy_m', 'battery', 'signal', 'at')

# Finding the open case and writing to it happen under a lock: a double tap on SOS does not create two cases.
_lock = threading.Lock()


def init(db):
    """Create the SOS tables (if missing)."""
    db.executescript(SCHEMA)
    db.commit()


def routes():
    return [
        ('POST', '/api/sos', post_sos),
        ('GET', '/api/sos/current', get_current),
        ('GET', '/api/sos/{id}/messages', get_messages),
        ('POST', '/api/sos/{id}/messages', post_message),
        ('POST', '/api/sos/{id}/location', post_location),
        ('GET', '/api/admin/sos', admin_list),
        ('GET', '/api/admin/sos/{id}', admin_get),
        ('POST', '/api/admin/sos/{id}/messages', admin_post_message),
        ('POST', '/api/admin/sos/{id}/close', admin_close),
    ]


def stats(db):
    """{open: open cases, today: cases filed during the current Kabul day}. No tables → zeros."""
    since = kabul_day_start_utc()
    try:
        open_count = db.execute("SELECT count(*) FROM sos_cases WHERE status = 'open'").fetchone()[0]
        today = db.execute('SELECT count(*) FROM sos_cases WHERE created_at >= ?', (since,)).fetchone()[0]
    except Exception as exc:  # sqlite3.OperationalError: no such table — sos.init not called yet
        if 'no such table' not in str(exc):
            raise
        return {'open': 0, 'today': 0}
    return {'open': int(open_count), 'today': int(today)}


def kabul_day_start_utc(now=None):
    """Start of the current Kabul day (UTC+4:30) as a UTC string '…Z', to compare with created_at."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    local = now.astimezone(KABUL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(datetime.timezone.utc).strftime(TIME_FORMAT)


# ---------- resident ----------

def post_sos(req):
    db = req.db
    body = _body(req)
    kind = str(body.get('kind') or 'default').strip().lower()
    if kind not in KINDS:
        raise ApiError(400, 'bad_kind', 'نوع درخواست کمک نادرست است.')
    text = _text(body.get('text'))
    if kind == 'custom' and not text:
        kind = 'default'  # an empty "custom" message is still an SOS
    if kind == 'default':
        text, text_key = DEFAULT_TEXT, 'sos_default'
    else:
        text_key = None
    lat, lon = _latlon(body, strict=False)
    accuracy = _accuracy(body)
    battery = _battery(body.get('battery'))
    signal = _signal(body.get('signal'))
    uid, dev = _uid(req), _dev(req)
    place = _quick_place(db, lat, lon)  # no network; the real label comes in the background after the event
    now = utcnow()
    _release(db)
    with _lock:
        case = _find_open(db, uid, dev)
        is_new = case is None
        need_name = lat is not None and (is_new or _moved(case, lat, lon) or not case['place_name'])
        if is_new:
            cur = db.execute(
                'INSERT INTO sos_cases (user_id, device_id, kind, status, lat, lon, accuracy_m, place_name, battery,'
                ' signal, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (uid, dev, kind, 'open', lat, lon, accuracy, place, battery, signal, now, now))
            case_id = cur.lastrowid
        else:
            case_id = case['id']
            sets, args = ['updated_at = ?'], [now]
            if uid is not None and case['user_id'] is None:
                sets.append('user_id = ?')
                args.append(uid)
            if lat is not None:
                sets += ['lat = ?', 'lon = ?', 'accuracy_m = ?']
                args += [lat, lon, accuracy]
                if need_name:  # moved > 300 m or no label: fallback now, the real one comes from the background
                    sets.append('place_name = ?')
                    args.append(place)
            if battery is not None:
                sets.append('battery = ?')
                args.append(battery)
            if signal is not None:
                sets.append('signal = ?')
                args.append(signal)
            db.execute('UPDATE sos_cases SET %s WHERE id = ?' % ', '.join(sets), args + [case_id])
        point = None
        if lat is not None:
            point = _add_point(db, case_id, lat, lon, accuracy, battery, signal, now)
        new_messages = [_add_message(db, case_id, 'user', text, text_key, now)]
        if is_new:
            new_messages.append(_add_message(db, case_id, 'auto', AUTO_RECEIVED, 'sos_auto_received', now))
        else:
            auto = _auto_wait(db, case_id, now)
            if auto:
                new_messages.append(auto)
        db.commit()
        case = _get_case(db, case_id)

    card = _card(db, case)
    card['is_new'] = is_new
    _publish('sos_case', card, admins=True)
    for message in new_messages:
        _publish('sos_message', {'case_id': case_id, 'message': message}, admins=True)
    if point:
        _publish('sos_location', _location_event(case, point), admins=True)
    if need_name:
        _name_later(db, case_id, lat, lon)
    return 200, {'case': _case_out(case), 'messages': _messages(db, case_id)}


def get_current(req):
    db = req.db
    case = _find_open(db, _uid(req), _dev(req))
    if case is None:
        return 200, {'case': None, 'messages': []}
    return 200, {'case': _case_out(case), 'messages': _messages(db, case['id'])}


def get_messages(req):
    case = _owned_case(req, allow_admin=True)
    after = _query(req, 'after')
    try:
        after = int(after) if after not in (None, '') else 0
    except (TypeError, ValueError):
        raise ApiError(400, 'bad_after', 'شمارهٔ پیام نادرست است.')
    return 200, {'case': _case_out(case), 'messages': _messages(req.db, case['id'], after)}


def post_message(req):
    db = req.db
    case = _owned_case(req)
    _require_open(case)
    text = _text(_body(req).get('text'))
    if not text:
        raise ApiError(400, 'text_required', 'لطفاً پیام خود را بنویسید.')
    now = utcnow()
    _release(db)
    with _lock:
        message = _add_message(db, case['id'], 'user', text, None, now)
        new_messages = [message]
        auto = _auto_wait(db, case['id'], now)
        if auto:
            new_messages.append(auto)
        db.execute('UPDATE sos_cases SET updated_at = ? WHERE id = ?', (now, case['id']))
        db.commit()
    for item in new_messages:
        _publish('sos_message', {'case_id': case['id'], 'message': item}, admins=True)
    return 200, {'case': _case_out(_get_case(db, case['id'])), 'message': message, 'messages': new_messages}


def post_location(req):
    db = req.db
    case = _owned_case(req)
    _require_open(case)
    body = _body(req)
    lat, lon = _latlon(body, strict=True)
    accuracy = _accuracy(body)
    battery = _battery(body.get('battery'))
    signal = _signal(body.get('signal'))
    place = case['place_name']
    need_name = _moved(case, lat, lon) or not place
    if need_name:  # a fallback label for now; the real one comes in the background after the event
        place = _quick_place(db, lat, lon)
    now = utcnow()
    point = _add_point(db, case['id'], lat, lon, accuracy, battery, signal, now)
    db.execute(
        'UPDATE sos_cases SET lat = ?, lon = ?, accuracy_m = ?, place_name = ?, battery = coalesce(?, battery),'
        ' signal = coalesce(?, signal), updated_at = ? WHERE id = ?',
        (lat, lon, accuracy, place, battery, signal, now, case['id']))
    db.commit()
    case = _get_case(db, case['id'])
    _publish('sos_location', _location_event(case, point), admins=True)
    if need_name:
        _name_later(db, case['id'], lat, lon)
    return 200, {'case': _case_out(case), 'point': point}


# ---------- admin ----------

def admin_list(req):
    _require_admin(req)
    status = str(_query(req, 'status') or 'open').strip().lower()
    if status not in ('open', 'closed', 'all'):
        raise ApiError(400, 'bad_status', 'وضعیت نادرست است.')
    sql, args = 'SELECT * FROM sos_cases', []
    if status != 'all':
        sql += ' WHERE status = ?'
        args.append(status)
    sql += ' ORDER BY created_at DESC, id DESC LIMIT ?'
    args.append(ADMIN_LIST_LIMIT)
    rows = req.db.execute(sql, args).fetchall()
    return 200, {'cases': [_card(req.db, row) for row in rows]}


def admin_get(req):
    _require_admin(req)
    db = req.db
    case = _case_by_param(req)
    track = [_pick(row, POINT_FIELDS) for row in db.execute(
        'SELECT * FROM sos_locations WHERE case_id = ? ORDER BY at, rowid', (case['id'],))]
    return 200, {'case': _case_out(case), 'user': _user(db, case['user_id']),
                 'messages': _messages(db, case['id']), 'track': track}


def admin_post_message(req):
    admin = _require_admin(req)
    db = req.db
    case = _case_by_param(req)
    _require_open(case)
    text = _text(_body(req).get('text'))
    if not text:
        raise ApiError(400, 'text_required', 'لطفاً پیام خود را بنویسید.')
    now = utcnow()
    _release(db)
    with _lock:
        message = _add_message(db, case['id'], 'rescuer', text, None, now, admin_id=_admin_id(req, admin))
        db.execute('UPDATE sos_cases SET updated_at = ? WHERE id = ?', (now, case['id']))
        db.commit()
    _publish('sos_message', {'case_id': case['id'], 'message': message}, admins=True, **_owner_target(case))
    return 200, {'case': _case_out(_get_case(db, case['id'])), 'message': message}


def admin_close(req):
    _require_admin(req)
    db = req.db
    case = _case_by_param(req)
    if case['status'] != 'closed':
        now = utcnow()
        db.execute("UPDATE sos_cases SET status = 'closed', closed_at = ?, updated_at = ? WHERE id = ?",
                   (now, now, case['id']))
        db.commit()
        case = _get_case(db, case['id'])
        card = _card(db, case)
        card['is_new'] = False
        _publish('sos_case', card, admins=True, **_owner_target(case))
    return 200, {'case': _case_out(case)}


# ---------- internals ----------

def _release(db):
    """Commit this request's other writes (geo cache, auth session) before _lock; otherwise it would hold the database
    waiting for the lock while the lock owner waits for the database, and the SOS fails after 10 s (busy_timeout)."""
    if db.in_transaction:
        db.commit()


def _moved(case, lat, lon):
    """The point is farther than PLACE_REFRESH_KM from the previous one (or there is none)."""
    if case is None or case['lat'] is None or case['lon'] is None:
        return True
    return _distance_km(case['lat'], case['lon'], lat, lon) > PLACE_REFRESH_KM


# ---------- place label in the background ----------

_jobs = queue.Queue()
_idle = threading.Condition()
_pending = 0
_worker = None
_worker_lock = threading.Lock()


def _name_later(db, case_id, lat, lon):
    """Queue a place label for the background thread (its own connection to the same database)."""
    global _pending, _worker
    path = _db_file(db)
    if not path:  # ':memory:' cannot be opened by another thread: the fallback label stays
        return False
    with _idle:
        _pending += 1
    _jobs.put((path, case_id, lat, lon))
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name='panah-sos-place', daemon=True)
            _worker.start()
    return True


def wait_idle(timeout=10):
    """Wait until the background place labels are done → True; not done within timeout seconds → False."""
    with _idle:
        return _idle.wait_for(lambda: _pending == 0, timeout)


def _work():
    global _pending
    while True:
        job = _jobs.get()
        try:
            _name_case(*job)
        except Exception:
            log.exception('sos: background place labeling failed')
        finally:
            with _idle:
                _pending -= 1
                _idle.notify_all()


def _name_case(path, case_id, lat, lon):
    """geo.place_name (network) → if the case is still there and the label differs, update it and resend 'sos_case'."""
    if not Path(path).exists():  # the database is already deleted (the test ended): do not create a new one
        return
    from server import store
    db = store.connect(path)
    try:
        name = _place_name(db, lat, lon)
        case = _get_case(db, case_id)
        stale = (not name or case is None or case['lat'] is None
                 or _distance_km(case['lat'], case['lon'], lat, lon) > PLACE_REFRESH_KM
                 or case['place_name'] == name)
        if not stale:
            db.execute('UPDATE sos_cases SET place_name = ? WHERE id = ?', (name, case_id))
        db.commit()  # and the geo cache
        if stale:
            return
        case = _get_case(db, case_id)
        card = _card(db, case)
    finally:
        db.close()
    card['is_new'] = False
    card['changed'] = 'place_name'
    _publish('sos_case', card, admins=True, **_owner_target(case))


def _db_file(db):
    """Path of the connection's database file, or None (':memory:')."""
    try:
        for row in db.execute('PRAGMA database_list'):
            if row[1] == 'main':
                return row[2] or None
    except Exception:
        log.exception('sos: cannot find out the database file')
    return None


def _find_open(db, uid, dev):
    """The resident's open case; for a guest (or a still-guest case from this device), by device."""
    if uid is not None:
        row = db.execute("SELECT * FROM sos_cases WHERE status = 'open' AND user_id = ? ORDER BY id DESC LIMIT 1",
                         (uid,)).fetchone()
        if row is not None or not dev:
            return row
        return db.execute("SELECT * FROM sos_cases WHERE status = 'open' AND user_id IS NULL AND device_id = ?"
                          " ORDER BY id DESC LIMIT 1", (dev,)).fetchone()
    if dev:
        return db.execute("SELECT * FROM sos_cases WHERE status = 'open' AND device_id = ? ORDER BY id DESC LIMIT 1",
                          (dev,)).fetchone()
    return None


def _is_owner(req, case):
    uid, dev = _uid(req), _dev(req)
    if uid is not None and case['user_id'] == uid:
        return True
    return bool(dev) and case['device_id'] == dev and (case['user_id'] is None or uid is None)


def _owned_case(req, allow_admin=False):
    case = _case_by_param(req)
    if _is_owner(req, case):
        return case
    if allow_admin and getattr(req, 'admin', None):
        return case
    raise ApiError(403, 'sos_forbidden', 'شما به این درخواست کمک دسترسی ندارید.')


def _case_by_param(req):
    params = getattr(req, 'params', None) or {}
    try:
        case_id = int(params.get('id'))
    except (TypeError, ValueError):
        case_id = None
    case = _get_case(req.db, case_id) if case_id is not None else None
    if case is None:
        raise ApiError(404, 'sos_not_found', 'درخواست کمک پیدا نشد.')
    return case


def _require_open(case):
    if case['status'] != 'open':
        raise ApiError(409, 'sos_closed', 'این درخواست کمک بسته شده است.')


def _get_case(db, case_id):
    return db.execute('SELECT * FROM sos_cases WHERE id = ?', (case_id,)).fetchone()


def _add_message(db, case_id, sender, text, text_key, now, admin_id=None):
    cur = db.execute(
        'INSERT INTO sos_messages (case_id, sender, admin_id, text, text_key, created_at) VALUES (?, ?, ?, ?, ?, ?)',
        (case_id, sender, admin_id, text, text_key, now))
    return {'id': cur.lastrowid, 'case_id': case_id, 'sender': sender, 'admin_id': admin_id, 'text': text,
            'text_key': text_key, 'created_at': now}


def _auto_wait(db, case_id, now):
    """Auto-reply of figma 13: once per case and only while no rescuer has answered."""
    seen = db.execute(
        "SELECT 1 FROM sos_messages WHERE case_id = ? AND (sender = 'rescuer' OR text_key = 'sos_auto_wait') LIMIT 1",
        (case_id,)).fetchone()
    if seen:
        return None
    return _add_message(db, case_id, 'auto', AUTO_WAIT, 'sos_auto_wait', now)


def _add_point(db, case_id, lat, lon, accuracy, battery, signal, now):
    db.execute('INSERT INTO sos_locations (case_id, lat, lon, accuracy_m, battery, signal, at)'
               ' VALUES (?, ?, ?, ?, ?, ?, ?)', (case_id, lat, lon, accuracy, battery, signal, now))
    return {'lat': lat, 'lon': lon, 'accuracy_m': accuracy, 'battery': battery, 'signal': signal, 'at': now}


def _messages(db, case_id, after=0):
    rows = db.execute('SELECT * FROM sos_messages WHERE case_id = ? AND id > ? ORDER BY id', (case_id, after))
    return [_pick(row, MESSAGE_FIELDS) for row in rows]


def _card(db, case):
    """Card for the admin list (figma 18) and the 'sos_case' event."""
    last = db.execute("SELECT * FROM sos_messages WHERE case_id = ? AND sender = 'user' ORDER BY id DESC LIMIT 1",
                      (case['id'],)).fetchone()
    return {'case': _case_out(case), 'user': _user(db, case['user_id']),
            'last_message': _pick(last, MESSAGE_FIELDS) if last else None,
            'place_name': case['place_name'], 'kind': case['kind']}


def _user(db, user_id):
    """Resident info for the rescuer: {id, name, phone, blood_group, health, contacts}; guest → None."""
    if user_id is None:
        return None
    row = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    return {'id': data.get('id'), 'name': data.get('name') or '', 'phone': data.get('phone') or '',
            'blood_group': data.get('blood_group') or '',
            'health': _json_list(data.get('health')), 'contacts': _json_list(data.get('contacts'))}


def _json_list(value):
    try:
        from server import store
        value = store.loads(value, [])
    except ImportError:
        import json
        try:
            value = json.loads(value) if isinstance(value, str) else value
        except ValueError:
            value = []
    return value if isinstance(value, list) else []


def _case_out(case):
    return _pick(case, CASE_FIELDS) if case is not None else None


def _location_event(case, point):
    data = {'case_id': case['id']}
    data.update(point)
    data['place_name'] = case['place_name']
    return data


def _owner_target(case):
    """The owner's address for live: resident → user_id, guest → device_id."""
    if case['user_id'] is not None:
        return {'user_id': case['user_id']}
    if case['device_id']:
        return {'device_id': case['device_id']}
    return {}


def _pick(row, fields):
    return {name: row[name] for name in fields}


# ---------- request ----------

def _body(req):
    body = getattr(req, 'body', None)
    return body if isinstance(body, dict) else {}


def _query(req, key):
    value = (getattr(req, 'query', None) or {}).get(key)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    return value


def _uid(req):
    return _id_of(getattr(req, 'user', None))


def _admin_id(req, admin=None):
    found = _id_of(admin) if admin not in (None, True) else None
    return found if found is not None else _id_of(getattr(req, 'admin', None))


def _id_of(who):
    if not who or who is True:
        return None
    try:
        return who['id']
    except (KeyError, TypeError, IndexError):
        return getattr(who, 'id', None)


def _dev(req):
    dev = getattr(req, 'device_id', None)
    return str(dev) if dev else None


def _text(value):
    text = str(value).strip() if value is not None else ''
    if len(text) > MAX_TEXT:
        raise ApiError(400, 'text_too_long', 'پیام بسیار طولانی است.')
    return text


def _number(value, low, high):
    """Number in [low, high] or None (empty). Not a number / out of range → ValueError."""
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ValueError('bool')
    if isinstance(value, str):  # like geo.parse_point: Dari digits and the decimal sign '٫'
        value = value.strip().replace('٫', '.')
    number = float(value)
    if not math.isfinite(number) or number < low or number > high:
        raise ValueError('range')
    return number


def _latlon(body, strict):
    try:
        lat = _number(body.get('lat'), -90, 90)
        lon = _number(body.get('lon'), -180, 180)
        if (lat is None) != (lon is None):
            raise ValueError('pair')
    except (TypeError, ValueError):
        if strict:
            raise ApiError(400, 'bad_location', 'موقعیت نادرست است.')
        return None, None
    if strict and lat is None:
        raise ApiError(400, 'bad_location', 'موقعیت نادرست است.')
    return lat, lon


def _accuracy(body):
    value = body.get('accuracy', body.get('accuracy_m'))
    try:
        accuracy = _number(value, 0, 1e7)
    except (TypeError, ValueError):
        return None
    return round(accuracy, 1) if accuracy is not None else None


def _battery(value):
    try:
        battery = _number(value, 0, 100)
    except (TypeError, ValueError):
        return None
    return int(round(battery)) if battery is not None else None


def _signal(value):
    if value is None or value == '' or isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()[:20]
    return text or None


# ---------- other engines, lazily ----------

def _require_admin(req):
    try:
        from server import auth
        check = auth.require_admin
    except (ImportError, AttributeError):
        check = None
    if check is not None:
        return check(req)
    admin = getattr(req, 'admin', None)
    if not admin:  # like auth.require_admin: 403 for both a guest and a resident
        raise ApiError(403, 'admin_required', 'این بخش تنها برای مدیر است.')
    return admin


def _quick_place(db, lat, lon):
    """Label without network: the nearest known place ("نزدیک: …", geo.nearest_known) or None."""
    if lat is None or lon is None:
        return None
    try:
        from server import geo
        name = geo.nearest_known(db, lat, lon)
    except (ImportError, AttributeError):
        return None
    except Exception:
        log.exception('geo.nearest_known failed; the SOS place has no label for now')
        return None
    return str(name) if name else None


def _place_name(db, lat, lon):
    if lat is None or lon is None:
        return None
    try:
        from server import geo
        name = geo.place_name(db, lat, lon)
    except ImportError:
        log.info('no geo engine; the SOS place has no name')
        return None
    except Exception:
        log.exception('geo.place_name failed; the SOS place has no name')
        return None
    return str(name) if name else None


def _distance_km(lat1, lon1, lat2, lon2):
    try:
        from server import geo
        return float(geo.distance_km(lat1, lon1, lat2, lon2))
    except Exception:
        pass
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(h)))


def _publish(event, data, **target):
    try:
        from server import live
    except ImportError:
        log.info('no live engine; event %s not sent', event)
        return False
    try:
        live.publish(event, data, **target)
        return True
    except Exception:
        log.exception('live.publish(%s) failed', event)
        return False
