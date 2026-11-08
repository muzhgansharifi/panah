"""sms — PANAH (پناه) SMS sending: test mode (the default) and a log of everything sent.

Contract:
  send(db, phone, text, kind) → sms_outbox row id; kind: otp | alert | sos | other.
    Test mode: no real SMS goes out — a row in sms_outbox (status='test') and a line in the log
    sms.log (UTF-8) in app.config['data_dir'] (remembered by start(app)), else next to the database file, data/.
    The caller commits (in a request, web does).
    Bad kind, empty number or text → ValueError (a bug in the engine code, not in the resident's input).
    With a real provider: sent → a row with status='sent'; the provider fails → SmsError, and nothing is recorded
    as sent (no sms_outbox row, only a 'failed' line in sms.log). The caller decides what to do: the alerts queue
    retries after 2/10/60 s and then marks the notification failed; a login code request answers 503.
  stats(db) → {total, today, by_kind}: total, today (since midnight Kabul time, UTC+4:30), by kind.
  GET /api/admin/sms?limit=50&kind=otp (admin only) →
    {items: [{id, phone, text, kind, status, created_at}] newest first, total, today, by_kind, test_mode}.

A real provider is "future work": it is plugged in with one call, use_provider(provider); a provider has one method,
send(phone, text). Example: TwilioProvider below, for now a stub with NotImplementedError and notes on connecting it.
"""
import datetime
import logging
import os
import threading
from pathlib import Path

from server.common import ApiError, to_utc

KINDS = ('otp', 'alert', 'sos', 'other')
KABUL = datetime.timezone(datetime.timedelta(hours=4, minutes=30))  # Afghanistan: UTC+4:30 all year round
LOG_NAME = 'sms.log'
DEFAULT_LIMIT = 50
MAX_LIMIT = 500

LOG_DIR = None     # log folder for all databases; None: the app's data_dir or the database file's folder
_provider = None   # None: test mode
_data_dirs = {}    # database file → app.config['data_dir'] (remembered by start(app))
_log_lock = threading.Lock()

log = logging.getLogger(__name__)

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS sms_outbox (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        phone      TEXT NOT NULL,
        text       TEXT NOT NULL,
        kind       TEXT NOT NULL,
        status     TEXT NOT NULL DEFAULT 'test',
        created_at TEXT NOT NULL
    )""",
    'CREATE INDEX IF NOT EXISTS sms_outbox_created ON sms_outbox (created_at)',
)


def init(db):
    """Own table sms_outbox (calling again changes nothing)."""
    for statement in SCHEMA:
        db.execute(statement)


def _now():
    """Now, UTC, without fractions of a second. Tests replace it to "move the clock"."""
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


# ---------- sending ----------

class SmsError(Exception):
    """The provider did not take the SMS: it was not sent and is not in sms_outbox. cause — the provider's error."""

    def __init__(self, phone, kind, cause):
        self.phone, self.kind, self.cause = phone, kind, cause
        super().__init__('SMS to %s (%s) not sent: %s: %s' % (phone, kind, type(cause).__name__, cause))


def send(db, phone, text, kind='other'):
    """Send an SMS → sms_outbox row id. In test mode it is only a database row and a log line.

    A provider failure → SmsError (nothing is recorded as sent, the log gets a 'failed' line).
    """
    if kind not in KINDS:
        raise ValueError('sms.send: kind must be one of %s, not %r' % (', '.join(KINDS), kind))
    phone = '' if phone is None else str(phone).strip()
    if not phone:
        raise ValueError('sms.send: empty phone number')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('sms.send: empty text')
    provider = _provider
    if provider is None:
        status = 'test'
    else:  # network before the database write: a write locks the database (see store)
        try:
            provider.send(phone, text)
            status = 'sent'
        except Exception as exc:  # not a send: no sms_outbox row; the caller retries or reports it
            note = '%s: %s' % (type(exc).__name__, exc)
            log.warning('sms: SMS to %s not sent: %s', phone, note)
            _write_log(db, None, to_utc(_now()), phone, kind, 'failed', text, note)
            raise SmsError(phone, kind, exc) from exc
    created_at = to_utc(_now())
    cur = db.execute(
        'INSERT INTO sms_outbox (phone, text, kind, status, created_at) VALUES (?, ?, ?, ?, ?)',
        (phone, text, kind, status, created_at))
    sms_id = cur.lastrowid
    _write_log(db, sms_id, created_at, phone, kind, status, text)
    return sms_id


def start(app):
    """No background tasks: only remember app.config['data_dir'], where this database's log will go."""
    config = getattr(app, 'config', None) or {}
    data_dir = config.get('data_dir') or getattr(app, 'data_dir', None)
    db_path = config.get('db_path') or getattr(app, 'db_path', None)
    if data_dir and db_path:
        _data_dirs[_path_key(db_path)] = Path(data_dir)


def _path_key(path):
    return os.path.normcase(str(Path(path).resolve()))


def _db_file(db):
    for row in db.execute('PRAGMA database_list'):
        if row[1] == 'main':
            return row[2] or None
    return None


