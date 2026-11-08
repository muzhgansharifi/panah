"""alerts — PANAH earthquake and flood alerts and resident notifications.

create(db, data, source, admin_id=None) → {alert, notified, sms, ms}
    data: type (earthquake|flood), lat, lon are required; place, starts_at (ISO 8601, no time zone means UTC;
    missing means now), radius_km (2.0), safe_place / safe_lat / safe_lon (the admin must give a place or a point),
    image (URL from POST /api/upload), magnitude, depth_km, source_id (USGS event id; no duplicates, 409).
    Order: the alert and notifications (one per resident; a resident counts by last_lat/lon, else home_lat/lon, within
    radius_km inclusive) → commit → live event 'alert' to all at once → background SMS (SmsQueue, its own connection).
    sms — how many SMS were queued; ms — time from writing the alert to the event.
Alert SMS (Dari and Pashto: type, place, where to go) are queued right in notifications.sms_status
    (none | queued | sending | sent | failed), so the queue survives a restart: what was not sent goes at the next
    start (start). Each SMS goes once: the row is first "claimed" (queued → sending, commit), then sms.send and
    the sent mark are one write together with the sms_outbox row. Error → more attempts after SMS_RETRY_S, then failed.
    On server shutdown the queue is drained (up to SMS_STOP_DRAIN_S); what did not make it is logged and stays
    queued. wait_sms(db, timeout) — wait until sent (tests, experiments).
add_past(db, data, source) → an alert straight into the past ones (status ended), no notifications: USGS history.
end(db, alert_id) → alert ended, commit, then the live event 'alert_end' to everyone; already ended → 409.
Live events go out only after commit, so on 'alert' / 'alert_end' the client already finds the alert in the database.
The event carries the public alert (id, type, place, lat, lon, radius_km, magnitude, starts_at, status, safe_place,
safe_lat, safe_lon …): the screen itself works out "I am inside the radius".
search(db, q, limit=20) → alerts by place and type (Dari, Pashto and English words).
residents_within(db, lat, lon, radius_km) → [(resident, km)], nearest first.
stats(db) → {users, active_alerts, alerts_today, notifications, sms_queue, sos, sms, usgs} (missing engine → None).

Routes:
    GET  /api/alerts?lat=&lon=[&sort=time]   → {active: [...], past: [... up to 50], point}
         each alert has distance_km and inside; without lat/lon, the logged-in resident's place; nearest first
    GET  /api/alerts/{id}?lat=&lon=          → {alert, safe_places: [≤ 3 nearest, outside the circle, ≤ 30 km], point}
    GET  /api/notifications                  → {notifications: [... with alert], unseen}   (resident)
    POST /api/notifications/{id}/seen        → {notification}                              (resident)
    POST /api/admin/alerts                   → 201 {alert, notified, sms, ms}              (admin, figma 15 fields)
    GET  /api/admin/alerts[?status=active]   → {alerts: [... with notified, seen, sms, sms_queued, sms_failed], counts}
    POST /api/admin/alerts/{id}/end          → {alert}
    GET  /api/admin/stats                    → stats(db)
"""
import atexit
import datetime
import importlib
import itertools
import logging
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from server import geo, store
from server.common import ApiError, parse_utc, to_utc, utcnow

log = logging.getLogger(__name__)

TYPES = ('earthquake', 'flood')
SOURCES = ('admin', 'usgs')
DEFAULT_RADIUS_KM = 2.0
MAX_RADIUS_KM = 1000.0
EDGE_KM = 1e-6          # 1 mm: a point exactly on the radius edge is inside, despite rounding
PAST_LIMIT = 50
ADMIN_LIMIT = 200
NOTIFICATION_LIMIT = 100
SEARCH_LIMIT = 20
SAFE_PLACES = 3
SAFE_CANDIDATES = 500        # nearest places asked from content before dropping those inside the alert circle
SAFE_MAX_KM = 30.0           # safe places farther than this from the reference point are not offered at all
KABUL = datetime.timezone(datetime.timedelta(hours=4, minutes=30))   # Afghanistan: UTC+4:30 all year round
USGS_EVENT_URL = 'https://earthquake.usgs.gov/earthquakes/eventpage/'
SMS_RETRY_S = (2, 10, 60)    # send error → new attempts after this many seconds, then failed
SMS_BATCH = 20               # how many SMS we "claim" at a time
SMS_CLAIM_S = 120            # a "claimed" row (sending) is held this long; after that it can be claimed again
SMS_STOP_DRAIN_S = 10        # on server shutdown we keep sending for at most this long

FIELDS = ('id', 'type', 'source', 'source_id', 'place', 'lat', 'lon', 'radius_km', 'magnitude', 'depth_km',
          'starts_at', 'safe_place', 'safe_lat', 'safe_lon', 'image', 'status', 'created_at', 'ended_at')

BRAND = 'پناه'                # the system's name (PANAH) at the start of every alert SMS
TITLE = {
    'earthquake': {'fa': 'هشدار زلزله', 'ps': 'د زلزلې خبرتیا'},
    'flood': {'fa': 'هشدار سیلاب', 'ps': 'د سېلاب خبرتیا'},
}
GO_SAFE = {'fa': 'به محل امن بروید: %s', 'ps': 'خوندي ځای ته لاړ شئ: %s'}
ADVICE = {
    'earthquake': {'fa': 'از ساختمان‌ها دور شوید و در جای باز بمانید.',
                   'ps': 'له ودانیو لرې شئ او په خلاص ځای کې پاتې شئ.'},
    'flood': {'fa': 'به جای بلند بروید.', 'ps': 'لوړ ځای ته لاړ شئ.'},
}

