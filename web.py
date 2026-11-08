"""web — the PANAH glue: HTTP server, engine routes, session, static files, image upload.

Engines are ENGINES, in order: sms, auth, geo, alerts, usgs, sos, content (module server/<name>.py).
No file yet → the engine is skipped (logged as info). An engine that fails on import or in init is logged
(error) and turned off, the others keep working. What an engine may have (all optional):
    init(db)      — its tables and seed data; called at startup in ENGINES order, web commits;
    routes()      → [(method, path, handler)], path with parameters: '/api/alerts/{id}' (string) or
                    '/api/alerts/{id:int}' (digits only, passed as int); a trailing '/' in the request does no harm;
    start(app)    — background tasks (daemon thread). app.stopping is the threading.Event for shutdown,
                    app.db_path is the database file: a thread opens its own connection store.connect(app.db_path);
                    app.config['<engine name>'] is False → start is not called (that is how --no-usgs stops polling).
web's own routes: GET /api/stream (live), POST /api/upload (at most 20 per hour per device and per IP, admin — no
limit; above that, 429 with retry_after), GET /api/health; GET /uploads/…; any other GET —
static files from app/web/.

Handler: handler(req) → (code, dict) | (code, dict, {headers}) | dict (= 200). The response is JSON UTF-8,
Dari as is. ApiError(code, reason, Dari text) → {"error": reason, "message": text, ...};
any other exception → 500 {"error": "server_error"} and a traceback in the log.
Database in a request: req.db, a pool connection (store.Pool) owned by this request only. At the end web commits
(on ApiError too, so attempt counters are not lost; on any other exception, rollback) and returns it to the pool.

req: db, user, admin, body (dict from JSON), query (dict of strings), params (from path), ip, device_id, app, headers,
cookies, method, path; req.set_session(token[, max_age]) / req.clear_session() — cookie panah_session;
req.require_user() / req.require_admin() — resident / admin or ApiError 401 / 403; req.add_header(name, value).
Session: cookie panah_session → auth.who(db, token) → {user, admin} → req.user, req.admin (no auth → guest).
Device: cookie panah_device (a random id for a year, HttpOnly) → req.device_id, for SOS without login.
app.config: port, db_path, data_dir, base_dir, usgs; app.data_dir, app.uploads_dir, app.hub (live).
"""
import base64
import binascii
import errno
import importlib
import ipaddress
import json
import collections
import logging
import math
import os
import re
import secrets
import socket
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from server import live, store
from server.common import ApiError, to_utc, utcnow

APP_DIR = Path(__file__).resolve().parent.parent
BASE_DIR = APP_DIR.parent
WEB_DIR = APP_DIR / 'web'
DEFAULT_DB = BASE_DIR / 'data' / 'panah.db'

ENGINES = ['sms', 'auth', 'geo', 'alerts', 'usgs', 'sos', 'content']

SESSION_COOKIE = 'panah_session'
DEVICE_COOKIE = 'panah_device'
SESSION_MAX_AGE = 30 * 24 * 3600
DEVICE_MAX_AGE = 365 * 24 * 3600
MAX_BODY = 6 * 1024 * 1024
MAX_IMAGE = 3 * 1024 * 1024
UPLOAD_LIMIT = 20       # uploads per window, per device (panah_device) and per IP
UPLOAD_WINDOW = 3600    # window, seconds
METHODS = ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')
POOL_SIZE = 16          # idle database connections kept by the pool

MIME = {
    '.html': 'text/html; charset=utf-8',
    '.htm': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.mjs': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.map': 'application/json; charset=utf-8',
    '.webmanifest': 'application/manifest+json; charset=utf-8',
    '.txt': 'text/plain; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.gif': 'image/gif',
    '.ico': 'image/x-icon',
    '.woff2': 'font/woff2',
    '.woff': 'font/woff',
    '.ttf': 'font/ttf',
    '.otf': 'font/otf',
    '.mp3': 'audio/mpeg',
    '.mp4': 'video/mp4',
}
IMAGE_EXT = ('.png', '.jpg', '.webp')

