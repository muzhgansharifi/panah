"""Tests for the ui-core engine (app/web): files, dictionaries, links, App.phone/App.fmt (via node), the look in headless Chrome.

Run from the project root:  python -m unittest app.tests.test_ui_core   (or discover app/tests)
Python standard library only. No node: the format checks are skipped; no Chrome: the browser check is skipped.
The browser check saves a screenshot to stand/ui-core-440.png; its own temporary Chrome profile is in stand/, deleted afterwards.
"""
import base64
import functools
import html.parser
import http.cookiejar
import http.server
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'app' / 'web'
STAND = ROOT / 'stand'
LANGS = ('fa', 'ps', 'en')
GROUPS = ('auth', 'home', 'sos', 'admin')
ISO = re.compile('[⁦-⁩]')

REQUIRED = [
    'index.html', 'README.md', 'css/base.css', 'css/components.css', 'js/core.js', 'js/ui.js',
    'i18n/fa.json', 'i18n/ps.json', 'i18n/en.json', 'dev/components.html', 'dev/components.js', 'img/icon.svg',
    'vendor/vazirmatn/Vazirmatn-Variable.woff2', 'vendor/vazirmatn/OFL.txt',
    'vendor/leaflet/leaflet.js', 'vendor/leaflet/leaflet.css', 'vendor/leaflet/LICENSE',
]


def load_dicts():
    return {lang: json.loads((WEB / 'i18n' / f'{lang}.json').read_text(encoding='utf-8')) for lang in LANGS}


class RefParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs = []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k in ('src', 'href') and v:
                self.refs.append((tag, v))


class TestFiles(unittest.TestCase):
    def test_required_files(self):
        missing = [f for f in REQUIRED if not (WEB / f).is_file()]
        self.assertEqual(missing, [], 'files missing')

    def test_index_head(self):
        text = (WEB / 'index.html').read_text(encoding='utf-8')
        self.assertIn('<html lang="fa-AF" dir="rtl">', text)
        self.assertRegex(text, r'<meta name="viewport" content="width=device-width')
        self.assertIn('name="theme-color"', text)
        self.assertIn('App.start()', text)

    def test_brand_panah(self):
        """The official name: پناه (PANAH) in the title, brand color (no old name: test_name.py)."""
        text = (WEB / 'index.html').read_text(encoding='utf-8')
        self.assertIn('<title>پناه — ', text)
        self.assertIn('<meta name="theme-color" content="#1F2430">', text)  # ink of the palette, as the V3 logo square
        for svg in ('img/icon.svg', 'img/logo.svg'):
            body = (WEB / svg).read_text(encoding='utf-8')
            self.assertIn('PANAH', body, svg)
            self.assertIn('پناه', body, svg)
            self.assertIn('@font-face', body, svg + ': font must be embedded (works as <img> and favicon)')
        self.assertTrue((WEB / 'img' / 'logo-panah-1024.png').is_file())

    def test_css_logical_only(self):
        """Logical properties only: no margin-left, padding-right, left:, text-align: left…"""
        bad = re.compile(r'(?<![\w-])(margin|padding|border)-(left|right)\b|(?<![\w-])(left|right)\s*:|'
                         r'text-align\s*:\s*(left|right)|float\s*:\s*(left|right)|border-(top|bottom)-(left|right)-radius')
        for css in ['css/base.css', 'css/components.css']:
            text = re.sub(r'/\*.*?\*/', '', (WEB / css).read_text(encoding='utf-8'), flags=re.S)
            hits = [m.group(0) for m in bad.finditer(text)]
            self.assertEqual(hits, [], css)