MSG = {
    'bad_body': 'اطلاعات رویداد درست نیست.',
    'bad_type': 'نوع رویداد را انتخاب کنید.',
    'bad_source': 'منبع رویداد درست نیست.',
    'location_required': 'موقعیت رویداد را روی نقشه مشخص کنید.',
    'bad_time': 'تاریخ و زمان را درست انتخاب کنید.',
    'bad_radius': 'شعاع هشدار درست نیست.',
    'bad_number': 'عدد درست نیست.',
    'safe_place_required': 'محل امن برای تخلیه را وارد کنید.',
    'duplicate': 'این رویداد قبلاً ثبت شده است.',
    'not_found': 'هشدار پیدا نشد.',
    'notification_not_found': 'اطلاعیه پیدا نشد.',
    'already_ended': 'این رویداد قبلاً پایان یافته است.',
    'login_required': 'لطفاً اول وارد شوید.',
    'admin_required': 'این بخش فقط برای مدیر است.',
}

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS alerts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        type        TEXT NOT NULL CHECK (type IN ('earthquake', 'flood')),
        source      TEXT NOT NULL CHECK (source IN ('admin', 'usgs')),
        source_id   TEXT UNIQUE,                     -- USGS event id; NULL for the admin's
        place       TEXT NOT NULL DEFAULT '',
        lat         REAL NOT NULL,
        lon         REAL NOT NULL,
        radius_km   REAL NOT NULL DEFAULT 2.0,
        magnitude   REAL,
        depth_km    REAL,
        starts_at   TEXT NOT NULL,                   -- UTC ISO 8601
        safe_place  TEXT,
        safe_lat    REAL,
        safe_lon    REAL,
        image       TEXT,
        status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended')),
        created_by  INTEGER,                         -- admin id
        created_at  TEXT NOT NULL,
        ended_at    TEXT
    )""",
    'CREATE INDEX IF NOT EXISTS alerts_status ON alerts (status, starts_at)',
    'CREATE INDEX IF NOT EXISTS alerts_source ON alerts (source, starts_at)',
    """CREATE TABLE IF NOT EXISTS notifications (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_id     INTEGER NOT NULL REFERENCES alerts (id) ON DELETE CASCADE,
        user_id      INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        distance_km  REAL,
        sms_id       INTEGER,                        -- sms_outbox row once the SMS is sent
        sms_status   TEXT NOT NULL DEFAULT 'none',   -- none | queued | sending | sent | failed
        sms_claim    TEXT,                           -- who is sending it now (sending)
        sms_next_at  TEXT,                           -- queued: not before; sending: claimed until
        sms_attempts INTEGER NOT NULL DEFAULT 0,
        sms_error    TEXT,
        sms_sent_at  TEXT,
        created_at   TEXT NOT NULL,
        seen_at      TEXT,
        UNIQUE (alert_id, user_id)
    )""",
    'CREATE INDEX IF NOT EXISTS notifications_user ON notifications (user_id, created_at)',
)
SMS_COLUMNS = (('sms_status', "TEXT NOT NULL DEFAULT 'none'"), ('sms_claim', 'TEXT'), ('sms_next_at', 'TEXT'),
               ('sms_attempts', 'INTEGER NOT NULL DEFAULT 0'), ('sms_error', 'TEXT'), ('sms_sent_at', 'TEXT'))


def init(db):
    """Tables alerts and notifications (calling again changes nothing; an old notifications table gets new columns)."""
    for statement in SCHEMA:
        db.execute(statement)
    have = {row[1] for row in db.execute('PRAGMA table_info(notifications)')}
    for name, declaration in SMS_COLUMNS:
        if name not in have:                     # database from an older version: SMS were sent right away there
            db.execute('ALTER TABLE notifications ADD COLUMN %s %s' % (name, declaration))
    if 'sms_status' not in have:
        db.execute("UPDATE notifications SET sms_status = 'sent' WHERE sms_id IS NOT NULL")
    db.execute('CREATE INDEX IF NOT EXISTS notifications_sms ON notifications (sms_status, sms_next_at)')


def start(app):
    """Background: send this database's alert SMS; at once sends what was left queued from the previous run."""
    SMS_QUEUE.attach(app)


def routes():
    return [
        ('GET', '/api/alerts', handle_list),
        ('GET', '/api/alerts/{id:int}', handle_get),
        ('GET', '/api/notifications', handle_notifications),
        ('POST', '/api/notifications/{id:int}/seen', handle_seen),
        ('POST', '/api/admin/alerts', handle_admin_create),
        ('GET', '/api/admin/alerts', handle_admin_list),
        ('POST', '/api/admin/alerts/{id:int}/end', handle_admin_end),
        ('GET', '/api/admin/stats', handle_admin_stats),
    ]


# ---------------------------------------------------------------- neighbours (lazy)

def _engine(name):
    """Neighbouring engine server.<name>, or None if it does not exist yet."""
    full = 'server.' + name
    try:
        return importlib.import_module(full)
    except ModuleNotFoundError as exc:
        if exc.name == full:
            log.debug('alerts: engine %s is not built yet', name)
            return None
        raise


def _call(name, func, *args):
    """server.<name>.<func>(*args) or None: the engine is missing or failed (logged)."""
    module = _engine(name)
    target = getattr(module, func, None) if module is not None else None
    if target is None:
        return None
    try:
        return target(*args)
    except Exception:
        log.exception('alerts: %s.%s failed', name, func)
        return None


def _publish(event, data):
    live = _engine('live')
    publish = getattr(live, 'publish', None) if live is not None else None
    if publish is None:
        log.info('alerts: live is not built yet, event %s not sent', event)
        return False
    try:
        publish(event, data, everyone=True)
        return True
    except Exception:
        log.exception('alerts: live.publish(%s) failed', event)
        return False