MSG = {
    'not_found': 'پیدا نشد.',
    'method_not_allowed': 'این درخواست پذیرفته نمی‌شود.',
    'bad_request': 'درخواست نادرست است.',
    'bad_json': 'درخواست نادرست است.',
    'too_large': 'درخواست بسیار بزرگ است.',
    'server_error': 'خطای سرور رخ داد. لطفاً دوباره کوشش کنید.',
    'login_required': 'لطفاً اول وارد حساب خود شوید.',
    'admin_required': 'این بخش تنها برای مدیر است.',
    'no_image': 'تصویر انتخاب نشده است.',
    'bad_image': 'تنها تصویر PNG، JPG یا WebP پذیرفته می‌شود.',
    'image_too_large': 'اندازهٔ تصویر باید کمتر از ۳ میگابایت باشد.',
    'too_many_uploads': 'شما در یک ساعت بیش از حد تصویر فرستاده‌اید. لطفاً بعداً دوباره کوشش کنید.',
}

log = logging.getLogger('panah.web')

_PARAM = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)(?::(int|str))?\}')
_DEVICE_ID = re.compile(r'^[A-Za-z0-9_-]{16,64}$')
_COOKIE_VALUE = re.compile(r'^[A-Za-z0-9!#$%&\'()*+\-./:<=>?@\[\]^_`{|}~]*$')
_BAD_SEGMENT = re.compile(r'[\x00-\x1f\\/:*?"<>|]')


# ---------------------------------------------------------------- routes

class Route:
    def __init__(self, method, pattern, handler, engine):
        method = str(method).upper()
        if method not in METHODS:
            raise ValueError('bad method %r' % (method,))
        if not isinstance(pattern, str) or not pattern.startswith('/'):
            raise ValueError('bad path %r' % (pattern,))
        if not callable(handler):
            raise ValueError('handler for %s %s is not callable' % (method, pattern))
        self.method, self.pattern, self.handler, self.engine = method, pattern.rstrip('/') or '/', handler, engine
        regex, self.ints, pos = '', set(), 0
        for match in _PARAM.finditer(self.pattern):
            regex += re.escape(self.pattern[pos:match.start()])
            name, kind = match.groups()
            regex += '(?P<%s>%s)' % (name, r'\d+' if kind == 'int' else '[^/]+')
            if kind == 'int':
                self.ints.add(name)
            pos = match.end()
        self.regex = re.compile('^' + regex + re.escape(self.pattern[pos:]) + '$')

    def match(self, path):
        found = self.regex.match(path)
        if found is None:
            return None
        params = {}
        for name, value in found.groupdict().items():
            value = unquote(value)
            params[name] = int(value) if name in self.ints else value
        return params


# ---------------------------------------------------------------- request

class Stream:
    """Response stream after req.stream(): write(text) writes and sends at once; client gone → OSError."""

    def __init__(self, wfile):
        self._wfile = wfile

    def write(self, text):
        self._wfile.write(text.encode('utf-8') if isinstance(text, str) else text)
        self._wfile.flush()