def log_path(db):
    """Log file sms.log: in LOG_DIR, else in the app's data_dir, else next to the database file; ':memory:' → None."""
    if LOG_DIR is not None:
        return Path(LOG_DIR) / LOG_NAME
    db_file = _db_file(db)
    if not db_file:
        return None
    folder = _data_dirs.get(_path_key(db_file)) or Path(db_file).parent
    return folder / LOG_NAME


def _write_log(db, sms_id, created_at, phone, kind, status, text, note=''):
    """One SMS is one log line: time, #id, kind, status, number, text (text lines joined with ' | ')."""
    path = log_path(db)
    if path is None:
        return
    one_line = ' | '.join(part.strip() for part in text.splitlines() if part.strip())
    fields = [created_at, '#-' if sms_id is None else '#%d' % sms_id, kind, status, phone, one_line]
    if note:
        fields.append(note)
    line = '\t'.join(field.replace('\t', ' ') for field in fields) + '\n'
    try:
        with _log_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'a', encoding='utf-8') as fh:
                fh.write(line)
    except OSError as exc:  # the log is a copy; the database row is what matters
        log.warning('sms: log %s not written: %s', path, exc)


# ---------- provider ----------

def use_provider(provider):
    """Plug in a real provider (it has a method send(phone, text)); None switches back to test mode.

    Returns the previous provider.
    """
    global _provider
    previous, _provider = _provider, provider
    return previous


def is_test_mode():
    """True: no real SMS, the login code is shown on screen (test_code)."""
    return _provider is None


class TwilioProvider:
    """Real SMS via Twilio is "future work"; for now a stub, send() → NotImplementedError.

    How to connect it once there is a Twilio account and money for SMS:
      1. Open an account on twilio.com, buy a sender number (or a Messaging Service), and take the
         Account SID and Auth Token from the Console. Keep the keys in environment variables, not in code or git.
      2. Finish send(): POST https://api.twilio.com/2010-04-01/Accounts/<AccountSid>/Messages.json,
         form fields To=<+937XXXXXXXX>, From=<sender number>, Body=<text>; auth is HTTP Basic
         (Account SID and Auth Token). urllib.request and urllib.parse from the standard library are enough;
         the response is JSON, its sid field is Twilio's message id. Network error or a 4xx/5xx reply → raise:
         send() in this module turns it into SmsError, so nothing is recorded as sent.
      3. At server start: sms.use_provider(sms.TwilioProvider(sid, token, from_number)).
      4. Alert SMS already go through the alerts queue (its own thread, retries after 2/10/60 s, then failed);
         login codes are sent inside the request (a provider failure → 503 'sms_failed').
    """

    name = 'twilio'

    def __init__(self, account_sid, auth_token, from_number):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number

    def send(self, phone, text):
        raise NotImplementedError(
            'Twilio is not connected yet - PANAH runs in SMS test mode. How to connect it: see the '
            'TwilioProvider class docstring (app/server/sms.py).')


# ---------- stats and the admin list ----------

def _today_start():
    """Today's midnight in Kabul (timezone-aware datetime)."""
    return _now().astimezone(KABUL).replace(hour=0, minute=0, second=0, microsecond=0)


def stats(db):
    """{total, today, by_kind}: all SMS, since midnight in Kabul, by kind (every kind, even with zero)."""
    since = to_utc(_today_start())
    total = db.execute('SELECT count(*) FROM sms_outbox').fetchone()[0]
    today = db.execute('SELECT count(*) FROM sms_outbox WHERE created_at >= ?', (since,)).fetchone()[0]
    by_kind = dict.fromkeys(KINDS, 0)
    for kind, count in db.execute('SELECT kind, count(*) FROM sms_outbox GROUP BY kind'):
        by_kind[kind] = count
    return {'total': total, 'today': today, 'by_kind': by_kind}


def recent(db, limit=DEFAULT_LIMIT, kind=None):
    """Latest SMS (newest first) → list of dicts."""
    columns = ('id', 'phone', 'text', 'kind', 'status', 'created_at')
    sql = 'SELECT %s FROM sms_outbox' % ', '.join(columns)
    args = []
    if kind:
        sql += ' WHERE kind = ?'
        args.append(kind)
    sql += ' ORDER BY id DESC LIMIT ?'
    args.append(int(limit))
    return [dict(zip(columns, row)) for row in db.execute(sql, args)]


def _query_value(query, key):
    """Value of a URL query parameter: a string, a list of strings (parse_qs) or absent."""
    value = (query or {}).get(key)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    return None if value is None else str(value).strip()


def admin_sms(req):
    """GET /api/admin/sms?limit=50&kind=… — latest SMS and counters (admin only)."""
    from server import auth
    auth.require_admin(req)
    raw = _query_value(req.query, 'limit')
    if raw:
        try:
            limit = int(raw)
        except ValueError:
            raise ApiError(400, 'bad_limit', 'مقدار limit باید عدد باشد.')
        limit = max(1, min(MAX_LIMIT, limit))
    else:
        limit = DEFAULT_LIMIT
    kind = _query_value(req.query, 'kind') or None
    if kind is not None and kind not in KINDS:
        raise ApiError(400, 'bad_kind', 'نوع پیامک نادرست است.', kinds=list(KINDS))
    data = stats(req.db)
    data['items'] = recent(req.db, limit, kind)
    data['test_mode'] = is_test_mode()
    return 200, data


def routes():
    return [('GET', '/api/admin/sms', admin_sms)]
