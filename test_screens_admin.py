"""Tests for the admin screen group (figma 14–18, dashboard, event list, rescuer chat).

Run from the project root:  python -m unittest app.tests.test_screens_admin   (or: python -m unittest discover app/tests)
Python standard library only.
  - Without a browser: screens and routes (via node; no node: skipped), fa/ps/en dictionaries, keys from admin.js are in
    the dictionaries, CSS only logical and only our own (.adm…), the typo "عیور" is fixed.
  - In headless Google Chrome (no Chrome or PANAH_SKIP_BROWSER=1: skipped): a live PANAH server in this process
    (all engines, temporary database, USGS off, Nominatim faked: only the map tiles need the network), no windows on
    screen. Admin login (error, lockout, login by code) → dashboard → a new event with a point on the map and
    an image → it is in the list and in the resident's GET /api/alerts → end it; a number and a video: add, edit,
    delete; a resident's SOS (via the API) shows up in 18 by itself → chat → the reply goes to the resident, whose
    location and messages arrive by themselves → close; 360 and 440 px without horizontal scroll; English and پښتو;
    no console errors. Screenshots 440×956 (×2, like the Figma frames): stand/screens/admin/NN-<name>.png; the
    Chrome profile is in stand/, deleted afterwards.
"""
import base64
import http.client
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'app'
WEB = APP / 'web'
STAND = ROOT / 'stand'
SHOTS = STAND / 'screens' / 'admin'
LANGS = ('fa', 'ps', 'en')
OWN = ['js/screens/admin.js', 'css/admin.css', 'i18n/fa/admin.json', 'i18n/ps/admin.json', 'i18n/en/admin.json']
ROUTES = {
    'admin-login': '#/admin/login', 'admin-dashboard': '#/admin', 'admin-events': '#/admin/events',
    'admin-event-new': '#/admin/events/new', 'admin-numbers': '#/admin/numbers', 'admin-videos': '#/admin/videos',
    'admin-messages': '#/admin/messages', 'admin-chat': '#/admin/chat/:id',
}
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def read(rel):
    return (WEB / rel).read_text(encoding='utf-8')


def dicts(group=None):
    """{lang: dict}: the shared dictionary or a group dictionary."""
    name = (lambda lang: f'i18n/{lang}/{group}.json') if group else (lambda lang: f'i18n/{lang}.json')
    return {lang: json.loads(read(name(lang))) for lang in LANGS}