class Request:
    """What an engine's handler receives."""

    def __init__(self, app, handler, method, path, query, cookies, ip):
        self.app = app
        self.method = method
        self.path = path
        self.query = query
        self.params = {}
        self.body = {}
        self.headers = handler.headers
        self.cookies = cookies
        self.ip = ip
        self.user = None
        self.admin = None
        self.streamed = False
        self._handler = handler
        self._db = None
        self._out = []
        device = cookies.get(DEVICE_COOKIE, '')
        if not _DEVICE_ID.match(device):
            device = secrets.token_urlsafe(18)
            self._out.append(('Set-Cookie', _cookie(DEVICE_COOKIE, device, DEVICE_MAX_AGE)))
        self.device_id = device

    @property
    def db(self):
        """Database connection for this request (taken from the pool on first use)."""
        if self._db is None:
            self._db = self.app.pool.acquire()
        return self._db

    def set_session(self, token, max_age=SESSION_MAX_AGE):
        """Set the session cookie panah_session (HttpOnly, SameSite=Lax)."""
        token = str(token)
        if not token or not _COOKIE_VALUE.match(token):
            raise ValueError('session token has characters not allowed in a cookie')
        self._out.append(('Set-Cookie', _cookie(SESSION_COOKIE, token, int(max_age))))

    def clear_session(self):
        """Clear the session cookie (logout)."""
        self._out.append(('Set-Cookie', _cookie(SESSION_COOKIE, '', 0)))

    def add_header(self, name, value):
        self._out.append((str(name), str(value)))

    def require_user(self):
        """Resident or ApiError 401."""
        if not self.user:
            raise ApiError(401, 'login_required', MSG['login_required'])
        return self.user

    def require_admin(self):
        """Admin or ApiError 403."""
        if not self.admin:
            raise ApiError(403, 'admin_required', MSG['admin_required'])
        return self.admin

    def stream(self, content_type='text/event-stream'):
        """Start a streaming 200 response (SSE) → Stream. The database connection is released."""
        self.finish_db(True)
        handler = self._handler
        handler.send_response(200)
        handler.send_header('Content-Type', content_type)
        handler.send_header('Cache-Control', 'no-cache, no-transform')  # no-transform: proxies must not compress
        handler.send_header('X-Accel-Buffering', 'no')                    # nginx-style proxies: do not buffer
        handler.send_header('X-Content-Type-Options', 'nosniff')
        for name, value in self._out:
            handler.send_header(name, value)
        handler.end_headers()
        handler.wfile.flush()
        self.streamed = True
        return Stream(handler.wfile)

    def finish_db(self, ok):
        """commit (ok) or rollback, then return the request's connection to the pool (a broken one is closed)."""
        db, self._db = self._db, None
        if db is None:
            return
        pool = self.app.pool
        try:
            if ok:
                db.commit()
            else:
                db.rollback()
        except BaseException:
            pool.discard(db)
            raise
        pool.release(db)


def client_ip(peer, headers):
    """The client's address. Through cloudflared on this computer (peer = loopback) every request would come from
    127.0.0.1, so then the address from CF-Connecting-IP is used (Cloudflare sets it; a client cannot forge it there).
    From any other peer the header is ignored."""
    if peer.startswith('::ffff:'):
        peer = peer[7:]
    try:
        loopback = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        return peer
    forwarded = (headers.get('CF-Connecting-IP') or '').strip() if loopback else ''
    if forwarded:
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return peer


def _cookie(name, value, max_age):
    return '%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax' % (name, value, max_age)


def parse_cookies(header):
    """Cookie header → dict; malformed cookies of other apps on localhost do no harm."""
    cookies = {}
    for part in (header or '').split(';'):
        name, sep, value = part.strip().partition('=')
        if sep and name and name not in cookies:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            cookies[name] = value
    return cookies


def _json_default(value):
    if hasattr(value, 'keys') and hasattr(value, '__getitem__'):  # sqlite3.Row
        return {key: value[key] for key in value.keys()}
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, 'tzinfo') and hasattr(value, 'astimezone'):  # datetime
        return to_utc(value)
    raise TypeError('%r is not JSON serializable' % (value,))


def to_json_bytes(data):
    return json.dumps(data, ensure_ascii=False, default=_json_default).encode('utf-8')


# ---------------------------------------------------------------- application

