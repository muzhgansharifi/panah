"""PANAH test helpers: a server on a free port in a thread, a client with cookies, an SSE reader, a fake auth.

    from support import Running, fake_auth
    with Running(engines=[('auth', fake_auth())]) as srv:
        client = srv.client()
        r = client.post('/api/test/login', {'token': 'tok-user-1'})
        r.status, r.json(), client.cookies

Importing `support` adds app/ to sys.path; after that `from server import ...` works.
"""
import http.client
import json
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from server import web  # noqa: E402


class Response:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def json(self):
        return json.loads(self.body.decode('utf-8'))

    @property
    def text(self):
        return self.body.decode('utf-8')

    def cookies(self):
        """Set-Cookie of this response: name → the whole header line."""
        found = {}
        for line in self.headers.get_all('Set-Cookie') or []:
            found[line.split('=', 1)[0].strip()] = line
        return found


class Client:
    """HTTP client with cookies, like a browser on one device."""

    def __init__(self, port, host='127.0.0.1'):
        self.port, self.host, self.cookies = port, host, {}

    def _cookie_header(self):
        return '; '.join('%s=%s' % item for item in self.cookies.items())

    def _remember(self, headers):
        for line in headers.get_all('Set-Cookie') or []:
            first, *attrs = [part.strip() for part in line.split(';')]
            name, _, value = first.partition('=')
            if any(a.lower() == 'max-age=0' for a in attrs):
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value

    def request(self, method, path, body=None, headers=None, raw=None, timeout=10):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            send = dict(headers or {})
            if self.cookies:
                send.setdefault('Cookie', self._cookie_header())
            data = raw
            if body is not None:
                data = json.dumps(body, ensure_ascii=False).encode('utf-8')
                send.setdefault('Content-Type', 'application/json')
            conn.request(method, path, body=data, headers=send)
            resp = conn.getresponse()
            result = Response(resp.status, resp.msg, resp.read())
        finally:
            conn.close()
        self._remember(result.headers)
        return result

    def get(self, path, **kw):
        return self.request('GET', path, **kw)

    def post(self, path, body=None, **kw):
        return self.request('POST', path, body if body is not None else {}, **kw)

    def stream(self, path='/api/stream', headers=None, timeout=5):
        """Open an SSE stream → SSE (the ': connected' greeting is already read)."""
        send = dict(headers or {})
        if self.cookies:
            send.setdefault('Cookie', self._cookie_header())
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        conn.request('GET', path, headers=send)
        resp = conn.getresponse()
        self._remember(resp.msg)
        sse = SSE(conn, resp)
        hello = sse.next()
        if 'connected' not in hello.get('comments', []):
            raise AssertionError('no greeting in stream: %r' % (hello,))
        return sse


class SSE:
    """Reads a text/event-stream stream block by block."""

    def __init__(self, conn, resp):
        self.conn, self.resp = conn, resp
        self.status, self.headers = resp.status, resp.msg

    def next(self):
        """Next block: {'id', 'event', 'data' (parsed JSON), 'retry', 'comments': [...]}."""
        block = {'comments': []}
        while True:
            line = self.resp.readline()
            if not line:
                raise EOFError('stream closed')
            line = line.decode('utf-8').rstrip('\r\n')
            if line == '':
                if len(block) > 1 or block['comments']:
                    if 'data' in block:
                        block['data'] = json.loads(block['data'])
                    return block
                continue
            if line.startswith(':'):
                block['comments'].append(line[1:].strip())
                continue
            name, _, value = line.partition(':')
            value = value[1:] if value.startswith(' ') else value
            block[name] = block[name] + '\n' + value if name == 'data' and 'data' in block else value

    def events_until(self, marker):
        """Events (without heartbeats) up to and including the event named marker → [(event, data), ...]."""
        found = []
        while True:
            block = self.next()
            if 'event' not in block:
                continue
            found.append((block['event'], block.get('data')))
            if block['event'] == marker:
                return found

    def close(self):
        try:
            self.resp.close()
        finally:
            self.conn.close()


class Running:
    """PANAH server in a thread on 127.0.0.1 and a free port; the database is in a temporary folder."""

    def __init__(self, engines=(), web_dir=None, config=None, package='server'):
        self.engines, self.web_dir, self.config, self.package = list(engines), web_dir, config, package

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp = Path(self._tmp.name)
        web_dir = self.web_dir or (self.tmp / 'web')
        Path(web_dir).mkdir(parents=True, exist_ok=True)
        self.app = web.App(self.tmp / 'data' / 'panah.db', web_dir=web_dir, engines=self.engines,
                           package=self.package, config=self.config)
        self.app.setup()
        self.server = web.bind(self.app, '127.0.0.1', 0)
        self.port = self.server.server_address[1]
        self.app.start()
        self.thread = web.serve_in_thread(self.server)
        return self

    def __exit__(self, *exc):
        self.app.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self._tmp.cleanup()
        return False

    def client(self):
        return Client(self.port)

    def wait_listeners(self, count, timeout=5):
        """Wait until count streams are connected to /api/stream."""
        deadline = time.monotonic() + timeout
        while self.app.hub.count() < count:
            if time.monotonic() > deadline:
                raise AssertionError('listeners: %d, wanted %d' % (self.app.hub.count(), count))
            time.sleep(0.01)


def fake_auth():
    """Fake auth engine: who by token; /api/test/login|logout|me to check the session."""
    mod = types.ModuleType('fake_auth')
    people = {
        'tok-user-1': {'user': {'id': 1, 'name': 'احمد'}, 'admin': None},
        'tok-user-2': {'user': {'id': 2, 'name': 'فاطمه'}, 'admin': None},
        'tok-admin': {'user': None, 'admin': {'id': 1, 'username': 'admin'}},
    }
    mod.calls = []

    def who(db, token):
        mod.calls.append(token)
        db.execute('SELECT 1').fetchone()  # db is a real connection
        return people.get(token, {'user': None, 'admin': None})

    def login(req):
        req.set_session(req.body['token'])
        return 200, {'ok': True}

    def logout(req):
        req.clear_session()
        return 200, {'ok': True}

    def me(req):
        return 200, {'user': req.user, 'admin': req.admin, 'device_id': req.device_id, 'ip': req.ip}

    mod.who = who
    mod.routes = lambda: [('POST', '/api/test/login', login), ('POST', '/api/test/logout', logout),
                          ('GET', '/api/test/me', me)]
    return mod


def wait_until(check, timeout=5):
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def run_in_thread(target, *args):
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
    return thread