class TestI18n(unittest.TestCase):
    def test_same_keys_valid_json(self):
        d = load_dicts()
        keys = set(d['fa'])
        self.assertGreater(len(keys), 100)
        for lang in LANGS:
            self.assertEqual(set(d[lang]), keys, f'{lang}.json: keys do not match fa.json')
            for k, v in d[lang].items():
                self.assertIsInstance(v, str, f'{lang}:{k}')

    def test_fa_complete_and_placeholders(self):
        d = load_dicts()
        ph = lambda s: set(re.findall(r'\{(\w+)\}', s))
        for k, v in d['fa'].items():
            self.assertTrue(v.strip(), f'fa:{k} is empty, Dari is the main language')
            for lang in ('ps', 'en'):
                if d[lang][k]:
                    self.assertEqual(ph(d[lang][k]), ph(v), f'{lang}:{k}: placeholders differ from Dari')

    def test_brand_strings(self):
        """App name and motto in every language (no old name anywhere: test_name.py)."""
        d = load_dicts()
        self.assertEqual([d[l]['app.name'] for l in LANGS], ['پناه', 'پناه', 'PANAH'])
        self.assertEqual({d[l]['app.tagline'] for l in LANGS}, {'A warning today, a protection tomorrow'})

    def test_months_and_nav(self):
        d = load_dicts()
        self.assertEqual([d['fa'][f'month.{i}'] for i in range(1, 13)],
                         ['حمل', 'ثور', 'جوزا', 'سرطان', 'اسد', 'سنبله', 'میزان', 'عقرب', 'قوس', 'جدی', 'دلو', 'حوت'])
        self.assertEqual([d['fa'][k] for k in ('nav.home', 'nav.videos', 'nav.call', 'nav.message')],
                         ['صفحه اصلی', 'ویدیوها', 'زنگ', 'مسج'])
        self.assertEqual([d['fa']['nav.admin.' + k] for k in ('dashboard', 'events', 'numbers', 'videos', 'messages')],
                         ['داشبورد', 'رویدادها', 'شماره‌ها', 'ویدیوها', 'پیام‌ها'])


class TestGroupDicts(unittest.TestCase):
    """Group dictionaries i18n/<lang>/<group>.json: valid JSON {key: string}, the same keys in fa, ps, en."""

    def test_group_files_valid_same_keys(self):
        found = {f.stem for lang in LANGS for f in (WEB / 'i18n' / lang).glob('*.json')}
        for g in sorted(found | set(GROUPS)):
            data = {}
            for lang in LANGS:
                f = WEB / 'i18n' / lang / f'{g}.json'
                self.assertTrue(f.is_file(), f'missing i18n/{lang}/{g}.json')
                try:
                    d = json.loads(f.read_text(encoding='utf-8'))
                except ValueError as e:
                    self.fail(f'i18n/{lang}/{g}.json: invalid JSON: {e}')
                self.assertIsInstance(d, dict, f'i18n/{lang}/{g}.json: an object {{key: string}} is needed')
                for k, v in d.items():
                    self.assertIsInstance(v, str, f'i18n/{lang}/{g}.json: {k}')
                data[lang] = d
            for lang in ('ps', 'en'):
                self.assertEqual(sorted(data[lang]), sorted(data['fa']), f'i18n/{lang}/{g}.json: keys differ from i18n/fa/{g}.json')


class TestLocalRefs(unittest.TestCase):
    def check_html(self, rel):
        page = WEB / rel
        p = RefParser()
        p.feed(page.read_text(encoding='utf-8'))
        self.assertTrue(p.refs, rel)
        for tag, ref in p.refs:
            if ref.startswith(('#', 'tel:', 'mailto:', 'data:')):
                continue
            self.assertFalse(re.match(r'^(https?:)?//', ref), f'{rel}: external link {ref}, everything must be local')
            target = (page.parent / urllib.parse.unquote(ref.split('?')[0].split('#')[0])).resolve()
            self.assertTrue(target.is_file(), f'{rel}: no file {ref}')
            self.assertTrue(str(target).startswith(str(WEB.resolve())), f'{rel}: {ref} is outside app/web')

    def test_index_refs(self):
        self.check_html('index.html')

    def test_components_refs(self):
        self.check_html('dev/components.html')

    def test_css_urls(self):
        for css in (WEB / 'css').glob('*.css'):
            for ref in re.findall(r'url\(\s*[\'"]?([^\'")]+)', css.read_text(encoding='utf-8')):
                if ref.startswith('data:'):
                    continue
                self.assertFalse(re.match(r'^(https?:)?//', ref), f'{css.name}: external url {ref}')
                self.assertTrue((css.parent / ref).resolve().is_file(), f'{css.name}: no file {ref}')