class App:
    """The glue: engines, routes, database, file serving. One instance per server."""

    def __init__(self, db_path=None, web_dir=None, data_dir=None, engines=None, package='server', config=None,
                 hub=None):
        self.db_path = Path(db_path).resolve() if db_path else DEFAULT_DB
        self.data_dir = Path(data_dir).resolve() if data_dir else self.db_path.parent
        self.uploads_dir = self.data_dir / 'uploads'
        self.web_dir = Path(web_dir).resolve() if web_dir else WEB_DIR
        self.base_dir = BASE_DIR
        self.config = {'port': None, 'db_path': self.db_path, 'data_dir': self.data_dir, 'base_dir': BASE_DIR,
                       'usgs': True}
        self.config.setdefault('upload_limit', UPLOAD_LIMIT)
        self.config.setdefault('upload_window', UPLOAD_WINDOW)
        self.config.update(config or {})
        self.upload_limit = RateLimit(self.config['upload_limit'], self.config['upload_window'])
        self.pool = store.Pool(self.db_path, POOL_SIZE)
        self.hub = hub or live.HUB
        self.package = package
        self.engine_list = list(ENGINES if engines is None else engines)
        self.engines = {}   # name → module, in ENGINES order
        self.skipped = []   # the engine file does not exist yet
        self.failed = {}    # name → what broke
        self.routes = []
        self.who = None
        self.stopping = threading.Event()

    # --- database
    def connect(self):
        """New connection (for a background thread; close it yourself). In a request use req.db from the pool."""
        return store.connect(self.db_path)

    def transaction(self):
        """with app.transaction() as db: … — own connection, commits and closes by itself."""
        return store.transaction(self.db_path)

    # --- startup
    def setup(self):
        """Load the engines, call their init(db) in order, collect the routes."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._load_engines()
        db = self.connect()
        try:
            for name, module in list(self.engines.items()):
                init = getattr(module, 'init', None)
                if init is None:
                    continue
                try:
                    init(db)
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    self._fail(name, 'init', exc)
        finally:
            db.close()
        self._collect_routes()
        auth = self.engines.get('auth')
        self.who = getattr(auth, 'who', None) if auth is not None else None
        log.info('engines: %s; not built yet: %s; failed: %s', ', '.join(self.engines) or '-',
                 ', '.join(self.skipped) or '-', ', '.join(self.failed) or '-')
        return self

    def _load_engines(self):
        for item in self.engine_list:
            if isinstance(item, tuple):
                name, module = item
            else:
                name = item
                full = '%s.%s' % (self.package, name)
                try:
                    module = importlib.import_module(full)
                except ModuleNotFoundError as exc:
                    if exc.name == full:
                        self.skipped.append(name)
                        log.debug('engine %s: no file yet, skipped', name)
                        continue
                    self._fail(name, 'import', exc)
                    continue
                except Exception as exc:
                    self._fail(name, 'import', exc)
                    continue
            self.engines[name] = module

    def _fail(self, name, stage, exc):
        log.error('engine %s: %s failed, engine is off', name, stage, exc_info=exc)
        self.failed[name] = '%s: %s: %s' % (stage, type(exc).__name__, exc)
        self.engines.pop(name, None)

    def _collect_routes(self):
        own = [('GET', '/api/health', health), ('POST', '/api/upload', upload)]
        sources = [('web', lambda: own), ('live', live.routes)]
        sources += [(name, getattr(module, 'routes', None)) for name, module in self.engines.items()]
        taken = {}
        self.routes = []
        for name, get_routes in sources:
            if get_routes is None:
                continue
            try:
                found = [Route(method, path, handler, name) for method, path, handler in get_routes()]
            except Exception as exc:
                self._fail(name, 'routes', exc)
                continue
            for route in found:
                key = (route.method, _PARAM.sub(lambda m: '{int}' if m.group(2) == 'int' else '{}', route.pattern))
                if key in taken:
                    log.error('engine %s: route %s %s is already taken by %s, ignored', name, route.method,
                              route.pattern, taken[key])
                    continue
                taken[key] = name
                self.routes.append(route)

    def start(self):
        """Call the engines' start(app) (background tasks)."""
        for name, module in list(self.engines.items()):
            start = getattr(module, 'start', None)
            if start is None:
                continue
            if self.config.get(name) is False:
                log.info('engine %s: start skipped (off in config)', name)
                continue
            try:
                start(self)
            except Exception as exc:
                log.error('engine %s: start failed', name, exc_info=exc)
                self.failed[name] = 'start: %s: %s' % (type(exc).__name__, exc)

    def stop(self):
        """Shutdown: set app.stopping for background tasks, close the SSE streams and the pool connections."""
        self.stopping.set()
        self.hub.close_all()
        self.pool.close()

    # --- request
    def handle(self, handler, method):
        started = time.monotonic()
        target = handler.path
        if not target.startswith('/'):
            parts = urlsplit(target)
            target = (parts.path or '/') + ('?' + parts.query if parts.query else '')
        raw_path, _, raw_query = target.partition('?')
        raw_query = raw_query.split('#', 1)[0]
        ip = client_ip(handler.client_address[0] if handler.client_address else '', handler.headers)
        req = Request(self, handler, method, raw_path, dict(parse_qsl(raw_query, keep_blank_values=True)),
                      parse_cookies(handler.headers.get('Cookie', '')), ip)
        status = 500
        try:
            status = self._dispatch(req, handler, method, raw_path)
        except Exception:  # the very last resort: the response may already be sent
            log.exception('%s %s: unexpected error', method, raw_path)
        finally:
            try:
                req.finish_db(False)
            except Exception:
                log.exception('closing database failed')
        if raw_path.startswith('/api/'):
            log.info('%s %s -> %s (%.0f ms)', method, raw_path, status, (time.monotonic() - started) * 1000)
        else:
            log.debug('%s %s -> %s', method, raw_path, status)

    def _dispatch(self, req, handler, method, raw_path):
        route_path = raw_path.rstrip('/') or '/'
        route, allowed = None, []
        for candidate in self.routes:
            params = candidate.match(route_path)
            if params is None:
                continue
            if candidate.method == method:
                route, req.params = candidate, params
                break
            allowed.append(candidate.method)
        if route is None:
            if allowed:
                return self._send_json(req, 405, {'error': 'method_not_allowed', 'message': MSG['method_not_allowed']},
                                       [('Allow', ', '.join(sorted(set(allowed))))])
            if route_path == '/api' or route_path.startswith('/api/'):
                return self._send_json(req, 404, {'error': 'not_found', 'message': MSG['not_found']})
            if method not in ('GET', 'HEAD'):
                return self._send_json(req, 405, {'error': 'method_not_allowed', 'message': MSG['method_not_allowed']},
                                       [('Allow', 'GET, HEAD')])
            if raw_path == '/uploads' or raw_path.startswith('/uploads/'):
                return self._send_file(req, self.uploads_dir, raw_path[len('/uploads'):], IMAGE_EXT,
                                       'public, max-age=86400')
            return self._send_file(req, self.web_dir, raw_path, None, 'no-cache')
        try:
            req.body = self._read_body(handler, method)
            self._resolve_session(req)
            result = route.handler(req)
            if req.streamed:
                return 200
            status, data, headers = _normalize(result)
            req.finish_db(True)
            return self._send_json(req, status, data, headers)
        except ApiError as exc:
            if req.streamed:
                return 200
            try:
                req.finish_db(True)
            except Exception:
                log.exception('%s %s: commit after ApiError failed', method, raw_path)
            return self._send_json(req, exc.status, exc.to_dict())
        except Exception:
            log.exception('%s %s: engine %s failed', method, raw_path, route.engine)
            if req.streamed:
                return 200
            req.finish_db(False)
            return self._send_json(req, 500, {'error': 'server_error', 'message': MSG['server_error']})

    def _resolve_session(self, req):
        token = req.cookies.get(SESSION_COOKIE)
        if not token or self.who is None:
            return
        try:
            found = self.who(req.db, token) or {}
        except Exception:
            log.error('auth.who failed', exc_info=True)
            raise
        if isinstance(found, dict):
            req.user, req.admin = found.get('user'), found.get('admin')

    @staticmethod
    def _read_body(handler, method):
        length = handler.headers.get('Content-Length')
        if method in ('GET', 'HEAD') or not length:
            return {}
        try:
            size = int(length)
        except ValueError:
            raise ApiError(400, 'bad_request', MSG['bad_request'])
        if size < 0:
            raise ApiError(400, 'bad_request', MSG['bad_request'])
        if size > MAX_BODY:
            _discard(handler.rfile, size)
            raise ApiError(413, 'too_large', MSG['too_large'])
        if size == 0:
            return {}
        raw = handler.rfile.read(size)
        if len(raw) < size:
            raise ApiError(400, 'bad_request', MSG['bad_request'])
        try:
            data = json.loads(raw.decode('utf-8-sig'))
        except ValueError:  # and UnicodeDecodeError
            raise ApiError(400, 'bad_json', MSG['bad_json'])
        if not isinstance(data, dict):
            raise ApiError(400, 'bad_json', MSG['bad_json'])
        return data

    # --- responses
    def _send_json(self, req, status, data, headers=()):
        headers = list(headers.items()) if isinstance(headers, dict) else list(headers)
        if data is None:
            body, content_type = b'', None
        elif isinstance(data, (bytes, bytearray)):
            content_type = 'application/octet-stream'
            for name, value in headers:
                if name.lower() == 'content-type':
                    content_type = value
            headers = [(n, v) for n, v in headers if n.lower() != 'content-type']
            body = bytes(data)
        else:
            body, content_type = to_json_bytes(data), 'application/json; charset=utf-8'
        return self._send(req, status, body, content_type, headers, 'no-store')

    def _send(self, req, status, body, content_type, headers, cache):
        handler = req._handler
        try:
            handler.send_response(status)
            if content_type:
                handler.send_header('Content-Type', content_type)
            if status not in (204, 304):
                handler.send_header('Content-Length', str(len(body)))
            handler.send_header('Cache-Control', cache)
            handler.send_header('X-Content-Type-Options', 'nosniff')
            for name, value in list(headers) + req._out:
                handler.send_header(name, value)
            handler.end_headers()
            if body and req.method != 'HEAD' and status not in (204, 304):
                handler.wfile.write(body)
        except OSError:  # the client left before the response
            pass
        return status

    def _send_file(self, req, root, raw_path, allowed_ext, cache):
        path = safe_path(root, raw_path)
        if path is not None and path.is_dir():
            path = path / 'index.html'
        if path is None or not path.is_file() or (allowed_ext and path.suffix.lower() not in allowed_ext):
            return self._send(req, 404, b'Not found', 'text/plain; charset=utf-8', [], 'no-store')
        stat = path.stat()
        etag = '"%x-%x"' % (stat.st_mtime_ns, stat.st_size)
        content_type = MIME.get(path.suffix.lower(), 'application/octet-stream')
        if etag in (req.headers.get('If-None-Match') or ''):
            return self._send(req, 304, b'', None, [('ETag', etag)], cache)
        try:
            body = path.read_bytes()
        except OSError:
            return self._send(req, 404, b'Not found', 'text/plain; charset=utf-8', [], 'no-store')
        return self._send(req, 200, body, content_type, [('ETag', etag)], cache)