def _uid(who):
    """Resident or admin id from a dict / sqlite3.Row / number; otherwise None."""
    if who is None or isinstance(who, bool):
        return None
    if isinstance(who, int):
        return who
    try:
        value = who['id']
    except (KeyError, IndexError, TypeError):
        value = getattr(who, 'id', None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _require_user(req):
    method = getattr(req, 'require_user', None)
    if callable(method):
        return method()
    auth = _engine('auth')
    check = getattr(auth, 'require_user', None) if auth is not None else None
    if check is not None:
        return check(req) or req.user
    if not getattr(req, 'user', None):
        raise ApiError(401, 'login_required', MSG['login_required'])
    return req.user


def _require_admin(req):
    method = getattr(req, 'require_admin', None)
    if callable(method):
        return method()
    auth = _engine('auth')
    check = getattr(auth, 'require_admin', None) if auth is not None else None
    if check is not None:
        return check(req) or req.admin
    if not getattr(req, 'admin', None):
        if getattr(req, 'user', None):
            raise ApiError(403, 'admin_required', MSG['admin_required'])
        raise ApiError(401, 'login_required', MSG['login_required'])
    return req.admin


# ---------------------------------------------------------------- alert view

def public(row):
    """alerts row → dict for the resident and live events (without created_by)."""
    data = {key: row[key] for key in FIELDS}
    titles = TITLE.get(data['type'], {})
    data['title_fa'] = titles.get('fa', '')
    data['title_ps'] = titles.get('ps', '')
    data['url'] = USGS_EVENT_URL + data['source_id'] if data['source'] == 'usgs' and data['source_id'] else None
    return data


def _row(db, alert_id):
    try:
        alert_id = int(alert_id)
    except (TypeError, ValueError):
        return None
    return db.execute('SELECT * FROM alerts WHERE id = ?', (alert_id,)).fetchone()


def get(db, alert_id):
    """Alert by id → dict (as public) or None."""
    row = _row(db, alert_id)
    return public(row) if row is not None else None


def _with_distance(row, point):
    item = public(row)
    if point is None:
        item['distance_km'], item['inside'] = None, False
    else:
        dist = geo.distance_km(point[0], point[1], row['lat'], row['lon'])
        item['distance_km'] = round(dist, 3)
        item['inside'] = dist <= row['radius_km'] + EDGE_KM
    return item


def _by_distance(items):
    """Nearest first; without a distance, at the end; on a tie, the newest first."""
    items.sort(key=lambda a: a['starts_at'] or '', reverse=True)
    items.sort(key=lambda a: (a['distance_km'] is None, a['distance_km'] or 0.0))
    return items


# ---------------------------------------------------------------- data validation

def _text(value, limit):
    if value is None:
        return ''
    return ' '.join(str(value).split())[:limit]


def _number(value, default, low, high, code='bad_number'):
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        raise ApiError(400, code, MSG.get(code, MSG['bad_number']))
    try:
        number = float(str(value).strip().replace('٫', '.')) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        raise ApiError(400, code, MSG.get(code, MSG['bad_number'])) from None
    if not math.isfinite(number) or not low <= number <= high:
        raise ApiError(400, code, MSG.get(code, MSG['bad_number']))
    return number


def _time(value):
    """ISO 8601 (with Z, a time zone, or none → UTC) or Unix seconds → '…Z'; empty → now."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return utcnow()
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return to_utc(value)
        return to_utc(parse_utc(value))
    except (TypeError, ValueError, OverflowError, OSError):
        raise ApiError(400, 'bad_time', MSG['bad_time']) from None


def _kind(value):
    word = normalize(value)
    if word in TYPES:
        return word
    found = _type_words(word.split())[0]
    if len(found) == 1:
        return found.pop()
    raise ApiError(400, 'bad_type', MSG['bad_type'])


def _coords_text(lat, lon):
    return '%.4f, %.4f' % (lat, lon)


def _clean(db, data, source):
    """Form / feed data → a row for alerts (without id). Error → ApiError 400 / 409."""
    if not isinstance(data, dict):
        raise ApiError(400, 'bad_body', MSG['bad_body'])
    if source not in SOURCES:
        raise ApiError(400, 'bad_source', MSG['bad_source'])
    kind = _kind(data.get('type'))
    try:
        lat, lon = geo.parse_point(data.get('lat'), data.get('lon'))
    except ApiError as exc:
        raise ApiError(400, exc.code, MSG['location_required'] if exc.code == 'location_required'
                       else exc.message_fa) from None
    safe = geo.parse_point(data.get('safe_lat'), data.get('safe_lon'), required=False)
    row = {
        'type': kind,
        'source': source,
        'source_id': _text(data.get('source_id'), 100) or None,
        'place': _text(data.get('place'), 200),
        'lat': lat,
        'lon': lon,
        'radius_km': _number(data.get('radius_km'), DEFAULT_RADIUS_KM, 0.001, MAX_RADIUS_KM, 'bad_radius'),
        'magnitude': _number(data.get('magnitude'), None, -2.0, 12.0),
        'depth_km': _number(data.get('depth_km'), None, -10.0, 1000.0),
        'starts_at': _time(data.get('starts_at')),
        'safe_place': _text(data.get('safe_place'), 200),
        'safe_lat': safe[0] if safe else None,
        'safe_lon': safe[1] if safe else None,
        'image': _text(data.get('image'), 500) or None,
    }
    if source == 'admin' and not row['safe_place'] and safe is None:
        raise ApiError(400, 'safe_place_required', MSG['safe_place_required'])
    if row['source_id'] and db.execute('SELECT 1 FROM alerts WHERE source_id = ?', (row['source_id'],)).fetchone():
        raise ApiError(409, 'duplicate', MSG['duplicate'])
    # Network (Nominatim) before the first database write.
    if not row['place']:
        row['place'] = geo.place_name(db, lat, lon) or _coords_text(lat, lon)
    if not row['safe_place'] and safe is not None:
        row['safe_place'] = geo.place_name(db, safe[0], safe[1]) or _coords_text(*safe)
    return row


def _insert(db, row, status, admin_id=None):
    now = utcnow()
    values = dict(row, status=status, created_by=admin_id, created_at=now,
                  ended_at=now if status == 'ended' else None)
    columns = [key for key in FIELDS if key != 'id'] + ['created_by']
    try:
        cur = db.execute('INSERT INTO alerts (%s) VALUES (%s)' % (', '.join(columns), ', '.join('?' * len(columns))),
                         [values[key] for key in columns])
    except sqlite3.IntegrityError as exc:
        if 'source_id' in str(exc):
            raise ApiError(409, 'duplicate', MSG['duplicate']) from None
        raise
    return _row(db, cur.lastrowid)


# ---------------------------------------------------------------- distribution

def residents_within(db, lat, lon, radius_km):
    """Residents whose place (last_lat/lon, else home_lat/lon) is within radius_km → [(dict, km)], nearest first."""
    # Box around the circle (spherical cap): latitude ± δ, longitude ± asin(sin δ / cos φ); margin 1e-4° ≈ 11 m.
    delta = radius_km / geo.EARTH_RADIUS_KM
    phi = math.radians(lat)
    d_lat = math.degrees(delta) + 1e-4
    ratio = math.sin(delta) / math.cos(phi) if math.cos(phi) > 1e-9 else 2.0
    near_pole = abs(phi) + delta >= math.pi / 2
    d_lon = 360.0 if near_pole or ratio >= 1 else math.degrees(math.asin(ratio)) + 1e-4
    sql = """SELECT * FROM (
                 SELECT id, phone, name,
                        CASE WHEN last_lat IS NOT NULL AND last_lon IS NOT NULL THEN last_lat ELSE home_lat END AS lat,
                        CASE WHEN last_lat IS NOT NULL AND last_lon IS NOT NULL THEN last_lon ELSE home_lon END AS lon
                 FROM users)
             WHERE lat IS NOT NULL AND lon IS NOT NULL AND lat BETWEEN ? AND ?"""
    args = [lat - d_lat, lat + d_lat]
    if lon - d_lon >= -180 and lon + d_lon <= 180:   # the box does not cross the 180th meridian
        sql += ' AND lon BETWEEN ? AND ?'
        args += [lon - d_lon, lon + d_lon]
    found = []
    for row in db.execute(sql, args):
        dist = geo.distance_km(lat, lon, row['lat'], row['lon'])
        if dist <= radius_km + EDGE_KM:
            found.append((dict(row), dist))
    found.sort(key=lambda pair: pair[1])
    return found


def sms_text(alert):
    """Alert SMS text: a line in Dari and a line in Pashto — type, place, where to go."""
    kind = alert['type']
    mag = ' M%.1f' % alert['magnitude'] if alert['magnitude'] is not None else ''
    place = alert['place'] or _coords_text(alert['lat'], alert['lon'])
    lines = []
    for lang in ('fa', 'ps'):
        where = GO_SAFE[lang] % alert['safe_place'] if alert['safe_place'] else ADVICE[kind][lang]
        lines.append('%s — %s%s: %s. %s' % (BRAND, TITLE[kind][lang], mag, place, where))
    return '\n'.join(lines)


def _sms_send():
    sms = _engine('sms')
    return getattr(sms, 'send', None) if sms is not None else None


def _distribute(db, alert, sms_on=True):
    """Notifications to residents in the radius (no duplicates), SMS into the queue → (notified, queued). No commit."""
    notified = queued = 0
    now = utcnow()
    for user, dist in residents_within(db, alert['lat'], alert['lon'], alert['radius_km']):
        sms_status = 'queued' if sms_on and user['phone'] else 'none'
        cur = db.execute('INSERT OR IGNORE INTO notifications (alert_id, user_id, distance_km, created_at, sms_status) '
                         'VALUES (?, ?, ?, ?, ?)', (alert['id'], user['id'], round(dist, 3), now, sms_status))
        if cur.rowcount != 1:
            continue                         # this resident was already notified about this alert
        notified += 1
        queued += sms_status == 'queued'
    return notified, queued


def create(db, data, source, admin_id=None):
    """New active alert → {alert, notified, sms, ms}: write → commit → event 'alert' → SMS in the background."""
    row = _clean(db, data, source)            # validation and (if needed) the place name, before the write
    started = time.perf_counter()
    alert = _insert(db, row, 'active', admin_id)
    sms_on = _sms_send() is not None
    if not sms_on:
        log.info('alerts: sms is not built yet, alert %s goes without SMS', alert['id'])
    notified, queued = _distribute(db, alert, sms_on)
    db.commit()                      # write first, then the event: on 'alert' the client finds the alert already stored
    _publish('alert', public(alert))
    ms = round((time.perf_counter() - started) * 1000, 1)
    if queued:
        SMS_QUEUE.kick(db)           # SMS after the event, in its own thread: alerts never wait for the SMS provider
    log.info('alert %s (%s, %s): %d notified, %d SMS queued, event after %.1f ms', alert['id'], alert['type'],
             source, notified, queued, ms)
    return {'alert': public(alert), 'notified': notified, 'sms': queued, 'ms': ms}


def add_past(db, data, source):
    """An alert straight into the past ones (status ended), no notifications → dict (USGS history)."""
    return public(_insert(db, _clean(db, data, source), 'ended'))


def end(db, alert_id, admin_id=None):
    """End an alert → dict; live event 'alert_end' to everyone. Missing → 404, already ended → 409."""
    row = _row(db, alert_id)
    if row is None:
        raise ApiError(404, 'not_found', MSG['not_found'])
    if row['status'] == 'ended':
        raise ApiError(409, 'already_ended', MSG['already_ended'])
    db.execute("UPDATE alerts SET status = 'ended', ended_at = ? WHERE id = ? AND status = 'active'",
               (utcnow(), row['id']))
    alert = public(_row(db, row['id']))
    db.commit()                      # write first, then the 'alert_end' event
    _publish('alert_end', alert)
    log.info('alert %s ended (admin %s)', alert['id'], admin_id)
    return alert


# ---------------------------------------------------------------- alert SMS in the background

def _db_file(db):
    """Database file of the connection, or None (':memory:')."""
    for row in db.execute('PRAGMA database_list'):
        if row[1] == 'main':
            return row[2] or None
    return None


def _path_key(path):
    return os.path.normcase(str(Path(path).resolve()))


def _stopping(app):
    event = getattr(app, 'stopping', None) if app is not None else None
    return bool(event is not None and event.is_set())


class SmsQueue:
    """Background sending of alert SMS: one daemon thread per process, its own connection for each database.

    The queue is the notifications rows (sms_status): it survives a restart and is not lost. The thread sleeps until
    woken up (kick) or until a retry is due; for an attached server (attach) it checks app.stopping once a second,
    and then drains this database's queue (up to SMS_STOP_DRAIN_S) and honestly logs how many it did not
    manage to send (they stay queued and go out at the next start).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._jobs = {}       # path key → {'path', 'app', 'gen', 'kicked', 'due'}
        self._thread = None
        self._closing = False
        self._busy = None     # key of the database the thread is working on now
        self._claims = itertools.count(1)
        self.token = secrets.token_hex(6)

    # --- outside API
    def kick(self, db):
        """New SMS are queued in database db: wake up the sender. ':memory:' — send right here."""
        path = _db_file(db)
        if path is None:      # another connection cannot see ':memory:': send here (the event is already out)
            self.process(db)
            return
        self._add(path, None)

    def attach(self, app):
        """Server app has started: send its database's SMS; on app.stopping, drain the queue and let go."""
        self._add(app.db_path, app)

    def idle(self, path):
        """The thread is not working on database path now and is not about to (no pending kick)."""
        key = _path_key(path)
        with self._lock:
            job = self._jobs.get(key)
            return self._busy != key and not (job and job['kicked'])

    def shutdown(self, timeout=SMS_STOP_DRAIN_S + 1):
        """Program exit: send everything left (for at most timeout) and stop the thread."""
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive() or not self._jobs:
                return
            self._closing = True
        self._wake.set()
        thread.join(timeout)
        if thread.is_alive():
            log.warning('alerts: SMS sending did not finish before exit; the rest stays queued for the next start')

    # --- thread
    def _add(self, path, app):
        key = _path_key(path)
        with self._lock:
            job = self._jobs.setdefault(key, {'path': path, 'app': None, 'gen': 0, 'kicked': False, 'due': None})
            if app is not None:
                job['app'] = app
            job['gen'] += 1
            job['kicked'] = True
            if self._thread is None or not self._thread.is_alive():
                self._closing = False
                self._thread = threading.Thread(target=self._run, name='panah-alert-sms', daemon=True)
                self._thread.start()
        self._wake.set()

    def _timeout(self):
        with self._lock:
            watch = self._closing or any(job['app'] is not None for job in self._jobs.values())
            dues = [job['due'] for job in self._jobs.values() if job['due'] is not None]
        timeout = 1.0 if watch else None
        if dues:
            left = max(0.0, min(dues) - time.monotonic())
            timeout = left if timeout is None else min(timeout, left)
        return timeout

    def _run(self):
        while True:
            try:
                self._wake.wait(self._timeout())
                self._wake.clear()
                if self._run_once():
                    return
            except Exception:
                log.exception('alerts: SMS queue loop failed')
                time.sleep(1)

    def _run_once(self):
        """One pass over the databases → True if it is time to end the thread (program exit)."""
        with self._lock:
            jobs = [(key, dict(job)) for key, job in self._jobs.items()]
            closing = self._closing
        now = time.monotonic()
        for key, job in jobs:
            stopping = closing or _stopping(job['app'])
            if not (job['kicked'] or stopping or (job['due'] is not None and job['due'] <= now)):
                continue
            with self._lock:
                if key in self._jobs:
                    self._jobs[key]['kicked'] = False
                self._busy = key
            deadline = time.monotonic() + SMS_STOP_DRAIN_S if stopping else None
            app = job['app']
            try:
                sent, left, due = self._pass(job['path'], deadline,
                                             lambda: closing or self._closing or _stopping(app))
            finally:
                with self._lock:
                    self._busy = None
            stopping = stopping or self._closing or _stopping(app)     # stopped in the middle of sending
            if stopping:
                if left:
                    log.warning('alerts: server stopping — %d alert SMS not sent in %g s (sent %d); they stay queued '
                                'and will be sent at the next start (%s)', left, SMS_STOP_DRAIN_S, sent, job['path'])
                else:
                    log.info('alerts: server stopping — alert SMS queue is empty (sent %d)', sent)
            with self._lock:
                current = self._jobs.get(key)
                if current is None:
                    continue
                current['due'] = due
                if stopping:
                    del self._jobs[key]
                elif not left and current['app'] is None and current['gen'] == job['gen']:
                    del self._jobs[key]                     # no work: forget the database until the next kick
        return closing

    def _pass(self, path, deadline=None, stop_check=None):
        if not Path(path).exists():                 # database deleted (temporary test folder): no work
            return 0, 0, None
        try:
            db = store.connect(path)
        except Exception:
            log.exception('alerts: SMS queue cannot open %s', path)
            return 0, 1, time.monotonic() + 5
        try:
            return self.process(db, deadline, stop_check)
        except Exception:
            if not Path(path).exists():              # database deleted in the middle of sending: no more work
                log.debug('alerts: database %s disappeared during SMS sending', path)
                return 0, 0, None
            log.exception('alerts: SMS queue pass failed (%s)', path)
            return 0, 1, time.monotonic() + 5
        finally:
            db.close()

    # --- sending
    def process(self, db, deadline=None, stop_check=None):
        """Send everything that is due in database db → (sent, left in the queue, when to look again).

        deadline — time.monotonic() after which we do not send (shutdown); stop_check() → True means the server
        is being stopped in the middle of sending: from that moment we have SMS_STOP_DRAIN_S.
        """
        send = _sms_send()
        if send is None:
            sent, (left, _) = 0, self._left(db)
            return sent, left, (time.monotonic() + 30 if left else None)
        stale = db.execute("UPDATE notifications SET sms_status = 'queued', sms_claim = NULL, sms_next_at = NULL "
                           "WHERE sms_status = 'sending' AND sms_next_at < ?", (utcnow(),))
        db.commit()
        if stale.rowcount:
            log.warning('alerts: %d alert SMS were taken by a stopped sender; queued again', stale.rowcount)
        texts, sent = {}, 0
        while deadline is None or time.monotonic() < deadline:
            batch = self._claim(db)
            if not batch:
                break
            for number, item in enumerate(batch):
                if deadline is None and stop_check is not None and stop_check():
                    deadline = time.monotonic() + SMS_STOP_DRAIN_S
                if deadline is not None and time.monotonic() >= deadline:
                    self._release(db, [row['id'] for row in batch[number:]], item['sms_claim'])
                    break
                sent += self._send_one(db, send, item, texts)
        left, next_at = self._left(db)
        due = None
        if left:
            wait = 1.0
            if next_at:
                wait = max(0.05, (parse_utc(next_at) - datetime.datetime.now(datetime.timezone.utc)).total_seconds())
            due = time.monotonic() + wait
        return sent, left, due

    def _claim(self, db):
        token = '%s-%d' % (self.token, next(self._claims))
        cur = db.execute("UPDATE notifications SET sms_status = 'sending', sms_claim = ?, sms_next_at = ? "
                         "WHERE id IN (SELECT id FROM notifications WHERE sms_status = 'queued' "
                         "AND (sms_next_at IS NULL OR sms_next_at <= ?) ORDER BY id LIMIT ?)",
                         (token, utcnow(SMS_CLAIM_S), utcnow(), SMS_BATCH))
        db.commit()
        if not cur.rowcount:
            return []
        return db.execute('SELECT n.id, n.alert_id, n.sms_attempts, n.sms_claim, u.phone FROM notifications n '
                          'JOIN users u ON u.id = n.user_id WHERE n.sms_claim = ? ORDER BY n.id', (token,)).fetchall()

    def _send_one(self, db, send, item, texts):
        if item['alert_id'] not in texts:
            texts[item['alert_id']] = sms_text(_row(db, item['alert_id']))
        try:
            sms_id = send(db, item['phone'], texts[item['alert_id']], 'alert')   # sms_outbox row (no commit)
            if isinstance(sms_id, dict):
                sms_id = sms_id.get('id')
            if not isinstance(sms_id, (int, str)) or isinstance(sms_id, bool):
                sms_id = None
            cur = db.execute("UPDATE notifications SET sms_status = 'sent', sms_id = ?, sms_sent_at = ?, "
                             "sms_claim = NULL, sms_next_at = NULL, sms_error = NULL WHERE id = ? AND sms_claim = ?",
                             (sms_id, utcnow(), item['id'], item['sms_claim']))
            if cur.rowcount != 1:            # the claim expired and someone else took the row: do not record ours
                db.rollback()
                return 0
            db.commit()                      # the SMS and the "sent" mark in one write
            return 1
        except Exception as exc:
            db.rollback()
            self._failed(db, item, exc)
            return 0

    def _failed(self, db, item, exc):
        attempts = item['sms_attempts'] + 1
        retry = attempts <= len(SMS_RETRY_S)
        error = ('%s: %s' % (type(exc).__name__, exc))[:300]
        db.execute('UPDATE notifications SET sms_status = ?, sms_attempts = ?, sms_error = ?, sms_next_at = ?, '
                   'sms_claim = NULL WHERE id = ? AND sms_claim = ?',
                   ('queued' if retry else 'failed', attempts, error,
                    utcnow(SMS_RETRY_S[attempts - 1]) if retry else None, item['id'], item['sms_claim']))
        db.commit()
        if retry:
            log.warning('alerts: SMS for notification %s failed (%s), attempt %d; retry in %s s', item['id'], error,
                        attempts, SMS_RETRY_S[attempts - 1])
        else:
            log.error('alerts: SMS for notification %s failed %d times (%s); marked failed', item['id'], attempts,
                      error)

    def _release(self, db, ids, token):
        db.executemany("UPDATE notifications SET sms_status = 'queued', sms_claim = NULL, sms_next_at = NULL "
                       "WHERE id = ? AND sms_claim = ?", [(nid, token) for nid in ids])
        db.commit()

    @staticmethod
    def _left(db):
        row = db.execute("SELECT count(*), min(CASE WHEN sms_status = 'queued' THEN sms_next_at END) "
                         "FROM notifications WHERE sms_status IN ('queued', 'sending')").fetchone()
        return row[0], row[1]


SMS_QUEUE = SmsQueue()
atexit.register(SMS_QUEUE.shutdown)


def wait_sms(db, timeout=5.0):
    """Wait for the background sending of database db's alert SMS → True (queue empty, thread not on this database)
    or False (not done within timeout). An open write in db would block the database, so commit first."""
    if db.in_transaction:
        raise RuntimeError('alerts.wait_sms: commit first — an open write transaction blocks the SMS sender')
    deadline = time.monotonic() + timeout
    path = _db_file(db)
    kicked = False
    while True:
        left = db.execute("SELECT count(*) FROM notifications WHERE sms_status IN ('queued', 'sending')").fetchone()[0]
        if not left and (path is None or SMS_QUEUE.idle(path)):
            return True
        if left and not kicked:
            SMS_QUEUE.kick(db)
            kicked = True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)