# ====================================================================== without a browser
class TestAdminFiles(unittest.TestCase):
    def test_own_files(self):
        for rel in OWN:
            self.assertTrue((WEB / rel).is_file(), rel)
        self.assertGreater(len(read('js/screens/admin.js')), 20000, 'admin.js is almost empty')
        index = read('index.html')
        self.assertIn('css/admin.css', index)
        self.assertIn('js/screens/admin.js', index)

    def test_youtube_preview_16x9(self):
        js = read('js/screens/admin.js')
        self.assertIn('/mqdefault.jpg', js)
        self.assertNotIn('hqdefault', js)

    def test_typo_fixed(self):
        """The 14 picture has "رمز عیور"; we use "رمز عبور" everywhere."""
        for rel in OWN:
            self.assertNotIn('عیور', read(rel), rel)
        self.assertEqual(dicts()['fa']['admin.login.password'], 'رمز عبور')
        self.assertIn('رمز عبور', dicts('admin')['fa']['admin.login.forgotA'])

    def test_css_logical_and_scoped(self):
        text = re.sub(r'/\*.*?\*/', '', read('css/admin.css'), flags=re.S)
        bad = re.compile(r'(?<![\w-])(margin|padding|border)-(left|right)\b|(?<![\w-])(left|right)\s*:|'
                         r'text-align\s*:\s*(left|right)|float\s*:\s*(left|right)|border-(top|bottom)-(left|right)-radius')
        self.assertEqual([m.group(0) for m in bad.finditer(text)], [], 'logical properties only')
        for head in re.findall(r'([^{}]+)\{', text):
            if head.strip().startswith('@'):
                continue
            for sel in head.split(','):
                self.assertIn('.adm', sel, f'a selector without .adm… affects other screens: {sel.strip()}')

    @unittest.skipUnless(shutil.which('node'), 'no node')
    def test_routes_registered(self):
        """Screens and routes per the contract; the core and ui.js load in node without a DOM."""
        script = r"""
          globalThis.window = globalThis;
          const path = require('path'), web = process.argv[1];
          for (const f of ['js/core.js', 'js/ui.js', 'js/screens/admin.js']) require(path.join(web, f));
          process.stdout.write(JSON.stringify(App.screens.map((s) => [s.name, s.route, s.nav, s.auth, s.theme || null])));
        """
        r = subprocess.run(['node', '-e', script, str(WEB)], capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = {name: (route, nav, auth, theme) for name, route, nav, auth, theme in json.loads(r.stdout)}
        for name, route in ROUTES.items():
            self.assertIn(name, got)
            self.assertEqual(got[name][0], route)
        self.assertEqual(got['admin-login'][1:], (None, None, 'admin'), 'login: no menu, admin orange')
        for name in ('admin-dashboard', 'admin-events', 'admin-event-new', 'admin-numbers', 'admin-videos', 'admin-messages'):
            self.assertEqual(got[name][1:3], ('admin', 'admin'), name)
        self.assertEqual(got['admin-chat'][1:3], (None, 'admin'), 'chat: no menu (reply field at the bottom), admin only')


class TestAdminDicts(unittest.TestCase):
    def test_same_keys_filled_placeholders(self):
        d = dicts('admin')
        keys = set(d['fa'])
        self.assertGreater(len(keys), 100)
        ph = lambda s: set(re.findall(r'\{(\w+)\}', s))  # noqa: E731
        for lang in LANGS:
            self.assertEqual(set(d[lang]), keys, f'i18n/{lang}/admin.json: keys differ from fa')
            for k, v in d[lang].items():
                self.assertTrue(isinstance(v, str) and v.strip(), f'{lang}:{k} is empty')
                self.assertEqual(ph(v), ph(d['fa'][k]), f'{lang}:{k}: placeholders differ from Dari')

    def test_own_keys_do_not_replace_common(self):
        common = dicts()['fa']
        self.assertEqual(sorted(set(dicts('admin')['fa']) & set(common)), [], 'our own strings use new keys')

    def test_keys_used_in_js_exist(self):
        js = read('js/screens/admin.js')
        have = set(dicts()['fa']) | set(dicts('admin')['fa'])
        prefixes = ('admin', 'common', 'nav', 'sos', 'alert', 'health', 'auth', 'device', 'fmt', 'loc', 'error')
        used = set(re.findall(r"'((?:%s)\.[\w.]+)'" % '|'.join(prefixes), js)) - {'admin.seen', 'admin.messages'}  # a storage key and the number on the tab
        self.assertGreater(len(used), 100)
        missing = sorted(k for k in used if (k.endswith('.') and not any(h.startswith(k) for h in have))
                         or (not k.endswith('.') and k not in have))
        self.assertEqual(missing, [], 'keys missing from the dictionaries')
        for k in ('admin.events.source.admin', 'admin.events.source.usgs', 'admin.dash.usgsOn', 'admin.dash.usgsOff',
                  'admin.dash.usgsError', 'admin.sms.kind.otp', 'admin.sms.kind.alert', 'admin.sms.kind.sos',
                  'admin.sms.kind.other', 'alert.type.flood', 'alert.type.earthquake', 'device.signal.strong'):
            self.assertIn(k, have)


# ====================================================================== headless Chrome: our own mini CDP client
def find_chrome():
    if os.environ.get('PANAH_CHROME'):
        return os.environ['PANAH_CHROME']
    for name in ('google-chrome-stable', 'google-chrome', 'chromium', 'chromium-browser', 'chrome'):
        if shutil.which(name):
            return shutil.which(name)
    for base in (os.environ.get('PROGRAMFILES'), os.environ.get('PROGRAMFILES(X86)'), os.environ.get('LOCALAPPDATA')):
        if base:
            p = Path(base) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe'
            if p.is_file():
                return str(p)
    return None


class WS:
    """WebSocket client for CDP (RFC 6455, only what is needed)."""

    def __init__(self, url, timeout=60):
        u = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f'GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n'
                           f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        self.buf = bytearray()
        while b'\r\n\r\n' not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError('websocket handshake')
            self.buf += chunk
        head, _, rest = bytes(self.buf).partition(b'\r\n\r\n')
        if b' 101 ' not in head.split(b'\r\n')[0]:
            raise ConnectionError(head.decode(errors='replace'))
        self.buf = bytearray(rest)

    def _frame(self, op, data):
        hdr = bytearray([0x80 | op])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack('>H', n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack('>Q', n)
        mask = os.urandom(4)
        self.sock.sendall(bytes(hdr) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def send(self, text):
        self._frame(0x1, text.encode('utf-8'))

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 20)
            if not chunk:
                raise ConnectionError('websocket closed')
            self.buf += chunk
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def recv(self):
        parts = []
        while True:
            b1, b2 = self._read(2)
            op, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack('>H', self._read(2))[0]
            elif n == 127:
                n = struct.unpack('>Q', self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 0x8:
                raise ConnectionError('websocket closed by peer')
            if op == 0x9:
                self._frame(0xA, data)
                continue
            if op in (0x0, 0x1, 0x2):
                parts.append(data)
                if b1 & 0x80:
                    return b''.join(parts).decode('utf-8')

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class CDP:
    def __init__(self, ws):
        self.ws, self.n, self.events = ws, 0, []

    def call(self, method, params=None, timeout=60):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({'id': mid, 'method': method, 'params': params or {}}))
        end = time.time() + timeout
        while time.time() < end:
            msg = json.loads(self.ws.recv())
            if msg.get('id') == mid:
                if 'error' in msg:
                    raise RuntimeError(f'{method}: {msg["error"]}')
                return msg.get('result', {})
            if 'method' in msg:
                self.events.append(msg)
        raise TimeoutError(method)

    def eval(self, expr, wait=False):
        r = self.call('Runtime.evaluate', {'expression': expr, 'returnByValue': True, 'awaitPromise': wait})
        if 'exceptionDetails' in r:
            raise RuntimeError(json.dumps(r['exceptionDetails'], ensure_ascii=False)[:800])
        return r['result'].get('value')

    def wait_for(self, expr, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            if self.eval(expr):
                return True
            time.sleep(0.15)
        return False

    def errors(self, ignore=()):
        out = []
        for e in self.events:
            m, p = e['method'], e.get('params', {})
            if m == 'Runtime.exceptionThrown':
                d = p.get('exceptionDetails', {})
                out.append('exception: ' + (d.get('exception', {}).get('description') or d.get('text', '')))
            elif m == 'Runtime.consoleAPICalled' and p.get('type') in ('error', 'assert'):
                out.append('console.error: ' + ' '.join(str(a.get('value', a.get('description', ''))) for a in p.get('args', [])))
            elif m == 'Log.entryAdded' and p.get('entry', {}).get('level') == 'error':
                en = p['entry']
                out.append(f"log: {en.get('text', '')} {en.get('url', '')}")
        return [x for x in out if not any(s in x for s in ignore)]


# width overflow: remove the overflow-x safety net and measure the real width; plus the culprits
OVERFLOW_JS = r"""(() => {
  document.body.style.overflowX = 'visible';
  document.querySelectorAll('.app').forEach((a) => { a.style.overflowX = 'visible'; });
  const W = document.documentElement.clientWidth, bad = [];
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('.leaflet-container') || el.closest('.toasts')) continue;
    const r = el.getBoundingClientRect();
    if (r.width && (r.right > W + 1 || r.left < -1)) bad.push((typeof el.className === 'string' ? el.className : el.tagName) + ' ' + Math.round(r.left) + '..' + Math.round(r.right));
  }
  const res = { sw: document.documentElement.scrollWidth, w: W, bad: bad.slice(0, 8) };
  document.body.style.overflowX = '';
  document.querySelectorAll('.app').forEach((a) => { a.style.overflowX = ''; });
  return res;
})()"""
NET_NOISE = ('tile.openstreetmap.org', 'img.youtube.com')  # internet: map tiles and YouTube previews are not screen errors


def tiny_png(path):
    """An 8×8 (orange) image to check "افزودن تصویر"."""
    raw = b''.join(b'\x00' + b'\xff\x6b\x00' * 8 for _ in range(8))
    chunk = lambda kind, data: struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))  # noqa: E731
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 8, 8, 8, 2, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))
    return path