def _normalize(result):
    if isinstance(result, tuple):
        if len(result) == 2:
            return int(result[0]), result[1], {}
        if len(result) == 3:
            return int(result[0]), result[1], result[2] or {}
        raise TypeError('handler must return (code, dict) or (code, dict, headers), got %d items' % len(result))
    if result is None:
        return 204, None, {}
    return 200, result, {}


def _discard(stream, size):
    """Drain a too-large body (up to 64 MB) so the browser sees the 413 response instead of a dropped connection."""
    left = min(size, 64 * 1024 * 1024)
    while left > 0:
        chunk = stream.read(min(left, 65536))
        if not chunk:
            break
        left -= len(chunk)


def safe_path(root, raw_path):
    """Path from the URL (as received, with %XX) → a file inside root, or None. Escaping root is impossible."""
    parts = []
    for raw in raw_path.split('/'):
        if raw == '':
            continue
        segment = unquote(raw)
        if (segment in ('.', '..') or segment.startswith('.') or _BAD_SEGMENT.search(segment)
                or segment != segment.rstrip('. ')):
            return None
        parts.append(segment)
    try:
        base = Path(root).resolve()
        path = base.joinpath(*parts).resolve()
    except (OSError, ValueError):
        return None
    if path != base and base not in path.parents:
        return None
    return path