# ---------------------------------------------------------------- search

_SPACE = re.compile(r'[\s،؛؟,.;:!?()\[\]{}"\'«»<>\-_/\\|+*=#&%$@^~`]+')
_FOLD = str.maketrans({
    'ك': 'ک', 'ي': 'ی', 'ى': 'ی', 'ې': 'ی', 'ۍ': 'ی', 'ئ': 'ی', 'ۀ': 'ه', 'ة': 'ه', 'أ': 'ا', 'إ': 'ا', 'آ': 'ا',
    '‌': '', '‍': '', '‎': '', '‏': '', 'ـ': '',   # ZWNJ, ZWJ, LRM, RLM, kashida
    '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4', '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
    '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4', '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9',
})
_MARKS = re.compile('[ً-ٰٟ]')   # vowel marks


def normalize(text):
    """For search: Dari and Pashto letters folded to one form, no ZWNJ or vowel marks, Latin digits, lower case."""
    text = _MARKS.sub('', str(text or '').translate(_FOLD)).lower()
    return ' '.join(_SPACE.sub(' ', text).split())


TYPE_WORDS = {
    'flood': ['سیلاب', 'سیل', 'سېلاب', 'flood', 'floods', 'flooding'],
    'earthquake': ['زلزله', 'زلزلې', 'زمین‌لرزه', 'زمین لرزه', 'earthquake', 'quake', 'seismic'],
}
_TYPE_WORDS = {kind: [normalize(word) for word in words] for kind, words in TYPE_WORDS.items()}
_STOP = {normalize(word) for word in (
    'هشدار', 'هشدارها', 'خبرتیا', 'خبرتیاوې', 'رویداد', 'alert', 'alerts', 'warning', 'در', 'د', 'و', 'به', 'از',
    'کې', 'ته', 'of', 'in', 'the', 'near', 'نزدیک', 'نږدې')}