NODE_SCRIPT = r"""
const path = require('path'), fs = require('fs');
const web = process.argv[1];
require(path.join(web, 'js', 'core.js'));
for (const l of ['fa', 'ps', 'en']) App.i18n.add(l, JSON.parse(fs.readFileSync(path.join(web, 'i18n', l + '.json'), 'utf8')));
const iso = (s) => String(s).replace(/[⁦-⁩]/g, '');
const out = {};
const P = App.phone, F = App.fmt;
out.normalize = ['0777123456', '۰۷۷۷ ۱۲۳ ۴۵۶', '+93 777 123 456', '0093777123456', '777123456', '93777123456',
  '٠٧٧٧١٢٣٤٥٦', '0777-123-456', '⁦۰۷۷۷ ۱۲۳ ۴۵۶⁩', '0777 123 4547', '0677123456', '12345', '', null]
  .map((x) => P.normalize(x));
out.display = ['+93777123456', '0799123456', '۰۷۰۰۱۲۳۴۵۶', '119'].map((x) => iso(P.display(x)));
out.displayIsolated = /^⁦.*⁩$/.test(P.display('0777123456'));
out.tel = [P.tel('0777123456'), P.tel('119')];
out.local = P.local('+93777123456');
out.digits = [F.digits('2026'), F.latin('۰۱۲۳۴۵۶۷۸۹'), F.num(12), F.num(34.555, 4)];
const d10 = '2026-05-01T06:03:00Z';
out.d10 = [F.dateNum(d10), F.time(d10), F.stamp(d10), F.date(d10), F.time('2026-05-01T06:03:00')];
out.dates = [F.date('2024-06-01T00:00:00Z'), F.date('2026-03-20T19:30:00Z'), F.time('2026-11-05T20:00:00Z'),
  F.stamp('2026-05-01T10:15:00Z')];
out.months = [];
for (let i = 0; i < 12; i++) out.months.push(F.date(Date.UTC(2026, 2, 26) + i * 30.44 * 86400000).split(' ')[1]);
out.misc = [F.distance(12), F.distance(1.53), F.distance(0.35), F.percent(67), iso(F.coord(34.555, 69.207)),
  F.ago(Date.now() - 30000), F.ago(Date.now() - 5 * 60000)];
out.t = [App.t('home.distance', { d: F.distance(12) }), App.t('fmt.km', { n: 7 }), App.t('no.such.key')];
out.html = String(App.html`<b title="${'"x"'}">${'<i>'}</b>${App.icon ? '' : ''}`);
App.lang = 'ps'; App.i18n.add('ps', { 'nav.home': '' });
out.ps = [App.t('nav.home'), F.date('2024-06-01T00:00:00Z'), F.num(12)];
App.lang = 'en';
out.en = [F.distance(12), F.date('2024-06-01T00:00:00Z'), F.num(34.5, 1), iso(P.display('0777123456')), App.dir()];
App.lang = 'fa';
out.dir = App.dir();
(async () => {
  const me = async (status, body) => {
    globalThis.fetch = async () => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });
    await App.refreshMe();
    return [App.state.user, App.state.admin];
  };
  out.me = [
    await me(200, { user: null, admin: null }),
    await me(200, { user: { id: 7 }, admin: null }),
    await me(401, { error: 'auth_required', message: 'x' }),
    await me(200, { user: null, admin: { username: 'admin' } }),
    await me(200, { id: 9 }),
    await me(200, { user: null, admin: null }),
  ];
  process.stdout.write(JSON.stringify(out));
})();
"""


@unittest.skipUnless(shutil.which('node'), 'no node')
class TestFormatsNode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = subprocess.run(['node', '-e', NODE_SCRIPT, str(WEB)], capture_output=True, text=True, encoding='utf-8', timeout=60)
        if r.returncode != 0:
            raise AssertionError('node: ' + r.stderr)
        cls.o = json.loads(r.stdout)

    def test_phone_normalize(self):
        ok = '+93777123456'
        self.assertEqual(self.o['normalize'], [ok] * 9 + [None] * 5)

    def test_phone_display(self):
        self.assertEqual(self.o['display'], ['۰۷۷۷ ۱۲۳ ۴۵۶', '۰۷۹۹ ۱۲۳ ۴۵۶', '۰۷۰۰ ۱۲۳ ۴۵۶', '۱۱۹'])
        self.assertTrue(self.o['displayIsolated'])
        self.assertEqual(self.o['tel'], ['tel:+93777123456', 'tel:119'])
        self.assertEqual(self.o['local'], '0777123456')

    def test_digits(self):
        self.assertEqual(self.o['digits'], ['۲۰۲۶', '0123456789', '۱۲', '۳۴٫۵۵۵۰'])

    def test_solar_kabul(self):
        # Figma 10: "۱۴۰۵/۰۲/۱۱ - ۱۰:۳۳ - قبل از ظهر" = 2026-05-01 06:03 UTC (Kabul UTC+4:30)
        self.assertEqual(self.o['d10'], ['۱۴۰۵/۰۲/۱۱', '۱۰:۳۳', '۱۴۰۵/۰۲/۱۱ - ۱۰:۳۳ - قبل از ظهر', '۱۱ ثور ۱۴۰۵', '۱۰:۳۳'])
        self.assertEqual(self.o['dates'], ['۱۲ جوزا ۱۴۰۳', '۱ حمل ۱۴۰۵', '۰۰:۳۰', '۱۴۰۵/۰۲/۱۱ - ۲:۴۵ - بعد از ظهر'])
        self.assertEqual(self.o['months'], ['حمل', 'ثور', 'جوزا', 'سرطان', 'اسد', 'سنبله', 'میزان', 'عقرب', 'قوس', 'جدی', 'دلو', 'حوت'])

    def test_misc_formats(self):
        self.assertEqual(self.o['misc'], ['۱۲ کیلومتر', '۱٫۵ کیلومتر', '۳۵۰ متر', '۶۷٪', '۳۴٫۵۵۵۰°N, ۶۹٫۲۰۷۰°E',
                                          'همین حالا', '۵ دقیقه پیش'])

    def test_t_and_html(self):
        self.assertEqual(self.o['t'], ['به دوری ۱۲ کیلومتر', '۷ کیلومتر', 'no.such.key'])
        self.assertEqual(self.o['html'], '<b title="&quot;x&quot;">&lt;i&gt;</b>')

    def test_me_guest_200_and_401(self):
        # guest: both 200 {user: null, admin: null} and 401 → nobody is logged in
        self.assertEqual(self.o['me'], [[None, None], [{'id': 7}, None], [None, None], [None, {'username': 'admin'}],
                                        [{'id': 9}, None], [None, None]])

    def test_other_languages(self):
        self.assertEqual(self.o['ps'], ['صفحه اصلی', '۱۲ غبرګولی ۱۴۰۳', '۱۲'])  # empty translation → Dari
        self.assertEqual(self.o['en'], ['12 km', '12 Jawza 1403', '34.5', '0777 123 456', 'ltr'])
        self.assertEqual(self.o['dir'], 'rtl')