# ---------------------------------------------------------------- own routes

def health(req):
    """GET /api/health — the server is alive; which engines are running."""
    app = req.app
    return 200, {'ok': True, 'time': utcnow(), 'engines': list(app.engines), 'not_built': list(app.skipped),
                 'failed': dict(app.failed), 'live': app.hub.count(), 'port': app.config.get('port')}


def image_kind(data):
    """Image bytes → (extension, MIME) from the first bytes, or None."""
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png', 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return '.jpg', 'image/jpeg'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return '.webp', 'image/webp'
    return None


class RateLimit:
    """Sliding window: at most limit times per window seconds for each key (limit None or 0: no limit)."""

    def __init__(self, limit, window, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, float(window), clock
        self._lock = threading.Lock()
        self._hits = {}  # key → deque of moments

    def take(self, keys):
        """Take one use for all keys at once → (True, ticket) or (False, seconds until it is allowed)."""
        keys = tuple(keys)
        now = self.clock()
        with self._lock:
            if not self.limit:
                return True, (keys, None)
            wait = 0.0
            for key in keys:
                hits = self._hits.get(key)
                while hits and hits[0] <= now - self.window:
                    hits.popleft()
                if hits is not None and not hits:
                    del self._hits[key]
                elif hits is not None and len(hits) >= self.limit:
                    wait = max(wait, hits[0] + self.window - now)
            if wait > 0:
                return False, max(1, int(math.ceil(wait)))
            for key in keys:
                self._hits.setdefault(key, collections.deque()).append(now)
            return True, (keys, now)

    def release(self, ticket):
        """Give back a taken use (a failed upload does not count toward the limit)."""
        keys, moment = ticket
        if moment is None:
            return
        with self._lock:
            for key in keys:
                hits = self._hits.get(key)
                if hits and moment in hits:
                    hits.remove(moment)


def upload(req):
    """POST /api/upload {data_url, name} → a PNG/JPG/WebP image up to 3 MB into data/uploads/ → {url, type, size}.

    Guests may upload too (photos in SOS), but at most config['upload_limit'] (20) successful uploads per
    config['upload_window'] (an hour) per device and per IP; beyond that, 429 {retry_after} and a Retry-After header.
    Admin: no limit.
    """
    if req.admin:
        return _save_upload(req)
    ok, ticket = req.app.upload_limit.take(('device:' + req.device_id, 'ip:' + req.ip))
    if not ok:
        req.add_header('Retry-After', str(ticket))
        log.warning('upload refused: limit reached (ip %s)', req.ip)
        raise ApiError(429, 'too_many_uploads', MSG['too_many_uploads'], retry_after=ticket)
    try:
        return _save_upload(req)
    except BaseException:
        req.app.upload_limit.release(ticket)
        raise


def _save_upload(req):
    data_url = req.body.get('data_url')
    if not isinstance(data_url, str) or not data_url.strip():
        raise ApiError(400, 'no_image', MSG['no_image'])
    head, comma, payload = data_url.strip().partition(',')
    if not comma:
        head, payload = '', head
    if head and not (head.lower().startswith('data:') and ';base64' in head.lower()):
        raise ApiError(400, 'bad_image', MSG['bad_image'])
    payload = ''.join(payload.split())
    if len(payload) > MAX_IMAGE * 4 // 3 + 4:
        raise ApiError(413, 'image_too_large', MSG['image_too_large'])
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(400, 'bad_image', MSG['bad_image'])
    if len(raw) > MAX_IMAGE:
        raise ApiError(413, 'image_too_large', MSG['image_too_large'])
    kind = image_kind(raw)
    if kind is None:
        raise ApiError(400, 'bad_image', MSG['bad_image'])
    ext, mime = kind
    folder = req.app.uploads_dir
    folder.mkdir(parents=True, exist_ok=True)
    name = '%s-%s%s' % (utcnow()[:10].replace('-', ''), secrets.token_hex(8), ext)
    part = folder / (name + '.part')
    part.write_bytes(raw)
    os.replace(part, folder / name)
    log.info('upload %s (%d bytes, %s)', name, len(raw), mime)
    return 200, {'url': '/uploads/' + name, 'type': mime, 'size': len(raw)}


# ---------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    server_version = 'PANAH/1.0'
    sys_version = ''
    protocol_version = 'HTTP/1.0'
    timeout = 120

    def do_GET(self):
        self.server.app.handle(self, 'GET')

    def do_HEAD(self):
        self.server.app.handle(self, 'HEAD')

    def do_POST(self):
        self.server.app.handle(self, 'POST')

    def do_PUT(self):
        self.server.app.handle(self, 'PUT')

    def do_PATCH(self):
        self.server.app.handle(self, 'PATCH')

    def do_DELETE(self):
        self.server.app.handle(self, 'DELETE')

    def version_string(self):
        return self.server_version

    def log_message(self, format, *args):
        log.debug('%s %s', self.client_address[0] if self.client_address else '-', format % args)


class Server(ThreadingHTTPServer):
    """ThreadingHTTPServer: a thread per request (daemon); shutdown does not wait for open SSE streams."""

    daemon_threads = True
    block_on_close = False
    allow_reuse_address = os.name != 'nt'  # on Windows SO_REUSEADDR would allow taking a port already in use
    request_queue_size = 1024  # backlog of waiting connections: at 64 with 100–200 users, retries after 1–7 s

    def __init__(self, address, handler_class, app, family=socket.AF_INET):
        self.address_family = family
        self.app = app
        super().__init__(address, handler_class)

    def server_bind(self):
        if self.address_family == socket.AF_INET6:
            try:  # one socket for IPv6 and IPv4: localhost answers at once however the browser resolves it
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except (AttributeError, OSError):
                pass
        if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)  # no getfqdn: it can be slow on Windows
        self.server_name, self.server_port = self.server_address[:2]

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return
        log.error('connection from %s failed', client_address[0] if client_address else '-',
                  exc_info=sys.exc_info())