def _type_words(tokens):
    """Query words → (types, other words). A type is a word starting with a known one (سیلابها, floods)."""
    text = ' %s ' % ' '.join(tokens)
    kinds = set()
    for kind, words in _TYPE_WORDS.items():
        for word in words:
            if ' ' in word and ' %s ' % word in text:
                kinds.add(kind)
                text = text.replace(' %s ' % word, ' ')
    rest = []
    for token in text.split():
        hit = [kind for kind, words in _TYPE_WORDS.items() if any(token.startswith(w) for w in words if ' ' not in w)]
        if hit:
            kinds.update(hit)
        elif token not in _STOP:
            rest.append(token)
    return kinds, rest


def search(db, q, limit=SEARCH_LIMIT):
    """Alerts by place and type → [dict as public], active ones first, then the newest.

    'سیلاب' / 'سېلاب' / 'flood' mean floods, 'زلزله' / 'earthquake' mean earthquakes,
    other words are searched in the place and the safe place ('کابل', 'Jurm'): 'سیلاب کابل' means floods in Kabul.
    """
    kinds, rest = _type_words(normalize(q).split())
    if not kinds and not rest:
        return []
    sql = 'SELECT * FROM alerts'
    args = []
    if kinds:
        sql += ' WHERE type IN (%s)' % ', '.join('?' * len(kinds))
        args += sorted(kinds)
    sql += " ORDER BY status = 'active' DESC, starts_at DESC, id DESC LIMIT 2000"
    try:
        rows = db.execute(sql, args).fetchall()
    except sqlite3.OperationalError as exc:      # alerts.init not called yet: no alerts
        if 'no such table' in str(exc):
            return []
        raise
    found = []
    for row in rows:
        if rest:
            hay = normalize('%s %s' % (row['place'] or '', row['safe_place'] or ''))
            if not all(token in hay for token in rest):
                continue
        found.append(public(row))
        if len(found) >= limit:
            break
    return found


