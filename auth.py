"""auth — PANAH (پناه) login: phone number + SMS code, sessions, resident profile, admin login.

Contract. Errors are ApiError(HTTP code, reason, Dari text, extra fields → into the response):
  normalize_phone(s) → '+937XXXXXXXX' or ApiError 400 'bad_phone'. Accepts 07XXXXXXXX, 7XXXXXXXX, +937…,
    00937…, 937…, an extra 0 after +93, spaces, hyphens, brackets, dots, invisible text-direction marks,
    Dari (۰–۹) and Arabic (٠–٩) digits.
  POST /api/auth/code {phone} → {phone, expires_in: 300, resend_in: 45, test_code}
    code: 6 digits (secrets), only its hash is stored; SMS via sms.send (Dari + Pashto), test_code in test mode;
    429 'too_soon' {retry_after}: a new code before 45 s; 429 'too_many' {retry_after}: more than 5 codes per hour;
    503 'sms_failed': a real SMS provider failed — no code is stored, so asking again right away is allowed.
  POST /api/auth/verify {phone, code} → {user, is_new} + req.set_session(token)
    400 'wrong_code' {attempts_left}: after 5 mistakes the code is burned; 410 'expired': expired, burned or already
    used; 400 'no_code': no code was requested; 400 'bad_code': not 6 digits (the attempt is not counted).
    A new number creates a new users row (is_new: true).
  POST /api/auth/logout → {ok: true}: session deleted from the database, cookie cleared (req.clear_session).
  GET /api/me → 200 {user, admin}: resident — admin: null; admin — user: null; guest — both null (not 401: every
    screen asks who is logged in, and the browser logs no console error). Protected routes: 401/403 via require_*.
  PUT /api/me {name, blood_group, health, contacts, home_lat, home_lon, home_address} → {user}
    only the fields sent are changed; a bad field → 400 with a reason: bad_name, bad_blood_group, bad_health,
    bad_contacts, bad_home, bad_address. The phone number cannot be changed this way, only by a new SMS login.
  POST /api/me/location {lat, lon, accuracy} → {user}: users.last_lat / last_lon (accuracy is only validated).
  POST /api/admin/login {username, password} → {admin} + session; 400 'wrong_login' {attempts_left};
    5 wrong in a row → 429 'locked' {retry_after} for 5 minutes (even with the right password).
  POST /api/admin/otp {username} → {phone (masked), expires_in, resend_in}: the code goes only to the admin's phone
    (sms_outbox and data/sms.log), never in the response, not even in test mode — otherwise a user name alone
    would open the admin panel (experiment 05). Residents keep test_code: that is the project's test mode;
  POST /api/admin/otp/verify {username, code} → {admin} + session (errors as in /api/auth/verify).
For web and other engines:
  who(db, token) → {user, admin}: no token, an unknown or expired one → both None.
  require_user(req) → req.user or ApiError 401 'login_required'.
  require_admin(req) → req.admin or ApiError 403 'admin_required' (no admin login: for a guest and a resident alike).
  Codes and texts are the same as in req.require_user() / req.require_admin() in web.
Session: token = secrets.token_urlsafe(32) in a cookie (set by web via req.set_session), the database keeps only its
SHA-256; it lives 30 days. Admin password: PBKDF2-HMAC-SHA256, 200 000 iterations, its own salt.
Own tables: otp_codes, sessions, admins, admin_login_fails. web commits (at the end of the request).
Demo admin: DEMO_ADMIN_* below (and app/README-admin.md); created only if there are no admins in the database.
"""
import datetime
import hashlib
import hmac
import math
import re
import secrets
import sqlite3

from server.common import ApiError, parse_utc, to_utc

CODE_TTL = 300             # a code lives 5 minutes
RESEND_AFTER = 45          # a new code no sooner than 45 s later
CODES_PER_HOUR = 5         # at most 5 codes per hour per number
MAX_ATTEMPTS = 5           # 5 wrong entries burn the code
SESSION_DAYS = 30
SESSION_COOKIE = 'panah_session'   # same as web.SESSION_COOKIE: the token for logout is taken from it
ADMIN_MAX_FAILS = 5        # 5 wrong passwords in a row —
ADMIN_LOCK_SECONDS = 300   # and admin login is locked for 5 minutes
PBKDF2_ITERATIONS = 200000