_PORT_BUSY = {errno.EADDRINUSE, errno.EACCES, 10048, 10013}


def bind(app, host='0.0.0.0', port=8000, tries=20):
    """Take a port (busy → the next one, up to tries times; port=0 → any free one) → Server, not started yet.

    host '0.0.0.0' (or '', '::') means all addresses: both localhost and a phone in the same network.
    """
    if host in ('', '0.0.0.0', '::'):
        families = [(socket.AF_INET6, '::'), (socket.AF_INET, '0.0.0.0')] if socket.has_ipv6 else \
            [(socket.AF_INET, '0.0.0.0')]
    elif ':' in host:
        families = [(socket.AF_INET6, host)]
    else:
        families = [(socket.AF_INET, host)]
    ports = [0] if not port else range(int(port), int(port) + max(1, int(tries)))
    last = None
    for candidate in ports:
        for family, address in families:
            try:
                server = Server((address, candidate), Handler, app, family)
            except OSError as exc:
                last = exc
                if exc.errno in _PORT_BUSY or getattr(exc, 'winerror', None) in (10048, 10013):
                    break  # port busy: next port
                continue  # address family not supported: try the next one
            app.config['port'] = server.server_address[1]
            return server
    raise last if last else OSError('no free port')


def serve_in_thread(server):
    """Run serve_forever in a background thread (for tests) → the thread."""
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.1}, daemon=True,
                              name='panah-web')
    thread.start()
    return thread