# ---------------------------------------------------------------- statistics

def _kabul_day(now=None):
    """Start and end of today in Kabul → two UTC strings '…Z'."""
    local = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(KABUL)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return to_utc(start), to_utc(start + datetime.timedelta(days=1))


def stats(db, now=None):
    """Numbers for the admin panel; a missing or failed neighbour → None."""
    day_start, day_end = _kabul_day(now)
    one = lambda sql, *args: db.execute(sql, args).fetchone()[0]  # noqa: E731
    return {
        'users': one('SELECT count(*) FROM users'),
        'active_alerts': one("SELECT count(*) FROM alerts WHERE status = 'active'"),
        'alerts_today': one('SELECT count(*) FROM alerts WHERE starts_at >= ? AND starts_at < ?', day_start, day_end),
        'notifications': one('SELECT count(*) FROM notifications'),
        'sms_queue': {status: one('SELECT count(*) FROM notifications WHERE sms_status = ?', status)
                      for status in ('queued', 'sending', 'sent', 'failed')},
        'sos': _call('sos', 'stats', db),
        'sms': _call('sms', 'stats', db),
        'usgs': _call('usgs', 'status', db),
    }


# ---------------------------------------------------------------- handlers

def _query(req, key):
    value = (getattr(req, 'query', None) or {}).get(key)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    return value