# ---------------------------------------------------------------- headless Chrome over CDP (our own mini WebSocket client)
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
            raise RuntimeError(json.dumps(r['exceptionDetails'])[:600])
        return r['result'].get('value')

    def wait_for(self, expr, timeout=25):
        end = time.time() + timeout
        while time.time() < end:
            if self.eval(expr):
                return True
            time.sleep(0.2)
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


class Quiet(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, '.js': 'text/javascript',
                      '.json': 'application/json', '.css': 'text/css', '.svg': 'image/svg+xml', '.woff2': 'font/woff2'}

    def log_message(self, *a):
        pass

    def do_GET(self):  # a stand-in group dictionary to check the loader; the real group files are not touched
        if self.path.split('?')[0] == '/i18n/fa/zz-test.json':
            body = json.dumps({'nav.home': 'خانه-تست', 'zz.only': 'فقط-گروه'}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


# overflow: remove the overflow-x safety net and measure the real width; plus the culprits
OVERFLOW_JS = r"""(() => {
  document.body.style.overflowX = 'visible';
  document.querySelectorAll('.app').forEach((a) => { a.style.overflowX = 'visible'; });
  const W = document.documentElement.clientWidth;
  const bad = [];
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('.leaflet-container')) continue;
    const r = el.getBoundingClientRect();
    if (r.width && (r.right > W + 1 || r.left < -1)) bad.push((el.className && el.className.baseVal === undefined ? el.className : el.tagName) + ' ' + Math.round(r.left) + '..' + Math.round(r.right));
  }
  const res = { sw: document.documentElement.scrollWidth, w: W, bad: bad.slice(0, 8) };
  document.body.style.overflowX = '';
  document.querySelectorAll('.app').forEach((a) => { a.style.overflowX = ''; });
  return res;
})()"""

TILE_HOSTS = ('tile.openstreetmap.org',)  # map tiles come from the internet: offline, that is not a ui-core error


@unittest.skipIf(os.environ.get('PANAH_SKIP_BROWSER'), 'PANAH_SKIP_BROWSER')
@unittest.skipUnless(find_chrome(), 'no Google Chrome')
class TestBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Quiet, directory=str(WEB)))
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.httpd.server_address[1]}/'
        STAND.mkdir(exist_ok=True)
        cls.profile = STAND / f'chrome-ui-core-{os.getpid()}'
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
        url = next(p['webSocketDebuggerUrl'] for p in pages if p.get('type') == 'page')
        cls.ws = WS(url)
        cls.cdp = CDP(cls.ws)
        for m in ('Runtime.enable', 'Log.enable', 'Page.enable'):
            cls.cdp.call(m)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.cdp.call('Browser.close', timeout=5)
        except Exception:
            pass
        cls.ws.close()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.httpd.shutdown()
        for _ in range(20):  # Windows holds the profile files a bit longer
            shutil.rmtree(cls.profile, ignore_errors=True)
            if not cls.profile.exists():
                break
            time.sleep(0.3)

    def open(self, path, width):
        c = self.cdp
        c.events.clear()
        c.call('Emulation.setDeviceMetricsOverride', {'width': width, 'height': 900, 'deviceScaleFactor': 2, 'mobile': True})
        c.call('Page.navigate', {'url': self.base + path})
        self.assertTrue(c.wait_for('document.readyState === "complete"'), 'page did not load')

    def test_1_components_440_and_360(self):
        c = self.cdp
        self.open('dev/components.html', 440)
        self.assertTrue(c.wait_for('document.body.dataset.ready === "1"'), 'demo did not render')
        c.eval('document.fonts.ready.then(() => true)', wait=True)
        fonts = c.eval('[...document.fonts].filter(f => f.family.replace(/"/g, "") === "Vazirmatn" && f.status === "loaded").length')
        self.assertGreaterEqual(fonts, 1, 'Vazirmatn font did not load')
        self.assertEqual(c.eval('getComputedStyle(document.body).fontFamily.split(",")[0].replace(/[\'"]/g, "")'), 'Vazirmatn')
        self.assertEqual(c.eval('document.documentElement.dir'), 'rtl')
        full = c.eval('document.querySelector(".logo--full") && document.querySelector(".logo--full").textContent')
        self.assertIn('پناه', full or '')
        self.assertIn('PANAH', full or '')
        c.wait_for('[...document.querySelectorAll(".leaflet-tile")].every(i => i.complete)', timeout=10)
        time.sleep(0.5)
        r = c.eval(OVERFLOW_JS)
        self.assertLessEqual(r['sw'], 440, f'horizontal scroll at 440: {r}')
        # full-page screenshot at 440 px (×2, like the Figma frames)
        h = c.call('Page.getLayoutMetrics')['cssContentSize']['height']
        shot = c.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': True,
                                                 'clip': {'x': 0, 'y': 0, 'width': 440, 'height': h, 'scale': 1}})
        (STAND / 'ui-core-440.png').write_bytes(base64.b64decode(shot['data']))
        # 360 px
        c.call('Emulation.setDeviceMetricsOverride', {'width': 360, 'height': 800, 'deviceScaleFactor': 2, 'mobile': True})
        time.sleep(0.6)
        r = c.eval(OVERFLOW_JS)
        self.assertLessEqual(r['sw'], 360, f'horizontal scroll at 360: {r}')
        # English flips the page to left-to-right
        c.eval('App.setLang("en").then(() => document.documentElement.dir)', wait=True)
        self.assertEqual(c.eval('document.documentElement.dir'), 'ltr')
        r = c.eval(OVERFLOW_JS)
        self.assertLessEqual(r['sw'], 360, f'en: horizontal scroll at 360: {r}')
        c.eval('App.setLang("fa").then(() => true)', wait=True)
        self.assertEqual(c.errors(ignore=TILE_HOSTS), [], 'console errors in components.html')

    def nav(self, route):
        """Navigate and wait for the new screen (and its possible further redirect) → [address, screen name]."""
        c = self.cdp
        c.eval('window.__prev = document.querySelector(".screen"); true')
        c.eval(f'App.go({json.dumps(route)}); true')
        c.wait_for('!!document.querySelector(".screen") && document.querySelector(".screen") !== window.__prev', timeout=8)
        time.sleep(0.4)
        return c.eval('[location.hash, (document.querySelector(".screen") || {dataset: {}}).dataset.screen]')

    def test_2_router_in_index(self):
        """index.html on a plain http.server (no API: /api/… → 404, screens show an error as expected; ApiError is a warn):
        an unknown address → "not found";
        every registered screen opens (a protected one via a redirect to its login), not "not found"."""
        c = self.cdp
        self.open('index.html', 440)
        self.assertTrue(c.wait_for('!!document.querySelector(".screen[data-screen]") && location.hash.length > 2'), 'app did not start')
        self.assertEqual(self.nav('#/no-such-route'), ['#/no-such-route', 'not-found'])
        # navigation mechanics on our own test screens: parameters, ?query, tab, admin login, "back"
        c.eval('''App.screen("t-alert", {route: "#/t-alert/:id", nav: "resident", tab: "home",
                   render(p, el) { el.innerHTML = String(App.html`<p id="t">${p.id}|${p.q || ""}</p>`); }});
                  App.screen("t-admin", {route: "#/t-admin", nav: "admin", auth: "admin", render(p, el) { el.textContent = "admin"; }});
                  true''')
        self.assertEqual(self.nav('#/t-alert/42?q=x'), ['#/t-alert/42?q=x', 't-alert'])
        self.assertEqual(c.eval('document.getElementById("t").textContent'), '42|x')
        self.assertEqual(c.eval('document.querySelector(".tabbar--resident .is-on").dataset.tab'), 'home')
        self.assertEqual(c.eval('document.querySelectorAll(".tabbar--resident .tabbar__item").length'), 4)
        hash_, _ = self.nav('#/t-admin')  # guest → to the admin login with ?next=
        self.assertTrue(hash_.startswith('#/admin/login?next='), hash_)
        c.eval('App.back(); true')
        self.assertTrue(c.wait_for('location.hash === "#/t-alert/42?q=x"'), c.eval('location.hash'))
        # all group screens: no known address gives "not found"
        routes = c.eval('App.screens.filter(s => !s.name.startsWith("t-")).map(s => s.route.replace(/:\\w+/g, "1"))')
        self.assertGreater(len(routes), 0)
        bad = []
        for r in routes:
            hash_, screen = self.nav(r)
            if not screen or screen == 'not-found':
                bad.append(f'{r} → {hash_} → {screen}')
        self.assertEqual(bad, [], 'known addresses gave "not found"')
        self.assertTrue(any('404' in e for e in c.errors()), 'the console check does not catch errors (expected a 404 from /api/…)')
        self.assertEqual(c.errors(ignore=TILE_HOSTS + ('/api/',)), [], 'console errors in index.html')

    def test_3_group_dicts_override(self):
        """A key from i18n/<lang>/<group>.json overrides the shared one; a missing group file is skipped silently."""
        c = self.cdp
        self.open('dev/components.html', 440)
        self.assertTrue(c.wait_for('document.body.dataset.ready === "1"'))
        v = c.eval('''(async () => {
            App.config.groups = App.config.groups.concat(['zz-test', 'zz-missing']);
            delete App.i18n.dicts.fa;
            await App.i18n.load('fa');
            return [App.t('nav.home'), App.t('zz.only'), App.t('nav.videos')];
        })()''', wait=True)
        self.assertEqual(v, ['خانه-تست', 'فقط-گروه', 'ویدیوها'])
        self.assertEqual(c.errors(ignore=TILE_HOSTS + ('zz-missing',)), [], 'console errors while loading group dictionaries')

    def test_4_offline_strip(self):
        """No network: App.online = false, the core 'offline' event, a thin banner in the page language; network back: removed."""
        c = self.cdp
        self.open('dev/components.html', 440)
        self.assertTrue(c.wait_for('document.body.dataset.ready === "1"'))
        c.eval('App.i18n.load("en").then(() => true)', wait=True)  # offline, the dictionary can no longer be downloaded
        c.eval('window.__net = []; App.on("offline", () => __net.push("off")); App.on("online", () => __net.push("on")); true')
        self.assertIs(c.eval('App.online'), True)
        self.assertFalse(c.eval('!!document.querySelector(".netbar")'))
        c.call('Network.enable')
        net = {'offline': True, 'latency': 0, 'downloadThroughput': -1, 'uploadThroughput': -1}
        try:
            c.call('Network.emulateNetworkConditions', net)
            self.assertTrue(c.wait_for('App.online === false && !!document.querySelector(".netbar")', timeout=5), 'no offline banner')
            self.assertEqual(c.eval('document.querySelector(".netbar").textContent.trim()'), 'بدون اتصال به اینترنت')
            self.assertTrue(c.eval('document.body.classList.contains("is-offline")'))
            c.eval('App.setLang("en").then(() => true)', wait=True)
            self.assertEqual(c.eval('document.querySelector(".netbar").textContent.trim()'), 'No internet connection')
            self.assertLessEqual(c.eval(OVERFLOW_JS)['sw'], 440)
            c.eval('App.setLang("fa").then(() => true)', wait=True)
        finally:
            c.call('Network.emulateNetworkConditions', dict(net, offline=False))
        self.assertTrue(c.wait_for('App.online === true && !document.querySelector(".netbar")', timeout=5), 'banner did not go away')
        self.assertEqual(c.eval('__net'), ['off', 'on'])
        c.call('Network.disable')


    def test_5_map_destroy_is_safe(self):
        """A map removed right after creation (fast screen change), removed twice, called after removal: no errors."""
        c = self.cdp
        self.open('dev/components.html', 440)
        self.assertTrue(c.wait_for('document.body.dataset.ready === "1"'))
        c.eval('''(async () => {
            const box = document.createElement("div"); document.body.appendChild(box);
            for (let i = 0; i < 20; i++) {
              const m = App.map(box, {center: [34.5281, 69.1723], zoom: 12, fit: true,
                markers: [{lat: 34.5281, lon: 69.1723}, {lat: 34.50, lon: 69.15}], circle: {lat: 34.5281, lon: 69.1723, radius: 2000}});
              if (i % 2) await new Promise((r) => setTimeout(r, 5));
              m.destroy(); m.destroy();
              m.setMarkers([{lat: 1, lon: 1}]).setCircle({lat: 1, lon: 1}).setView([1, 1], 3).fit();
              if (!m.removed) throw new Error("removed flag");
            }
            await new Promise((r) => setTimeout(r, 400));  // deferred resize and zoom ends would fire here
            box.remove(); return true; })()''', wait=True)
        self.assertEqual(c.errors(ignore=TILE_HOSTS), [], 'errors when removing a map')