# The project's demo admin (not a secret: it is also written in app/README-admin.md).
DEMO_ADMIN_USERNAME = 'admin'
DEMO_ADMIN_PASSWORD = 'panah1234'
DEMO_ADMIN_PHONE = '+93700000000'
DEMO_ADMIN_NAME = 'مدیر سیستم'

BLOOD_GROUPS = ('A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-', 'unknown')
HEALTH_OPTIONS = ('epilepsy', 'blood_pressure', 'diabetes', 'disability', 'heart', 'drug_allergy', 'none')
NAME_MAX = 100
ADDRESS_MAX = 300
MAX_CONTACTS = 10

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS otp_codes (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        phone      TEXT NOT NULL,
        purpose    TEXT NOT NULL DEFAULT 'user',   -- user: resident login, admin: admin login by code
        code_hash  TEXT NOT NULL,                  -- sha256(salt:code); the code itself is not stored
        salt       TEXT NOT NULL,
        attempts   INTEGER NOT NULL DEFAULT 0,     -- wrong entries
        status     TEXT NOT NULL DEFAULT 'active', -- active | used | burned | replaced
        sms_id     INTEGER,                        -- sms_outbox row
        ip         TEXT,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used_at    TEXT
    )""",
    'CREATE INDEX IF NOT EXISTS otp_codes_phone ON otp_codes (phone, purpose, created_at)',
    """CREATE TABLE IF NOT EXISTS admins (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,               -- pbkdf2_sha256$iterations$salt$hash
        phone         TEXT,
        name          TEXT DEFAULT '',
        created_at    TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sessions (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        token_hash TEXT NOT NULL UNIQUE,           -- sha256(token); the token itself is only in the cookie
        user_id    INTEGER REFERENCES users (id) ON DELETE CASCADE,
        admin_id   INTEGER REFERENCES admins (id) ON DELETE CASCADE,
        ip         TEXT,
        device_id  TEXT,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS admin_login_fails (
        username     TEXT PRIMARY KEY COLLATE NOCASE,
        fails        INTEGER NOT NULL DEFAULT 0,  -- wrong passwords in a row
        locked_until TEXT                         -- login is locked until this time (UTC)
    )""",
)


def init(db):
    """Own tables and the demo admin (only if there are no admins, so a restart does not repeat it)."""
    for statement in SCHEMA:
        db.execute(statement)
    if db.execute('SELECT count(*) FROM admins').fetchone()[0] == 0:
        add_admin(db, DEMO_ADMIN_USERNAME, DEMO_ADMIN_PASSWORD, DEMO_ADMIN_PHONE, DEMO_ADMIN_NAME)


def _now():
    """Now, UTC, without fractions of a second. Tests replace it to "move the clock"."""
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def _seconds_until(moment_text):
    """Whole seconds (rounded up) left until the time '…Z'; already passed → 0."""
    left = (parse_utc(moment_text) - _now()).total_seconds()
    return max(0, math.ceil(left))


# ---------- digits and phone number ----------

_TO_ASCII = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
_TO_DARI = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
# spaces, hyphens and dashes, brackets, dots, invisible marks (ZWNJ, LRM/RLM, embeddings and isolates, BOM, ALM)
_PHONE_JUNK = re.compile('[\\s\\-‐-―−().؜​-‏‪-‮⁦-⁩﻿]')
_NATIONAL = re.compile('7[0-9]{8}')


def to_ascii_digits(text):
    """Dari and Arabic digits → 0–9 (everything else as is)."""
    return str(text).translate(_TO_ASCII)


def dari_digits(value):
    """Number or text → the same digits in Dari: 45 → '۴۵'."""
    return str(value).translate(_TO_DARI)


def _bad_phone():
    return ApiError(400, 'bad_phone', 'شمارهٔ موبایل نادرست است. شماره را مانند ۰۷۷۷۱۲۳۴۵۶ (۱۰ رقم) وارد کنید.')


def normalize_phone(value):
    """An Afghan mobile number in any usual form → '+937XXXXXXXX'; otherwise ApiError 400 'bad_phone'."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise _bad_phone()
    text = _PHONE_JUNK.sub('', to_ascii_digits(value))
    if text.startswith('+'):
        if not text.startswith('+93'):
            raise _bad_phone()
        national = text[3:]
    elif text.startswith('0093'):
        national = text[4:]
    elif text.startswith('93') and len(text) in (11, 12):
        national = text[2:]
    else:
        national = text
    if len(national) == 10 and national.startswith('0'):  # 07XXXXXXXX or +93 0 7XXXXXXXX
        national = national[1:]
    if not _NATIONAL.fullmatch(national):
        raise _bad_phone()
    return '+93' + national


def _mask_phone(phone):
    """'+93700123456' → '+9370*****56', to show the admin where the code went."""
    if not phone or len(phone) < 8:
        return phone or ''
    return phone[:5] + '*' * (len(phone) - 7) + phone[-2:]


# ---------- one-time codes ----------

def _write_lock(db):
    """Take the database write lock now (BEGIN IMMEDIATE): the limit check and the write cannot race, so a double
    tap on 'send code' on the phone does not issue two codes. web commits (releases it) at the end of the request."""
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')


def _code_hash(salt, code):
    return hashlib.sha256(('%s:%s' % (salt, code)).encode('ascii')).hexdigest()


def _code_sms(code):
    return 'کد تأیید پناه: %s\nد پناه د تایید کوډ: %s' % (code, code)


def _clean_code(value):
    """Code input → 6 ASCII digits; otherwise ApiError 400 'bad_code' (the attempt is not counted)."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        value = str(value).zfill(6)
    if not isinstance(value, str):
        raise ApiError(400, 'bad_code', 'کد باید ۶ رقم باشد.')
    code = _PHONE_JUNK.sub('', to_ascii_digits(value))
    if not re.fullmatch('[0-9]{6}', code):
        raise ApiError(400, 'bad_code', 'کد باید ۶ رقم باشد.')
    return code


def issue_code(db, phone, purpose='user', ip=None):
    """New code for a number: limit checks, hash into otp_codes, SMS → {code, sms_id}."""
    from server import sms
    _write_lock(db)
    now = _now()
    hour_ago = to_utc(now - datetime.timedelta(hours=1))
    recent = [row[0] for row in db.execute(
        'SELECT created_at FROM otp_codes WHERE phone = ? AND purpose = ? AND created_at > ? ORDER BY created_at',
        (phone, purpose, hour_ago))]
    if len(recent) >= CODES_PER_HOUR:
        wait = max(1, math.ceil((parse_utc(recent[0]) + datetime.timedelta(hours=1) - now).total_seconds()))
        raise ApiError(429, 'too_many',
                       'در یک ساعت بیشتر از %s کد نمی‌شود. لطفاً %s دقیقه بعد دوباره کوشش کنید.'
                       % (dari_digits(CODES_PER_HOUR), dari_digits(math.ceil(wait / 60))),
                       retry_after=wait)
    if recent:
        passed = (now - parse_utc(recent[-1])).total_seconds()
        if passed < RESEND_AFTER:
            wait = max(1, math.ceil(RESEND_AFTER - passed))
            raise ApiError(429, 'too_soon',
                           'کد تازه فرستاده شد. لطفاً %s ثانیه بعد دوباره کوشش کنید.' % dari_digits(wait),
                           retry_after=wait)
    code = '%06d' % secrets.randbelow(1000000)
    try:  # the SMS first: if the provider fails, nothing is written (web commits even after an ApiError)
        sms_id = sms.send(db, phone, _code_sms(code), 'otp')
    except sms.SmsError as exc:
        raise ApiError(503, 'sms_failed', 'پیامک فرستاده نشد. لطفاً چند لحظه بعد دوباره کوشش کنید.') from exc
    salt = secrets.token_hex(8)
    db.execute("UPDATE otp_codes SET status = 'replaced' WHERE phone = ? AND purpose = ? AND status = 'active'",
               (phone, purpose))
    db.execute(
        'INSERT INTO otp_codes (phone, purpose, code_hash, salt, sms_id, ip, created_at, expires_at)'
        ' VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (phone, purpose, _code_hash(salt, code), salt, sms_id, ip, to_utc(now),
         to_utc(now + datetime.timedelta(seconds=CODE_TTL))))
    db.execute('DELETE FROM otp_codes WHERE created_at < ?', (to_utc(now - datetime.timedelta(days=1)),))
    return {'code': code, 'sms_id': sms_id}


def check_code(db, phone, value, purpose='user'):
    """Check the code of the latest request for the number; a correct one is marked used, otherwise ApiError."""
    code = _clean_code(value)
    _write_lock(db)
    row = db.execute(
        'SELECT * FROM otp_codes WHERE phone = ? AND purpose = ? ORDER BY id DESC LIMIT 1',
        (phone, purpose)).fetchone()
    if row is None:
        raise ApiError(400, 'no_code', 'برای این شماره کدی فرستاده نشده است. ابتدا کد بخواهید.')
    if row['status'] == 'used':
        raise ApiError(410, 'expired', 'این کد قبلاً استفاده شده است. کد جدید بخواهید.')
    if row['status'] != 'active':
        raise ApiError(410, 'expired', 'این کد پس از ۵ کوشش نادرست باطل شد. کد جدید بخواهید.')
    if row['expires_at'] <= to_utc(_now()):
        raise ApiError(410, 'expired', 'مدت اعتبار کد تمام شده است. کد جدید بخواهید.')
    if not hmac.compare_digest(_code_hash(row['salt'], code), row['code_hash']):
        attempts = row['attempts'] + 1
        left = max(0, MAX_ATTEMPTS - attempts)
        db.execute('UPDATE otp_codes SET attempts = ?, status = ? WHERE id = ?',
                   (attempts, 'active' if left else 'burned', row['id']))
        if left:
            message = 'کد نادرست است. %s بار دیگر می‌توانید کوشش کنید.' % dari_digits(left)
        else:
            message = 'کد نادرست است. این کد باطل شد؛ کد جدید بخواهید.'
        raise ApiError(400, 'wrong_code', message, attempts_left=left)
    db.execute("UPDATE otp_codes SET status = 'used', used_at = ? WHERE id = ?", (to_utc(_now()), row['id']))


# ---------- sessions ----------

def _token_hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def create_session(db, user_id=None, admin_id=None, ip=None, device_id=None):
    """New resident or admin session → token (only its hash goes into the database)."""
    token = secrets.token_urlsafe(32)
    now = _now()
    db.execute('DELETE FROM sessions WHERE expires_at <= ?', (to_utc(now),))
    db.execute(
        'INSERT INTO sessions (token_hash, user_id, admin_id, ip, device_id, created_at, expires_at)'
        ' VALUES (?, ?, ?, ?, ?, ?, ?)',
        (_token_hash(token), user_id, admin_id, ip, device_id, to_utc(now),
         to_utc(now + datetime.timedelta(days=SESSION_DAYS))))
    return token


def who(db, token):
    """Who is behind the token → {user, admin}: resident dict (as store.user_dict) and/or admin; otherwise both None."""
    from server import store
    result = {'user': None, 'admin': None}
    if not isinstance(token, str) or not token or len(token) > 256:
        return result
    row = db.execute('SELECT user_id, admin_id, expires_at FROM sessions WHERE token_hash = ?',
                     (_token_hash(token),)).fetchone()
    if row is None or row['expires_at'] <= to_utc(_now()):
        return result
    if row['user_id'] is not None:
        result['user'] = store.get_user(db, row['user_id'])
    if row['admin_id'] is not None:
        result['admin'] = admin_dict(db.execute('SELECT * FROM admins WHERE id = ?', (row['admin_id'],)).fetchone())
    return result


def _request_token(req):
    """Token of the current session: cookie panah_session (req.cookies) or req.token (a simple req in tests)."""
    cookies = getattr(req, 'cookies', None)
    value = cookies.get(SESSION_COOKIE) if isinstance(cookies, dict) else None
    if not value:
        value = getattr(req, 'token', None)
    return value if isinstance(value, str) and value else None


def _start_session(req, user_id=None, admin_id=None):
    token = create_session(req.db, user_id=user_id, admin_id=admin_id,
                           ip=getattr(req, 'ip', None), device_id=getattr(req, 'device_id', None))
    setter = getattr(req, 'set_session', None)
    if callable(setter):
        setter(token)
    return token


def require_user(req):
    """The logged-in resident (dict) or ApiError 401 'login_required'."""
    user = getattr(req, 'user', None)
    if not user:
        raise _login_required()
    return user


def require_admin(req):
    """The logged-in admin (dict) or ApiError 403 'admin_required'."""
    admin = getattr(req, 'admin', None)
    if not admin:
        raise ApiError(403, 'admin_required', 'این بخش تنها برای مدیر است.')
    return admin


def _login_required():
    return ApiError(401, 'login_required', 'لطفاً اول وارد حساب خود شوید.')


# ---------- admins ----------

def hash_password(password, iterations=PBKDF2_ITERATIONS):
    """Password → 'pbkdf2_sha256$iterations$salt$hash' (salt: 16 random bytes)."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', to_ascii_digits(password).encode('utf-8'), salt, iterations)
    return 'pbkdf2_sha256$%d$%s$%s' % (iterations, salt.hex(), digest.hex())


def check_password(password, stored):
    """Does the password match the stored hash? Dari digits in a password equal digits 0–9 (Dari keyboard)."""
    try:
        algorithm, iterations, salt, expected = stored.split('$')
        if algorithm != 'pbkdf2_sha256':
            return False
        digest = hashlib.pbkdf2_hmac('sha256', to_ascii_digits(password).encode('utf-8'),
                                     bytes.fromhex(salt), int(iterations))
    except (AttributeError, TypeError, ValueError):
        return False
    return hmac.compare_digest(digest.hex(), expected)


_DUMMY_HASH = []


def _dummy_hash():
    """Dummy hash: an unknown username takes as long to check as a known one."""
    if not _DUMMY_HASH:
        _DUMMY_HASH.append(hash_password(secrets.token_hex(8)))
    return _DUMMY_HASH[0]


def _clean_username(value):
    return to_ascii_digits(value).strip().lower() if isinstance(value, str) else ''


def admin_dict(row):
    """admins row → {id, username, name, phone} (without the password hash)."""
    if row is None:
        return None
    return {'id': row['id'], 'username': row['username'], 'name': row['name'] or '', 'phone': row['phone'] or ''}


def add_admin(db, username, password, phone=None, name=''):
    """New admin → id. Bad number → ApiError 400 'bad_phone'; a taken username → sqlite3.IntegrityError."""
    username = _clean_username(username)
    if not username or not isinstance(password, str) or not password:
        raise ValueError('auth.add_admin: username and password are required')
    cur = db.execute(
        'INSERT INTO admins (username, password_hash, phone, name, created_at) VALUES (?, ?, ?, ?, ?)',
        (username, hash_password(password), normalize_phone(phone) if phone else None, name or '',
         to_utc(_now())))
    return cur.lastrowid


def set_admin_password(db, username, password):
    """Change an admin's password → True if such an admin exists. The caller commits."""
    if not isinstance(password, str) or not password:
        raise ValueError('auth.set_admin_password: empty password')
    cur = db.execute('UPDATE admins SET password_hash = ? WHERE username = ?',
                     (hash_password(password), _clean_username(username)))
    return cur.rowcount > 0


def renew_demo_password(db, old_password):
    """The demo admin still has old_password (a database from an earlier version) → DEMO_ADMIN_PASSWORD instead.
    → True if changed; any other password (chosen by the owner) stays. The caller commits."""
    row = db.execute('SELECT password_hash FROM admins WHERE username = ?', (DEMO_ADMIN_USERNAME,)).fetchone()
    if row is None or not check_password(old_password, row['password_hash']):
        return False
    return set_admin_password(db, DEMO_ADMIN_USERNAME, DEMO_ADMIN_PASSWORD)


def _locked_error(seconds):
    return ApiError(429, 'locked',
                    '%s بار رمز نادرست وارد شد. ورود برای %s دقیقه بسته است.'
                    % (dari_digits(ADMIN_MAX_FAILS), dari_digits(max(1, math.ceil(seconds / 60)))),
                    retry_after=seconds)


def _lock_left(db, username):
    row = db.execute('SELECT locked_until FROM admin_login_fails WHERE username = ?', (username,)).fetchone()
    return _seconds_until(row['locked_until']) if row and row['locked_until'] else 0


def _note_fail(db, username):
    """One more wrong password → how many attempts are left (0: login is locked for ADMIN_LOCK_SECONDS)."""
    _write_lock(db)  # after PBKDF2, not before: slow work stays outside the write
    row = db.execute('SELECT fails FROM admin_login_fails WHERE username = ?', (username,)).fetchone()
    fails = (row['fails'] if row else 0) + 1
    if fails >= ADMIN_MAX_FAILS:
        until = to_utc(_now() + datetime.timedelta(seconds=ADMIN_LOCK_SECONDS))
        db.execute('INSERT OR REPLACE INTO admin_login_fails (username, fails, locked_until) VALUES (?, 0, ?)',
                   (username, until))
        return 0
    db.execute('INSERT OR REPLACE INTO admin_login_fails (username, fails, locked_until) VALUES (?, ?, NULL)',
               (username, fails))
    return ADMIN_MAX_FAILS - fails


def _find_admin(db, username):
    if not username:
        return None
    return db.execute('SELECT * FROM admins WHERE username = ?', (username,)).fetchone()


# ---------- parsing the request body and profile fields ----------

def _body(req):
    body = getattr(req, 'body', None)
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise ApiError(400, 'bad_body', 'داده‌های درخواست نادرست است.')
    return body


def _number(value, code, message, low, high):
    """Number (or a string with digits 0–9 / Dari, decimal sign '.' or '٫') in [low, high]; otherwise 400."""
    if isinstance(value, bool):
        raise ApiError(400, code, message)
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            number = float(to_ascii_digits(value.strip()).replace('٫', '.'))
        except ValueError:
            raise ApiError(400, code, message)
    else:
        raise ApiError(400, code, message)
    if not math.isfinite(number) or not low <= number <= high:
        raise ApiError(400, code, message)
    return number


def _clean_text(value, code, message, limit):
    if value is None:
        return ''
    if not isinstance(value, str) or len(value.strip()) > limit:
        raise ApiError(400, code, message)
    return value.strip()


def _clean_blood_group(value):
    if value is None or value == '':
        return ''
    if isinstance(value, str):
        text = re.sub('[\\s‐-―−]', lambda m: '' if m.group().isspace() else '-', value).upper()
        text = 'unknown' if text == 'UNKNOWN' else text
        if text in BLOOD_GROUPS:
            return text
    raise ApiError(400, 'bad_blood_group', 'گروه خونی نادرست است.', allowed=list(BLOOD_GROUPS))


def _clean_health(value):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ApiError(400, 'bad_health', 'وضعیت صحی باید فهرست باشد.', allowed=list(HEALTH_OPTIONS))
    result = []
    for item in value:
        key = item.strip().lower() if isinstance(item, str) else None
        if key not in HEALTH_OPTIONS:
            raise ApiError(400, 'bad_health', 'وضعیت صحی نادرست است: %s' % (item,), allowed=list(HEALTH_OPTIONS))
        if key not in result:
            result.append(key)
    if 'none' in result and len(result) > 1:  # 'بدون مشکل خاص' together with illnesses: the illnesses stay
        result.remove('none')
    return result


def _clean_contacts(value):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ApiError(400, 'bad_contacts', 'مخاطبین اضطراری باید فهرست باشد.')
    if len(value) > MAX_CONTACTS:
        raise ApiError(400, 'bad_contacts', 'حداکثر %s مخاطب اضطراری.' % dari_digits(MAX_CONTACTS))
    result, seen = [], set()
    for number, item in enumerate(value, 1):
        if not isinstance(item, dict):
            raise ApiError(400, 'bad_contacts', 'مخاطب %s نادرست است.' % dari_digits(number), index=number - 1)
        name = _clean_text(item.get('name'), 'bad_contacts', 'نام مخاطب %s نادرست است.' % dari_digits(number),
                           NAME_MAX)
        try:
            phone = normalize_phone(item.get('phone'))
        except ApiError:
            raise ApiError(400, 'bad_contacts', 'شمارهٔ مخاطب %s نادرست است.' % dari_digits(number),
                           index=number - 1)
        if phone not in seen:
            seen.add(phone)
            result.append({'name': name, 'phone': phone})
    return result


def _clean_profile(body):
    """Profile fields from the PUT /api/me body → {users column: value} (only the fields sent)."""
    from server import store
    changes = {}
    if 'name' in body:
        changes['name'] = _clean_text(body['name'], 'bad_name', 'نام باید متن و حداکثر %s حرف باشد.'
                                      % dari_digits(NAME_MAX), NAME_MAX)
    if 'blood_group' in body:
        changes['blood_group'] = _clean_blood_group(body['blood_group'])
    if 'health' in body:
        changes['health'] = store.dumps(_clean_health(body['health']))
    if 'contacts' in body:
        changes['contacts'] = store.dumps(_clean_contacts(body['contacts']))
    if 'home_lat' in body or 'home_lon' in body:
        lat, lon = body.get('home_lat'), body.get('home_lon')
        if lat is None and lon is None:
            changes['home_lat'] = changes['home_lon'] = None
        else:
            message = 'موقعیت خانه نادرست است: عرض و طول جغرافیایی هر دو لازم است.'
            changes['home_lat'] = _number(lat, 'bad_home', message, -90, 90)
            changes['home_lon'] = _number(lon, 'bad_home', message, -180, 180)
    if 'home_address' in body:
        changes['home_address'] = _clean_text(body['home_address'], 'bad_address',
                                              'آدرس باید متن و حداکثر %s حرف باشد.' % dari_digits(ADDRESS_MAX),
                                              ADDRESS_MAX)
    return changes


def _user_by_phone(db, phone):
    """Resident by number → (dict, is_new); created if missing."""
    from server import store
    row = db.execute('SELECT id FROM users WHERE phone = ?', (phone,)).fetchone()
    if row is not None:
        return store.get_user(db, row['id']), False
    try:
        cur = db.execute("INSERT INTO users (phone, health, contacts, created_at) VALUES (?, '[]', '[]', ?)",
                         (phone, to_utc(_now())))
    except sqlite3.IntegrityError:  # the same number was just created by another request
        row = db.execute('SELECT id FROM users WHERE phone = ?', (phone,)).fetchone()
        return store.get_user(db, row['id']), False
    return store.get_user(db, cur.lastrowid), True


# ---------- handlers ----------

def request_code(req):
    """POST /api/auth/code {phone} → {phone, expires_in, resend_in, test_code}."""
    from server import sms
    phone = normalize_phone(_body(req).get('phone'))
    issued = issue_code(req.db, phone, 'user', getattr(req, 'ip', None))
    return 200, {'phone': phone, 'expires_in': CODE_TTL, 'resend_in': RESEND_AFTER,
                 'test_code': issued['code'] if sms.is_test_mode() else None}


def verify_code(req):
    """POST /api/auth/verify {phone, code} → {user, is_new} + resident session."""
    body = _body(req)
    phone = normalize_phone(body.get('phone'))
    check_code(req.db, phone, body.get('code'), 'user')
    user, is_new = _user_by_phone(req.db, phone)
    _start_session(req, user_id=user['id'])
    return 200, {'user': user, 'is_new': is_new}


def logout(req):
    """POST /api/auth/logout → {ok: true}; without a login it is ok too."""
    token = _request_token(req)
    if token:
        req.db.execute('DELETE FROM sessions WHERE token_hash = ?', (_token_hash(token),))
    clear = getattr(req, 'clear_session', None)
    if callable(clear):
        clear()
    return 200, {'ok': True}


def me(req):
    """GET /api/me → {user, admin}; a guest gets {user: null, admin: null} with code 200."""
    return 200, {'user': getattr(req, 'user', None) or None, 'admin': getattr(req, 'admin', None) or None}


def update_me(req):
    """PUT /api/me {profile fields} → {user}."""
    from server import store
    user = require_user(req)
    changes = _clean_profile(_body(req))
    if changes:
        columns = sorted(changes)
        req.db.execute('UPDATE users SET %s WHERE id = ?' % ', '.join('%s = ?' % c for c in columns),
                       [changes[c] for c in columns] + [user['id']])
    return 200, {'user': store.get_user(req.db, user['id'])}


def update_location(req):
    """POST /api/me/location {lat, lon, accuracy} → {user}."""
    from server import store
    user = require_user(req)
    body = _body(req)
    message = 'موقعیت نادرست است.'
    lat = _number(body.get('lat'), 'bad_location', message, -90, 90)
    lon = _number(body.get('lon'), 'bad_location', message, -180, 180)
    if body.get('accuracy') is not None:
        _number(body['accuracy'], 'bad_location', 'دقت موقعیت نادرست است.', 0, 10 ** 7)
    req.db.execute('UPDATE users SET last_lat = ?, last_lon = ? WHERE id = ?', (lat, lon, user['id']))
    return 200, {'user': store.get_user(req.db, user['id'])}


def admin_login(req):
    """POST /api/admin/login {username, password} → {admin} + admin session."""
    body = _body(req)
    username, password = _clean_username(body.get('username')), body.get('password')
    if not username or not isinstance(password, str) or not password:
        raise ApiError(400, 'missing_login', 'نام کاربری و رمز عبور را وارد کنید.')
    left = _lock_left(req.db, username)
    if left:
        raise _locked_error(left)
    row = _find_admin(req.db, username)
    ok = check_password(password, row['password_hash'] if row else _dummy_hash())
    if row is None or not ok:
        attempts_left = _note_fail(req.db, username)
        if not attempts_left:
            raise _locked_error(ADMIN_LOCK_SECONDS)
        raise ApiError(400, 'wrong_login', 'نام کاربری یا رمز عبور نادرست است. %s کوشش دیگر باقی است.'
                       % dari_digits(attempts_left), attempts_left=attempts_left)
    req.db.execute('DELETE FROM admin_login_fails WHERE username = ?', (username,))
    _start_session(req, admin_id=row['id'])
    return 200, {'admin': admin_dict(row)}


def admin_otp(req):
    """POST /api/admin/otp {username} → {phone (masked), expires_in, resend_in} — no test_code, see above."""
    row = _find_admin(req.db, _clean_username(_body(req).get('username')))
    if row is None:
        raise ApiError(400, 'wrong_login', 'نام کاربری نادرست است.')
    if not row['phone']:
        raise ApiError(400, 'no_phone', 'برای این مدیر شمارهٔ موبایل ثبت نشده است.')
    issue_code(req.db, row['phone'], 'admin', getattr(req, 'ip', None))
    return 200, {'phone': _mask_phone(row['phone']), 'expires_in': CODE_TTL, 'resend_in': RESEND_AFTER}


def admin_otp_verify(req):
    """POST /api/admin/otp/verify {username, code} → {admin} + admin session."""
    body = _body(req)
    row = _find_admin(req.db, _clean_username(body.get('username')))
    if row is None:
        raise ApiError(400, 'wrong_login', 'نام کاربری نادرست است.')
    if not row['phone']:
        raise ApiError(400, 'no_phone', 'برای این مدیر شمارهٔ موبایل ثبت نشده است.')
    check_code(req.db, row['phone'], body.get('code'), 'admin')
    req.db.execute('DELETE FROM admin_login_fails WHERE username = ?', (row['username'],))
    _start_session(req, admin_id=row['id'])
    return 200, {'admin': admin_dict(row)}


def routes():
    return [
        ('POST', '/api/auth/code', request_code),
        ('POST', '/api/auth/verify', verify_code),
        ('POST', '/api/auth/logout', logout),
        ('GET', '/api/me', me),
        ('PUT', '/api/me', update_me),
        ('POST', '/api/me/location', update_location),
        ('POST', '/api/admin/login', admin_login),
        ('POST', '/api/admin/otp', admin_otp),
        ('POST', '/api/admin/otp/verify', admin_otp_verify),
    ]