def fake_nominatim(url, timeout=None):
    """Fake geo.fetch_json: Nominatim without the network and without loading it."""
    if '/search?' in url:
        return [{'lat': '34.5154398', 'lon': '69.1338400', 'name': 'پوهنتون کابل',
                 'display_name': 'پوهنتون کابل, جاده علی‌آباد, ناحیه سوم, کابل, 1006, افغانستان',
                 'address': {'amenity': 'پوهنتون کابل', 'road': 'جاده علی‌آباد', 'city_district': 'ناحیه سوم',
                             'city': 'کابل', 'country': 'افغانستان'}}]
    if '/reverse?' in url and 'lat=30.5000' in url:   # southern Helmand desert: Nominatim knows nothing, no safe place within 5 km
        return {'error': 'Unable to geocode'}
    if '/reverse?' in url:
        return {'display_name': 'جاده علی‌آباد, ناحیه سوم, کابل, افغانستان',
                'address': {'road': 'جاده علی‌آباد', 'suburb': 'ناحیه سوم', 'city': 'کابل', 'country': 'افغانستان'}}
    raise OSError('no network in tests')


class Api:
    """HTTP client with cookies: a resident (or guest) with their own device."""

    def __init__(self, port):
        self.port, self.cookies = port, {}

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=15)
        headers = {'Accept': 'application/json'}
        if self.cookies:
            headers['Cookie'] = '; '.join(f'{k}={v}' for k, v in self.cookies.items())
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        try:
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            for line in resp.msg.get_all('Set-Cookie') or []:
                name, _, value = line.split(';')[0].partition('=')
                self.cookies[name.strip()] = value
        finally:
            conn.close()
        return resp.status, (json.loads(raw.decode('utf-8')) if raw else {})

    def ok(self, method, path, body=None):
        status, data = self.call(method, path, body)
        if status >= 300:
            raise AssertionError(f'{method} {path} → {status} {data}')
        return data

    def sign_in(self, phone):
        code = self.ok('POST', '/api/auth/code', {'phone': phone})['test_code']
        return self.ok('POST', '/api/auth/verify', {'phone': phone, 'code': code})['user']


LAT_UNI, LON_UNI = 34.5154398, 69.13384           # پوهنتون کابل (faked address search)