# ---------------------------------------------------------------- with the real server (app/main.py): SSE stream drops
MAIN = ROOT / 'app' / 'main.py'
WATCH_JS = """(() => {
  const bump = (k) => { try { localStorage.setItem(k, String(+(localStorage.getItem(k) || 0) + 1)); } catch (e) {} };
  addEventListener('pagehide', (e) => { if (e.persisted) bump('t.bfcache'); });
  addEventListener('pageshow', (e) => { if (e.persisted) { window.__restored = (window.__restored || 0) + 1; } });
})();"""


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@unittest.skipIf(os.environ.get('PANAH_SKIP_BROWSER'), 'PANAH_SKIP_BROWSER')
@unittest.skipUnless(find_chrome() and MAIN.is_file(), 'no Google Chrome or app/main.py')
class TestLiveServer(unittest.TestCase):
    """Experiment 10: a page with no events does not lose an alert after a server restart; 'reconnect'; the back/forward
    cache holds no connections (HTTP/1.0 server, Chrome has 6 connections per host), so warm loads do not stall."""

    @classmethod
    def start_server(cls):
        cls.server_log = open(cls.dir / 'server.out', 'ab')
        cls.server = subprocess.Popen(
            [sys.executable, str(MAIN), '--no-browser', '--no-usgs', '--host', '127.0.0.1', '--port', str(cls.port),
             '--db', str(cls.dir / 'panah.db')], stdout=cls.server_log, stderr=subprocess.STDOUT, cwd=str(ROOT))
        end = time.time() + 30
        while time.time() < end:
            try:
                urllib.request.urlopen(cls.base + 'api/health', timeout=2).read()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError('server did not start: ' + (cls.dir / 'server.out').read_text(errors='replace')[-800:])

    @classmethod
    def stop_server(cls):
        cls.server.terminate()
        try:
            cls.server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.server.kill()
            cls.server.wait()
        cls.server_log.close()

    @classmethod
    def setUpClass(cls):
        STAND.mkdir(exist_ok=True)
        cls.dir = STAND / f'ui-core-live-{os.getpid()}'
        cls.dir.mkdir(exist_ok=True)
        cls.port = free_port()
        cls.base = f'http://127.0.0.1:{cls.port}/'
        cls.start_server()
        cls.profile = cls.dir / 'chrome'
        cls.proc = subprocess.Popen(
            [find_chrome(), '--headless=new', f'--user-data-dir={cls.profile}', '--remote-debugging-port=0',
             '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--hide-scrollbars',
             '--disable-background-networking', '--lang=fa', 'about:blank'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = cls.profile / 'DevToolsActivePort'
        end = time.time() + 30
        while not (port_file.is_file() and port_file.read_text().strip()) and time.time() < end:
            time.sleep(0.1)
        dport = int(port_file.read_text().split()[0])
        pages = json.load(urllib.request.urlopen(f'http://127.0.0.1:{dport}/json/list', timeout=10))
        cls.ws = WS(next(p['webSocketDebuggerUrl'] for p in pages if p.get('type') == 'page'))
        cls.cdp = CDP(cls.ws)
        for m in ('Runtime.enable', 'Log.enable', 'Page.enable'):
            cls.cdp.call(m)
        cls.cdp.call('Page.addScriptToEvaluateOnNewDocument', {'source': WATCH_JS})
        cls.cdp.call('Emulation.setDeviceMetricsOverride', {'width': 440, 'height': 900, 'deviceScaleFactor': 2, 'mobile': True})

    @classmethod
    def tearDownClass(cls):
        try:
            cls.cdp.call('Browser.close', timeout=5)
        except Exception:
            pass
        cls.ws.close()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.stop_server()
        for _ in range(20):
            shutil.rmtree(cls.dir, ignore_errors=True)
            if not cls.dir.exists():
                break
            time.sleep(0.3)

    def load_home(self, timeout=60):
        """about:blank → #/home (full load); wait for the screen and an open stream. → seconds."""
        c = self.cdp
        c.call('Page.navigate', {'url': 'about:blank'})
        c.wait_for('location.href === "about:blank"', timeout=10)
        t0 = time.time()
        c.call('Page.navigate', {'url': self.base + '#/home'})
        ok = c.wait_for('!!window.App && !!document.querySelector(\'.screen[data-screen="home"]\') && App.live.status === "open"', timeout=timeout)
        self.assertTrue(ok, 'home did not open or the stream is not open')
        return time.time() - t0

    def admin_alert(self):
        jar = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

        def post(path, body):
            req = urllib.request.Request(self.base + path, data=json.dumps(body).encode('utf-8'),
                                         headers={'Content-Type': 'application/json'}, method='POST')
            return json.load(op.open(req, timeout=15))
        post('api/admin/login', {'username': 'admin', 'password': 'panah1234'})
        r = post('api/admin/alerts', {'type': 'flood', 'lat': 34.5281, 'lon': 69.1723, 'radius_km': 2,
                                      'place': 'کابل', 'safe_place': 'پارک شهر نو'})
        return (r.get('alert') or r).get('id')

    def test_1_warm_loads_no_stall(self):
        c = self.cdp
        self.load_home()  # the first load warms the cache
        c.eval('localStorage.setItem("t.bfcache", "0"); true')
        times = [self.load_home() for _ in range(24)]
        stored = int(c.eval('localStorage.getItem("t.bfcache")') or 0)
        slow = [round(t, 1) for t in times if t > 5]
        self.assertEqual(slow, [], f'stalled warm loads (> 5 s) out of {len(times)}; went to the back/forward cache: {stored}')
        self.assertGreater(stored, 0, 'pages did not go to the back/forward cache, so the check does not reproduce experiment 08')
        print(f'\n  warm loads: {len(times)}, max {max(times):.2f} s, in the back/forward cache: {stored}', end=' ')

    def test_2_bfcache_closes_and_reconnects(self):
        c = self.cdp
        self.load_home()
        # a listener after the core's: on pagehide the stream must already be closed (the server sees it only with the heartbeat 15 s later)
        c.eval('''window.__rc = 0; App.live.on("reconnect", () => { __rc++; }, {global: true});
                  addEventListener("pagehide", (e) => { localStorage.setItem("t.hide", e.persisted + ":" + App.live.status); });
                  localStorage.removeItem("t.hide"); true''')
        c.call('Page.navigate', {'url': 'about:blank'})
        c.wait_for('location.href === "about:blank"', timeout=10)
        hist = c.call('Page.getNavigationHistory')
        c.call('Page.navigateToHistoryEntry', {'entryId': hist['entries'][hist['currentIndex'] - 1]['id']})
        self.assertTrue(c.wait_for('window.__restored >= 1', timeout=10), 'page did not come back from the back/forward cache')
        self.assertEqual(c.eval('localStorage.getItem("t.hide")'), 'true:paused', 'stream not closed on pagehide')
        self.assertTrue(c.wait_for('App.live.status === "open" && window.__rc >= 1', timeout=10), 'no reconnect after pageshow')

    def test_3_restart_page_without_events(self):
        """Like experiment 10, part D: the page has no events yet; the server is stopped and started; the alert comes out before the reconnect."""
        c = self.cdp
        self.load_home()
        c.eval('''window.__rc = 0; window.__alerts = [];
                  App.live.on("reconnect", () => { __rc++; }, {global: true});
                  App.live.on("alert", (d) => { __alerts.push((d && (d.id || (d.alert && d.alert.id))) || 0); }, {global: true});
                  true''')
        self.assertEqual(c.eval('App.live.lastEventId()'), '', 'the page already got events, not the case under test')
        self.stop_server()
        time.sleep(1.0)
        self.start_server()
        aid = self.admin_alert()  # right away, before the page reconnects (server retry pause: 3000)
        self.assertTrue(c.wait_for(f'__alerts.includes({json.dumps(aid)})', timeout=15), f'alert {aid} did not arrive: {c.eval("__alerts")}')
        self.assertTrue(c.wait_for('__rc >= 1', timeout=5), 'no reconnect after the restart')


if __name__ == '__main__':
    unittest.main()