def _viewer_point(req):
    """Distance origin: the request's lat/lon, else the logged-in resident's place (last, else home), else None."""
    point = geo.parse_point(_query(req, 'lat'), _query(req, 'lon'), required=False)
    if point is not None:
        return point
    uid = _uid(getattr(req, 'user', None))
    user = store.get_user(req.db, uid) if uid is not None else None
    if user:
        for prefix in ('last', 'home'):
            lat, lon = user.get(prefix + '_lat'), user.get(prefix + '_lon')
            if lat is not None and lon is not None:
                return float(lat), float(lon)
    return None


def _point_out(point):
    return {'lat': point[0], 'lon': point[1]} if point else None


def handle_list(req):
    point = _viewer_point(req)
    db = req.db
    active = [_with_distance(row, point) for row in
              db.execute("SELECT * FROM alerts WHERE status = 'active' ORDER BY starts_at DESC, id DESC")]
    past = [_with_distance(row, point) for row in
            db.execute("SELECT * FROM alerts WHERE status = 'ended' ORDER BY starts_at DESC, id DESC LIMIT ?",
                       (PAST_LIMIT,))]
    if str(_query(req, 'sort') or '').lower() != 'time':
        _by_distance(active)
        _by_distance(past)
    return 200, {'active': active, 'past': past, 'point': _point_out(point)}