@unittest.skipIf(os.environ.get('PANAH_SKIP_BROWSER'), 'PANAH_SKIP_BROWSER')
@unittest.skipUnless(find_chrome(), 'no Google Chrome')
class TestAdminInBrowser(unittest.TestCase):
    """Steps in order (unittest sorts test_1…test_7): each continues where the previous one stopped."""

    @classmethod
    def setUpClass(cls):
        from server import geo, web
        cls.geo, cls.real_fetch = geo, geo.fetch_json
        geo.fetch_json = fake_nominatim
        geo.reset_network_state()
        cls.tmp = Path(tempfile.mkdtemp(prefix='panah-admin-test-'))
        cls.app = web.App(cls.tmp / 'panah.db', config={'usgs': False})
        cls.app.setup()
        from server import alerts
        with cls.app.transaction() as db:   # USGS history, like the first feed poll: "past" right away, not sent out
            alerts.add_past(db, {'type': 'earthquake', 'lat': 36.46, 'lon': 70.77, 'place': '47 km SSW of Jurm, Afghanistan',
                                 'magnitude': 4.2, 'depth_km': 199.3, 'starts_at': '2026-11-04T18:54:49Z',
                                 'source_id': 'us-test-history'}, 'usgs')
        cls.server = web.bind(cls.app, '127.0.0.1', 0)
        cls.port = cls.server.server_address[1]
        cls.app.start()
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={'poll_interval': 0.1}, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.port}/'
        # resident "احمد یوسفی" near Kabul University, inside the 2 km radius of the new alert
        cls.ahmad = Api(cls.port)
        cls.ahmad_user = cls.ahmad.sign_in('0700111222')
        cls.ahmad.ok('PUT', '/api/me', {'name': 'احمد یوسفی', 'blood_group': 'O+', 'health': ['diabetes'],
                                        'contacts': [{'name': 'پدر', 'phone': '0700111333'}]})
        cls.ahmad.ok('POST', '/api/me/location', {'lat': 34.5150, 'lon': 69.1350})
        SHOTS.mkdir(parents=True, exist_ok=True)
        cls.profile = STAND / f'chrome-admin-{os.getpid()}'
        cls.proc = subprocess.Popen(
            [find_chrome(), '--headless=new', f'--user-data-dir={cls.profile}', '--remote-debugging-port=0',
             '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--hide-scrollbars',
             '--disable-background-networking', '--lang=fa', 'about:blank'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = cls.profile / 'DevToolsActivePort'
        end = time.time() + 30
        while not (port_file.is_file() and port_file.read_text().strip()) and time.time() < end:
            time.sleep(0.1)
        port = int(port_file.read_text().split()[0])
        pages = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=10))
        cls.ws = WS(next(p['webSocketDebuggerUrl'] for p in pages if p.get('type') == 'page'))
        cls.c = CDP(cls.ws)
        for m in ('Runtime.enable', 'Log.enable', 'Page.enable', 'DOM.enable'):
            cls.c.call(m)
        cls.size(440)
        cls.c.call('Page.navigate', {'url': cls.base + '#/admin'})

    @classmethod
    def tearDownClass(cls):
        try:
            cls.c.call('Browser.close', timeout=5)
        except Exception:
            pass
        cls.ws.close()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        for _ in range(20):  # Windows holds the profile files a bit longer
            shutil.rmtree(cls.profile, ignore_errors=True)
            if not cls.profile.exists():
                break
            time.sleep(0.3)
        cls.app.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(5)
        cls.geo.fetch_json = cls.real_fetch
        cls.geo.reset_network_state()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ helpers
    @classmethod
    def size(cls, width, height=956):
        cls.c.call('Emulation.setDeviceMetricsOverride', {'width': width, 'height': height, 'deviceScaleFactor': 2, 'mobile': True})

    def js(self, expr):
        return self.c.eval(expr)

    def until(self, expr, msg=None, timeout=20):
        self.assertTrue(self.c.wait_for(expr, timeout), msg or f'timed out waiting for: {expr}')

    def go(self, route, screen):
        self.js(f'location.hash = {json.dumps(route)}; true')
        self.until(f'(document.querySelector(".screen") || {{dataset: {{}}}}).dataset.screen === {json.dumps(screen)}'
                   f' && !document.querySelector(".screen > .loading")', f'no screen {screen}')

    def click(self, sel):
        self.until(f'!!document.querySelector({json.dumps(sel)})', f'no {sel}')
        self.js(f'document.querySelector({json.dumps(sel)}).click(); true')

    def fill(self, sel, value):
        self.until(f'!!document.querySelector({json.dumps(sel)})', f'no {sel}')
        self.js(f'''(() => {{ const i = document.querySelector({json.dumps(sel)}); i.value = {json.dumps(value)};
                  i.dispatchEvent(new Event('input', {{bubbles: true}})); i.dispatchEvent(new Event('change', {{bubbles: true}})); return true; }})()''')

    def text(self, sel='.screen'):
        return self.js(f'(document.querySelector({json.dumps(sel)}) || {{}}).innerText || ""')

    def mouse_click(self, sel, dx=0.5, dy=0.5):
        """A real mouse click (Leaflet map: a point by coordinates)."""
        self.js(f'document.querySelector({json.dumps(sel)}).scrollIntoView({{block: "center"}}); true')
        time.sleep(0.4)
        r = self.js(f'(() => {{ const b = document.querySelector({json.dumps(sel)}).getBoundingClientRect(); return [b.left, b.top, b.width, b.height]; }})()')
        x, y = r[0] + r[2] * dx, r[1] + r[3] * dy
        for kind in ('mouseMoved', 'mousePressed', 'mouseReleased'):
            self.c.call('Input.dispatchMouseEvent', {'type': kind, 'x': x, 'y': y, 'button': 'left', 'clickCount': 1})
            time.sleep(0.05)

    def toast(self):
        return self.js('[...document.querySelectorAll(".toast")].map((t) => t.textContent).join(" | ")')

    def wait_tiles(self):
        self.c.wait_for('[...document.querySelectorAll(".leaflet-tile")].every((i) => i.complete)', timeout=8)

    def shot(self, name, toasts=False):
        """Screenshot 440×956 (×2), like a Figma frame; old toasts are removed (except those wanted in the shot)."""
        self.size(440)
        if not toasts:
            self.js('document.querySelectorAll(".toast").forEach((t) => t.remove()); true')
        self.js('document.fonts.ready.then(() => true)')
        self.wait_tiles()
        time.sleep(0.6)
        data = self.c.call('Page.captureScreenshot', {'format': 'png'})['data']
        (SHOTS / name).write_bytes(base64.b64decode(data))

    def widths(self, where):
        for w in (360, 440):
            self.size(w)
            time.sleep(0.35)
            r = self.js(OVERFLOW_JS)
            self.assertLessEqual(r['sw'], w, f'{where}: horizontal scroll at {w}: {r}')
        self.size(440)

    def clean_console(self, ignore=()):
        self.assertEqual(self.c.errors(ignore=NET_NOISE + tuple(ignore)), [], 'console errors')
        self.c.events.clear()

    def sms_code(self, username):
        """Latest sign-in code SMS to this admin's phone — from sms_outbox (test mode: also in data/sms.log)."""
        with self.app.transaction() as db:
            phone = db.execute('SELECT phone FROM admins WHERE username = ?', (username,)).fetchone()[0]
            row = db.execute("SELECT text FROM sms_outbox WHERE kind = 'otp' AND phone = ? ORDER BY id DESC LIMIT 1",
                             (phone,)).fetchone()
        self.assertIsNotNone(row, 'no OTP SMS in sms_outbox')
        found = re.search(r'\b\d{6}\b', row[0])
        self.assertIsNotNone(found, f'no 6-digit code in the SMS: {row[0]!r}')
        return found.group(0)

    def publish_case(self, case_id, place):
        """As the server does (the place label is computed in the background): place into the database and 'sos_case' again with the same id."""
        from server import live
        cls = type(self)
        if not getattr(cls, 'admin_api', None):
            cls.admin_api = Api(self.port)
            cls.admin_api.ok('POST', '/api/admin/login', {'username': 'admin', 'password': 'panah1234'})
        with self.app.transaction() as db:
            db.execute('UPDATE sos_cases SET place_name = ? WHERE id = ?', (place, case_id))
        card = [x for x in cls.admin_api.ok('GET', '/api/admin/sos?status=all')['cases'] if x['case']['id'] == case_id][0]
        self.assertEqual(card['place_name'], place)
        live.publish('sos_case', dict(card, is_new=True), admins=True)

    def sheet_ok(self):
        self.until('!!document.querySelector(".sheet [data-ok]")', 'no "are you sure?" sheet')
        self.js('document.querySelector(".sheet [data-ok]").click(); true')
        self.until('!document.querySelector(".sheet")')

    # ------------------------------------------------------------------ 14 · login
    def test_1_login(self):
        c = self.c
        self.until('location.hash.startsWith("#/admin/login?next=") && !!document.querySelector(".adm-login__title")',
                   '#/admin without login does not lead to login')
        self.assertEqual(self.text('.adm-login__title'), 'ورود مدیر')
        page = self.text()
        for s in ('لطفاً برای دسترسی به پنل مدیریت وارد شوید', 'ورود', 'یا', 'ورود با رمز یکبار مصرف',
                  'رمز عبور خود را', 'فراموش کرده‌اید؟'):
            self.assertIn(s, page)
        self.assertNotIn('عیور', page)
        self.assertEqual(self.js('document.querySelector("[name=password]").placeholder'), 'رمز عبور')
        self.assertTrue(self.js('document.querySelector(".app").classList.contains("theme-admin")'))
        self.assertEqual(self.js('getComputedStyle(document.querySelector(".adm-login__submit")).backgroundColor'), 'rgb(212, 64, 12)')
        self.assertFalse(self.js('!!document.querySelector(".tabbar")'), 'no menu on the login screen')
        self.widths('14')
        self.shot('14-admin-login.png')
        self.clean_console(ignore=('/api/me',))  # before login GET /api/me → 401: that is how the core learns "nobody is logged in"
        # the eye shows the password
        self.fill('[name=username]', 'admin')
        self.fill('[name=password]', 'wrong-1')
        self.click('[data-eye]')
        self.assertEqual(self.js('document.querySelector("[name=password]").type'), 'text')
        # wrong password → a clear error and how many attempts are left
        self.click('.adm-login__submit')
        self.until('!document.querySelector(".adm-login__error").hidden')
        self.assertIn('نادرست', self.text('.adm-login__error'))
        self.assertIn('۴', self.text('.adm-login__error'))
        # "forgot password?": a toast
        self.click('[data-forgot]')
        self.until('document.body.innerText.includes("با مسئول سیستم تماس بگیرید")')
        # 5 wrong in a row → 429 locked: a text with a countdown, the button disabled
        for i in range(4):
            self.until('!document.querySelector(".adm-login__submit").disabled')
            self.fill('[name=password]', f'wrong-{i + 2}')
            self.click('.adm-login__submit')
            time.sleep(0.4)
        self.until('document.querySelector(".adm-login__error").innerText.includes("بسته است")', 'no lockout text')
        self.assertTrue(self.js('document.querySelector(".adm-login__submit").disabled'))
        self.assertRegex(self.text('.adm-login__error'), '[۰-۹]:[۰-۹]{2}')
        self.clean_console(ignore=('/api/admin/login',))  # 400 and 429 are the expected responses to a wrong password
        # login by one-time code (lifts the lockout): the code is NOT on the screen (knowing the username must not be
        # enough), only the test-mode hint; the test reads the code where an admin would — the SMS outbox
        self.click('[data-otp]')
        self.until('!!document.querySelector("[data-form=otp-send]")')
        self.assertEqual(self.js('document.querySelector("[data-form=otp-send] [name=username]").value'), 'admin')
        self.click('[data-form=otp-send] [type=submit]')
        self.until('!document.querySelector("[data-test]").hidden', 'no test-mode hint')
        hint = self.text('[data-test]')
        self.assertIn('کد به تلفن مدیر فرستاده شد', hint)
        self.assertIn('data/sms.log', hint)
        self.assertTrue(self.js('document.querySelector("[data-sent]").hidden'))
        latin = self.sms_code('admin')
        self.assertNotIn(latin, self.text())
        self.assertNotRegex(self.text(), '[0-9۰-۹]{6}', 'a code is visible on the screen')
        self.shot('14-admin-login-otp.png')
        self.fill('[name=code]', '000000' if latin != '000000' else '111111')
        self.click('[data-form=otp-verify] [type=submit]')
        self.until('!document.querySelector("[data-form=otp-verify] .adm-login__error").hidden')
        self.fill('[name=code]', latin.translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')))   # Dari digits work too
        self.click('[data-form=otp-verify] [type=submit]')
        self.until('location.hash === "#/admin" && document.querySelector(".screen").dataset.screen === "admin-dashboard"',
                   'no dashboard after login by code')
        self.clean_console(ignore=('/api/admin/otp/verify',))  # 400 for a wrong code is expected

    # ------------------------------------------------------------------ dashboard
    def test_2_dashboard(self):
        self.until('document.querySelectorAll(".adm-stat").length === 4', 'no dashboard numbers')
        self.assertEqual(self.js('document.querySelector(".tabbar--admin .is-on").dataset.tab'), 'dashboard')
        self.assertEqual(self.js('document.querySelectorAll(".tabbar--admin .tabbar__item").length'), 5)
        self.assertEqual(self.text('[data-stat=users] .adm-stat__num'), '۱')
        self.assertEqual(self.text('[data-stat=alert] .adm-stat__num'), '۰')
        self.assertTrue(self.js('!!document.querySelector(".adm-dash__map.leaflet-container")'), 'no map')
        sms = self.text('[data-sms]')
        self.assertIn('حالت آزمایشی', sms)
        self.assertGreaterEqual(self.js('document.querySelectorAll(".adm-sms__item").length'), 2, 'login code SMS not visible')
        self.assertIn('کد ورود', sms)
        self.assertIn('خاموش', self.text('[data-usgs]'))   # USGS polling is off in the test
        self.widths('dashboard')
        self.shot('14a-admin-dashboard.png')
        # the language switcher (as on 02) at the bottom of the dashboard: پښتو → English → دری
        self.assertTrue(self.js('document.querySelector(".screen").lastElementChild.classList.contains("langs")'), 'switcher not at the bottom of the dashboard')
        self.click('.screen .langs [data-lang="ps"]')
        self.until('document.documentElement.lang === "ps-AF" && document.querySelector(".page-title h1").textContent === "د مدیریت پینل"', 'پښتو did not switch on')
        self.click('.screen .langs [data-lang="en"]')
        self.until('document.documentElement.dir === "ltr" && document.querySelector(".page-title h1").textContent === "Admin panel"', 'English did not switch on')
        self.assertEqual(self.js('document.querySelector(".langs .is-on").dataset.lang'), 'en')
        self.click('.screen .langs [data-lang="fa"]')
        self.until('document.documentElement.dir === "rtl" && document.querySelector(".page-title h1").textContent === "پنل مدیریت"', 'دری did not come back')
        # logout and a normal password login (the code login lifted the lockout)
        self.click('[data-logout]')
        self.until('location.hash === "#/admin/login" && !!document.querySelector(".adm-login__submit")')
        self.fill('[name=username]', 'admin')
        self.fill('[name=password]', 'panah1234')
        self.click('.adm-login__submit')
        self.until('location.hash === "#/admin" && document.querySelectorAll(".adm-stat").length === 4', 'password login')
        self.clean_console(ignore=('/api/me',))

    # ------------------------------------------------------------------ 15 · new event → list → at the resident → end
    def test_3_event(self):
        self.go('#/admin/events/new', 'admin-event-new')
        self.assertEqual(self.js('document.querySelector(".tabbar--admin .is-on").dataset.tab'), 'events')
        page = self.text()
        for s in ('افزودن رویداد اضطراری', 'افزودن تصویر (اختیاری)', 'نوع رویداد', 'زمان تقریبی شروع', 'موقعیت',
                  'محل امن برای تخلیه', 'انتشار رویداد'):
            self.assertIn(s, page)
        self.widths('15')
        self.js('scrollTo(0, 0); true')
        self.shot('15-admin-add-event.png')
        # the "انتشار رویداد" button is fully visible above the menu (in Figma it is under the menu): at the page bottom and when scrolled to
        gap = ('document.querySelector(".tabbar--admin").getBoundingClientRect().top'
               ' - document.querySelector(".adm-new__publish").getBoundingClientRect().bottom')
        self.js('scrollTo(0, document.documentElement.scrollHeight); true')
        time.sleep(0.3)
        self.assertGreaterEqual(self.js(gap), 0, 'at the page bottom the button is under the menu')
        self.js('document.querySelector(".adm-new__publish").scrollIntoView({block: "end"}); true')
        time.sleep(0.3)
        self.assertGreaterEqual(self.js(gap), 0, 'scrolling to the button hides it under the menu')
        # empty form → 4 errors under the fields
        self.click('.adm-new__publish')
        self.until('document.querySelectorAll(".adm-new .field.is-invalid").length === 4', 'no empty-form errors')
        # type, time "همین حالا", address by search, point by a map click, safe place, image
        self.fill('#adm-type', 'flood')
        self.click('[data-time]')
        self.click('.sheet [data-ago="0"]')
        self.until('document.querySelector("[data-time]").classList.contains("has-value")')
        self.assertRegex(self.text('[data-time-text]'), 'حمل|ثور|جوزا|سرطان|اسد|سنبله|میزان|عقرب|قوس|جدی|دلو|حوت')
        self.fill('#adm-place', 'پوهنتون کابل')
        self.click('[data-search]')
        self.until('!!document.querySelector(".adm-result")', 'no address search results')
        self.click('.adm-result')
        self.until('document.querySelector("[data-point]").classList.contains("is-set")')
        found_at = self.text('[data-point]')
        self.mouse_click('.adm-new__map', 0.3, 0.75)        # a map click away from the marker (~300 m from the found point)
        self.until(f'document.querySelector("[data-point]").innerText !== {json.dumps(found_at)}', 'no point picked from the map')
        self.assertRegex(self.text('[data-point]'), '۳۴٫۵۱')
        self.until('document.querySelector("#adm-place").value === "جاده علی‌آباد، ناحیه سوم، کابل"', 'no place name for the point')
        self.until('document.querySelectorAll("[data-place]").length >= 1', 'no nearest safe places')
        self.click('[data-place]')
        self.until('document.querySelector("#adm-safe").value.length > 0 && !document.querySelector("[data-safe-point]").hidden')
        png = tiny_png(self.tmp / 'event.png')
        obj = self.c.call('Runtime.evaluate', {'expression': 'document.querySelector("[data-file]")'})['result']['objectId']
        self.c.call('DOM.setFileInputFiles', {'files': [str(png)], 'objectId': obj})
        self.until('document.querySelector("[data-image]").classList.contains("has-image")', 'image did not upload')
        self.js('document.querySelector(".adm-new__publish").scrollIntoView({block: "end"}); true')
        self.shot('15-admin-add-event-filled.png')
        self.click('.adm-new__publish')
        self.until('location.hash.startsWith("#/admin/events?new=") && !!document.querySelector(".adm-event.is-new")', 'no new event in the list')
        self.assertIn('به ۱ نفر اطلاع داده شد، ۱ پیامک', self.toast())
        card = self.text('.adm-event.is-new')
        self.assertIn('به ۱ نفر اطلاع داده شد، ۱ پیامک', card)
        self.assertIn('هشدار سیلاب', card)
        self.assertIn('فعال', card)
        alert_id = int(self.js('location.hash.split("new=")[1]'))
        # at the resident: an active alert nearby, with the image and a safe place
        r = self.ahmad.ok('GET', '/api/alerts')
        mine = [a for a in r['active'] if a['id'] == alert_id]
        self.assertEqual(len(mine), 1, 'the resident does not have the alert')
        a = mine[0]
        self.assertEqual((a['type'], a['source'], a['inside']), ('flood', 'admin', True))
        self.assertTrue(a['image'].startswith('/uploads/'))
        self.assertTrue(a['safe_place'])
        self.assertAlmostEqual(a['lat'], LAT_UNI, delta=0.01)
        self.widths('event list')
        self.js('scrollTo(0, 0); true')
        self.shot('15a-admin-events.png', toasts=True)
        # end it
        self.click('.adm-event.is-new [data-end]')
        self.sheet_ok()
        self.until(f'!document.querySelector(\'.adm-event[data-id="{alert_id}"]\').classList.contains("is-active")', 'the event did not end')
        r = self.ahmad.ok('GET', '/api/alerts')
        self.assertNotIn(alert_id, [x['id'] for x in r['active']])
        self.assertIn(alert_id, [x['id'] for x in r['past']])
        self.assertIn('پایان: ', self.text(f'.adm-event[data-id="{alert_id}"]'))
        hist = self.js('[...document.querySelectorAll(".adm-event")].filter((e) => e.innerText.includes("Jurm")).map((e) => e.innerText)')
        self.assertEqual(len(hist), 1, 'no USGS history in the list')
        self.assertIn('USGS — تاریخچه', hist[0])
        self.assertIn('زمان رویداد: ۱۳ عقرب ۱۴۰۵، ۲۳:۲۴', hist[0])   # the quake time (Kabul), not the load time
        self.assertNotIn('پایان', hist[0])
        self.clean_console()

    # ------------------------------------------------------------------ 16 · numbers: add, edit, delete
    def test_4_numbers(self):
        self.go('#/admin/numbers', 'admin-numbers')
        self.assertEqual(self.js('document.querySelector(".tabbar--admin .is-on").dataset.tab'), 'numbers')
        self.until('document.querySelectorAll(".adm-num").length >= 2')
        self.assertIn('مدیریت شماره‌های اضطراری', self.text())
        self.assertIn('۱۱۹', self.text('.adm-list'))
        self.widths('16')
        self.shot('16-admin-numbers.png')
        before = len(self.ahmad.ok('GET', '/api/numbers')['numbers'])
        self.click('#adm-add-number')
        self.fill('.sheet [name=number]', 'abc')
        self.fill('.sheet [name=title]', 'آزمایش')
        self.click('.sheet [type=submit]')
        self.until('!!document.querySelector(".sheet .field.is-invalid")', 'an invalid number was accepted')
        self.fill('.sheet [name=number]', '۱۱۳')
        self.fill('.sheet [name=title_ps]', 'ازموینه')
        self.click('.sheet [type=submit]')
        self.until('!document.querySelector(".sheet") && document.querySelector(".adm-list").innerText.includes("۱۱۳")', 'number not added')
        nums = self.ahmad.ok('GET', '/api/numbers')['numbers']
        self.assertEqual(len(nums), before + 1)
        new = [n for n in nums if n['number'] == '113'][0]
        self.assertEqual((new['title'], new['title_ps']), ('آزمایش', 'ازموینه'))
        self.click(f'[data-edit="{new["id"]}"]')
        self.until('document.querySelector(".sheet [name=number]").value === "113"')
        self.fill('.sheet [name=title]', 'آزمایش دوم')
        self.click('.sheet [type=submit]')
        self.until('document.querySelector(".adm-list").innerText.includes("آزمایش دوم")', 'number not changed')
        self.assertEqual([n['title'] for n in self.ahmad.ok('GET', '/api/numbers')['numbers'] if n['id'] == new['id']], ['آزمایش دوم'])
        self.click(f'[data-del="{new["id"]}"]')
        self.sheet_ok()
        self.until(f'!document.querySelector(\'.adm-num[data-id="{new["id"]}"]\')', 'number not deleted')
        self.assertNotIn(new['id'], [n['id'] for n in self.ahmad.ok('GET', '/api/numbers')['numbers']])
        self.clean_console()

    # ------------------------------------------------------------------ 17 · videos: add, edit, delete
    def test_5_videos(self):
        self.go('#/admin/videos', 'admin-videos')
        self.assertEqual(self.js('document.querySelector(".tabbar--admin .is-on").dataset.tab'), 'videos')
        self.until('document.querySelectorAll(".adm-video").length >= 4')
        page = self.text()
        self.assertIn('مدیریت ویدیوها', page)
        self.assertIn('ویدیوهای آموزشی را اضافه، ویرایش یا حذف کنید.', page)
        self.assertIn('چگونه در هنگام سیلاب پناه بگیریم', page)
        self.assertRegex(self.text('.adm-video__date'), '[۰-۹]+ (حمل|ثور|جوزا|سرطان|اسد|سنبله|میزان|عقرب|قوس|جدی|دلو|حوت) [۰-۹]{4}')
        self.assertEqual(self.js('document.querySelectorAll(\'.adm-video__thumb img:not([src*="/mqdefault.jpg"])\').length'), 0,
                         'YouTube preview is not 16:9 (black bars)')
        self.widths('17')
        self.shot('17-admin-videos.png')
        self.click('#adm-add-video')
        self.fill('.sheet [name=title]', 'ویدیوی آزمایشی')
        self.fill('.sheet [name=url]', 'not a link')
        self.click('.sheet [type=submit]')
        self.until('!!document.querySelector(".sheet .field.is-invalid")', 'an invalid link was accepted')
        self.fill('.sheet [name=url]', 'https://youtu.be/AbCdEfGhIjK')
        self.click('.sheet [type=submit]')
        self.until('!document.querySelector(".sheet") && document.querySelector(".adm-list").innerText.includes("ویدیوی آزمایشی")', 'video not added')
        vid = [v for v in self.ahmad.ok('GET', '/api/videos')['videos'] if v['title'] == 'ویدیوی آزمایشی'][0]
        self.assertEqual(vid['youtube_id'], 'AbCdEfGhIjK')
        self.assertTrue(self.js(f'document.querySelector(\'.adm-video[data-id="{vid["id"]}"] .adm-video__thumb\').getAttribute("href") === "https://www.youtube.com/watch?v=AbCdEfGhIjK"'))
        self.click(f'[data-edit="{vid["id"]}"]')
        self.fill('.sheet [name=title_ps]', 'ازمېښتي ویډیو')
        self.click('.sheet [type=submit]')
        self.until('!document.querySelector(".sheet")')
        self.assertEqual([v['title_ps'] for v in self.ahmad.ok('GET', '/api/videos')['videos'] if v['id'] == vid['id']], ['ازمېښتي ویډیو'])
        self.click(f'[data-del="{vid["id"]}"]')
        self.sheet_ok()
        self.until(f'!document.querySelector(\'.adm-video[data-id="{vid["id"]}"]\')', 'video not deleted')
        self.assertNotIn(vid['id'], [v['id'] for v in self.ahmad.ok('GET', '/api/videos')['videos']])
        self.clean_console()

    # ------------------------------------------------------------------ 18 · cases: show up by themselves → chat → close
    def test_6_messages_chat(self):
        sos = self.ahmad.ok('POST', '/api/sos', {'kind': 'default', 'lat': 34.5160, 'lon': 69.1345, 'battery': 67, 'signal': 'strong'})
        case_id = sos['case']['id']
        self.go('#/admin/messages', 'admin-messages')
        self.assertEqual(self.js('document.querySelector(".tabbar--admin .is-on").dataset.tab'), 'messages')
        self.until('document.querySelectorAll(".adm-sos").length === 1')
        card = self.text('.adm-sos')
        for s in ('SOS پیش‌فرض', 'احمد یوسفی', 'من در وضعیت اضطراری قرار دارم', 'پاسخ دهید'):
            self.assertIn(s, card)
        self.until('document.querySelector(".tabbar--admin [data-tab=messages] .tabbar__badge").textContent === "۱"', 'no number on the tab')
        # a guest sends a message: the card shows up by itself (live), newest on top
        guest = Api(self.port)
        guest.ok('POST', '/api/sos', {'kind': 'custom', 'text': 'من در گیر مانده‌ام اما زخمی نیستم.', 'lat': 34.4649, 'lon': 69.1200})
        self.until('document.querySelectorAll(".adm-sos").length === 2', 'the new case did not show up by itself')
        first = self.text('.adm-sos')
        for s in ('سفارشی', 'ناشناس', 'من در گیر مانده‌ام اما زخمی نیستم.', 'مشاهده و پاسخ'):
            self.assertIn(s, first)
        self.until('document.querySelector(".tabbar--admin [data-tab=messages] .tabbar__badge").textContent === "۲"')
        overlap = self.js('''[...document.querySelectorAll(".adm-sos")].some((c) => {
            const b = c.querySelector(".adm-sos__btn").getBoundingClientRect();
            return [...c.querySelectorAll(".adm-sos__text, .adm-sos__place")].some((p) => { const r = p.getBoundingClientRect();
              return b.left < r.right && r.left < b.right && b.top < r.bottom && r.top < b.bottom; }); })''')
        self.assertFalse(overlap, 'the button covers the text')
        self.widths('18')
        self.shot('18-admin-messages.png')
        # 'sos_case' twice with one id: without the place label (computed in the background), then with it; the same card
        far = Api(self.port).ok('POST', '/api/sos', {'kind': 'custom', 'text': 'کمک', 'lat': 30.5, 'lon': 63.5})['case']
        self.assertIsNone(far['place_name'])
        one = f'.adm-sos[data-case="{far["id"]}"]'
        self.until(f'!!document.querySelector(\'{one} .adm-sos__place\')', 'the new case did not show up')
        self.assertIn('۳۰٫۵۰۰۰°N', self.text(one + ' .adm-sos__place'))   # no label yet: coordinates
        self.publish_case(far['id'], 'دیشو، ولایت هلمند')
        self.until(f'document.querySelector(\'{one} .adm-sos__place\').innerText === "دیشو، ولایت هلمند"', 'place label not updated')
        self.assertEqual(self.js(f'document.querySelectorAll(\'{one}\').length'), 1, 'a second case instead of an update')
        self.assertEqual(self.js('document.querySelectorAll(".adm-sos").length'), 3)
        # rescuer chat
        self.click(f'.adm-sos[data-case="{case_id}"] .adm-sos__btn')
        self.until(f'location.hash === "#/admin/chat/{case_id}" && document.querySelectorAll(".adm-msg").length >= 2', 'chat did not open')
        page = self.text()
        for s in ('احمد یوسفی', 'O+', 'دیابت', 'پدر', 'من در وضعیت اضطراری قرار دارم', 'باتری', 'قوی'):
            self.assertIn(s, page)
        self.assertTrue(self.js('!!document.querySelector(\'a[href="tel:+93700111222"]\')'), 'no call link to the resident')
        self.assertTrue(self.js('!!document.querySelector(".adm-chat__map.leaflet-container")'), 'no map')
        self.until('document.querySelector(".tabbar--admin") === null')
        # the reply goes to the resident
        self.fill('.chatbar__input', 'تیم نجات در راه است')
        self.js('document.querySelector(".chatbar").requestSubmit(); true')
        self.until('[...document.querySelectorAll(".adm-msg--team")].some((m) => m.innerText.includes("تیم نجات در راه است"))', 'reply not shown')
        msgs = self.ahmad.ok('GET', f'/api/sos/{case_id}/messages')['messages']
        self.assertIn(('rescuer', 'تیم نجات در راه است'), [(m['sender'], m['text']) for m in msgs])
        # the resident's location and message arrive by themselves
        self.ahmad.ok('POST', f'/api/sos/{case_id}/location', {'lat': 34.5200, 'lon': 69.1400, 'battery': 60, 'signal': 'medium'})
        self.until('document.querySelector(".adm-where").innerText.includes("۳۴٫۵۲۰۰")', 'location not updated')
        self.assertIn('متوسط', self.text('.adm-where'))
        self.assertGreaterEqual(self.js('document.querySelectorAll(".adm-chat__map path.leaflet-interactive").length'), 1, 'no track on the map')
        self.ahmad.ok('POST', f'/api/sos/{case_id}/messages', {'text': 'کجا بروم؟'})
        self.until('[...document.querySelectorAll(".adm-msg--user")].some((m) => m.innerText.includes("کجا بروم؟"))', "the resident's message did not arrive")
        # the resident's photo (paperclip): the message text is the /uploads/… path; the rescuer sees an image, enlarged on tap
        png = tiny_png(self.tmp / 'photo.png')
        url = self.ahmad.ok('POST', '/api/upload', {'data_url': 'data:image/png;base64,' + base64.b64encode(png.read_bytes()).decode(),
                                                   'name': 'photo.png'})['url']
        self.ahmad.ok('POST', f'/api/sos/{case_id}/messages', {'text': url})
        self.until(f'!!document.querySelector(\'.adm-msg--photo img[src="{url}"]\')', "the resident's photo is not shown as an image")
        self.assertNotIn(url, self.text('.adm-chat__list'))
        self.click('.adm-photo')
        self.until('!!document.querySelector(".sheet .adm-zoom")', 'photo did not open enlarged')
        self.js('document.querySelector(".sheet .sheet__head [data-sheet-close]").click(); true')
        self.until('!document.querySelector(".sheet")')
        self.js('scrollTo(0, 0); true')
        self.widths('chat')
        self.shot('18a-admin-chat.png')
        # chat header: the place label by id; a repeat without a label does not erase the previous one
        place0 = self.text('[data-head-place]')
        self.assertTrue(place0)
        msgs0 = self.js('document.querySelectorAll(".adm-msg").length')
        self.publish_case(case_id, None)
        time.sleep(0.8)
        self.assertEqual(self.text('[data-head-place]'), place0)
        self.publish_case(case_id, 'کارته سه، کابل')
        self.until('document.querySelector("[data-head-place]").innerText.includes("کارته سه، کابل")'
                   ' && document.querySelector(".adm-where__place").innerText.includes("کارته سه، کابل")', 'place label in the chat not updated')
        self.assertEqual(self.js('document.querySelectorAll(".adm-msg").length'), msgs0)
        # close the پرونده
        self.click('[data-close]')
        self.sheet_ok()
        self.until('!!document.querySelector(".adm-chat__closed") && !document.querySelector(".chatbar")', 'the case did not close')
        self.assertIn('بسته‌شده', self.text('.adm-chat__sub'))
        self.assertIsNone(self.ahmad.ok('GET', '/api/sos/current')['case'])
        self.go('#/admin/messages?status=closed', 'admin-messages')
        card = f'.adm-sos[data-case="{case_id}"]'
        self.until(f'!!document.querySelector(\'{card}\')', 'the closed case is not in "بسته‌شده"')
        self.assertIn('بسته شد', self.text(card))
        self.assertEqual(self.text(card + ' .adm-sos__btn'), 'مشاهده')
        self.assertNotIn('پاسخ', self.text(card))
        self.click(card + ' .adm-sos__btn')
        self.until(f'location.hash === "#/admin/chat/{case_id}" && !!document.querySelector(".adm-chat__closed")', 'the closed case chat did not open')
        self.assertFalse(self.js('!!document.querySelector(".chatbar")'), 'the closed case has a reply field')
        self.clean_console()

    # ------------------------------------------------------------------ English and پښتو
    def test_7_languages(self):
        c = self.c
        for lang, d in (('en', 'ltr'), ('ps', 'rtl')):
            c.eval(f'App.setLang("{lang}").then(() => true)', wait=True)
            self.assertEqual(self.js('document.documentElement.dir'), d)
            for route, screen in (('#/admin', 'admin-dashboard'), ('#/admin/events/new', 'admin-event-new'),
                                  ('#/admin/numbers', 'admin-numbers'), ('#/admin/videos', 'admin-videos'),
                                  ('#/admin/messages?status=closed', 'admin-messages'), ('#/admin/events', 'admin-events')):
                self.go(route, screen)
                time.sleep(0.3)
                self.widths(f'{lang} {route}')
            missing = self.js('[...App.i18n.missing].filter((k) => k.startsWith("admin."))')
            self.assertEqual(missing, [], f'{lang}: strings missing')
        self.assertIn('Manage videos', self.js('Object.values(App.i18n.dicts.en).join(" ")'))
        c.eval('App.setLang("fa").then(() => true)', wait=True)
        self.clean_console()


if __name__ == '__main__':
    unittest.main()