def _safe_places(db, alert, point, count=SAFE_PLACES):
    """Up to `count` safe places OUTSIDE the alert circle and within SAFE_MAX_KM of `point`, nearest first.

    A place at distance <= radius_km from the alert centre is in the danger zone, so it is never offered for
    evacuation; a place farther than SAFE_MAX_KM from `point` (the resident, else the alert centre) is no use in an
    emergency, so fewer than `count` places (or none) is the honest answer. Places without coordinates are skipped
    (we cannot tell where they are). The admin's own safe_place / safe_lat / safe_lon belong to the alert itself and
    are not filtered here.
    """
    places = _call('content', 'nearest_places', db, point[0], point[1], SAFE_CANDIDATES) or []
    out = []
    for place in places:
        item = dict(place)
        if item.get('lat') is None or item.get('lon') is None:
            continue
        from_alert = geo.distance_km(alert['lat'], alert['lon'], item['lat'], item['lon'])
        if from_alert <= alert['radius_km'] + EDGE_KM:      # inside the alert circle: danger zone, skip
            continue
        if item.get('distance_km') is None:
            item['distance_km'] = round(geo.distance_km(point[0], point[1], item['lat'], item['lon']), 3)
        if item['distance_km'] > SAFE_MAX_KM:               # too far to be of use: skip, do not top up to `count`
            continue
        out.append(item)
    out.sort(key=lambda item: item['distance_km'])          # content sorts already; keep the order explicit
    return out[:count]


def handle_get(req):
    row = _row(req.db, (req.params or {}).get('id'))
    if row is None:
        raise ApiError(404, 'not_found', MSG['not_found'])
    point = _viewer_point(req)
    near = point or (row['lat'], row['lon'])
    return 200, {'alert': _with_distance(row, point), 'safe_places': _safe_places(req.db, row, near),
                 'point': _point_out(point)}


def _notification_out(row, alert_row):
    data = {key: row[key] for key in ('id', 'alert_id', 'distance_km', 'sms_id', 'sms_status', 'created_at',
                                      'seen_at')}
    data['alert'] = public(alert_row) if alert_row is not None else None
    return data


def handle_notifications(req):
    uid = _uid(_require_user(req))
    db = req.db
    rows = db.execute('SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT ?',
                      (uid, NOTIFICATION_LIMIT)).fetchall()
    alerts = {}
    for row in rows:
        if row['alert_id'] not in alerts:
            alerts[row['alert_id']] = _row(db, row['alert_id'])
    unseen = db.execute('SELECT count(*) FROM notifications WHERE user_id = ? AND seen_at IS NULL',
                        (uid,)).fetchone()[0]
    return 200, {'notifications': [_notification_out(row, alerts[row['alert_id']]) for row in rows],
                 'unseen': unseen}


def handle_seen(req):
    uid = _uid(_require_user(req))
    db = req.db
    try:
        nid = int((req.params or {}).get('id'))
    except (TypeError, ValueError):
        nid = None
    row = db.execute('SELECT * FROM notifications WHERE id = ? AND user_id = ?', (nid, uid)).fetchone()
    if row is None:
        raise ApiError(404, 'not_found', MSG['notification_not_found'])
    if row['seen_at'] is None:
        db.execute('UPDATE notifications SET seen_at = ? WHERE id = ?', (utcnow(), row['id']))
        row = db.execute('SELECT * FROM notifications WHERE id = ?', (row['id'],)).fetchone()
    return 200, {'notification': _notification_out(row, _row(db, row['alert_id']))}


def handle_admin_create(req):
    admin = _require_admin(req)
    return 201, create(req.db, req.body or {}, 'admin', admin_id=_uid(admin))


def handle_admin_list(req):
    _require_admin(req)
    status = str(_query(req, 'status') or '').strip().lower()
    sql = """SELECT a.*,
                    (SELECT count(*) FROM notifications n WHERE n.alert_id = a.id) AS notified,
                    (SELECT count(*) FROM notifications n WHERE n.alert_id = a.id AND n.seen_at IS NOT NULL) AS seen,
                    (SELECT count(*) FROM notifications n WHERE n.alert_id = a.id AND n.sms_status = 'sent') AS sms,
                    (SELECT count(*) FROM notifications n
                     WHERE n.alert_id = a.id AND n.sms_status IN ('queued', 'sending')) AS sms_queued,
                    (SELECT count(*) FROM notifications n WHERE n.alert_id = a.id AND n.sms_status = 'failed')
                        AS sms_failed
             FROM alerts a"""
    args = []
    if status in ('active', 'ended'):
        sql += ' WHERE a.status = ?'
        args.append(status)
    sql += " ORDER BY a.status = 'active' DESC, a.starts_at DESC, a.id DESC LIMIT ?"
    args.append(ADMIN_LIMIT)
    items = []
    for row in req.db.execute(sql, args):
        item = public(row)
        item.update(created_by=row['created_by'], notified=row['notified'], seen=row['seen'], sms=row['sms'],
                    sms_queued=row['sms_queued'], sms_failed=row['sms_failed'])
        items.append(item)
    counts = {status_name: req.db.execute('SELECT count(*) FROM alerts WHERE status = ?', (status_name,)).fetchone()[0]
              for status_name in ('active', 'ended')}
    return 200, {'alerts': items, 'counts': counts}


def handle_admin_end(req):
    admin = _require_admin(req)
    return 200, {'alert': end(req.db, (req.params or {}).get('id'), admin_id=_uid(admin))}


def handle_admin_stats(req):
    _require_admin(req)
    return 200, stats(req.db)
