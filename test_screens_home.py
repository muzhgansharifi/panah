"""Screens of the home group (05/06, alert with map, search 09, videos, numbers): dictionaries, keys, CSS and a check in Chrome.

Run from the project root:  python -m unittest app.tests.test_screens_home   (or discover app/tests)
Python standard library only. No Google Chrome: the browser part is skipped (PANAH_SKIP_BROWSER=1 does the same).
Browser: our own PANAH server in a thread (temporary database; past alerts are a real USGS feed slice from tests/data),
headless Chrome with its own temporary profile in stand/ (deleted afterwards), location faked to Kabul (CDP).
PANAH_HOME_URL=http://127.0.0.1:8102 runs the same against an already running server (demo admin login admin / panah1234;
the test ends its own alerts at the end). Screenshots at 440 px (×2, like the Figma frames): stand/screens/home/NN-<name>.png.
"""
import base64
import datetime
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / 'app' / 'tests'
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import support  # noqa: E402  (puts app/ on sys.path)
from server.common import utcnow  # noqa: E402
from alerts_support import destination  # noqa: E402
from test_ui_core import CDP, OVERFLOW_JS, WS, find_chrome  # noqa: E402  the core's mini CDP client

from server import usgs, web  # noqa: E402

WEB = ROOT / 'app' / 'web'
STAND = ROOT / 'stand'
SHOTS = STAND / 'screens' / 'home'
HOME_JS = WEB / 'js' / 'screens' / 'home.js'
HOME_CSS = WEB / 'css' / 'home.css'
LANGS = ('fa', 'ps', 'en')
ROUTES = ('#/home', '#/alert/:id', '#/search', '#/videos', '#/video/:id', '#/numbers')
KABUL = (34.5553, 69.2075)                 # the Kabul point from Figma 10
ZARNEGAR = ('پارک زرنگار', 34.5212367, 69.1752644)  # a safe place from the content seed data
SLOW_3G = {'offline': False, 'latency': 2000, 'downloadThroughput': 50000, 'uploadThroughput': 50000}  # DevTools "Slow 3G"
NO_LIMIT = {'offline': False, 'latency': 0, 'downloadThroughput': -1, 'uploadThroughput': -1}
OFFLINE = {'offline': True, 'latency': 0, 'downloadThroughput': 0, 'uploadThroughput': 0}
EXTERNAL = os.environ.get('PANAH_HOME_URL', '').strip()
PNG_1PX = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def group_dict(lang):
    return read_json(WEB / 'i18n' / lang / 'home.json')


def common_dict(lang):
    return read_json(WEB / 'i18n' / f'{lang}.json')


class TestHomeFiles(unittest.TestCase):
    """Without a browser: group dictionaries, string keys in home.js, routes, CSS with logical properties only."""

    def test_dicts_complete(self):
        d = {lang: group_dict(lang) for lang in LANGS}
        self.assertGreater(len(d['fa']), 20)
        placeholders = lambda s: set(re.findall(r'\{(\w+)\}', s))  # noqa: E731
        for lang in ('ps', 'en'):
            self.assertEqual(sorted(d[lang]), sorted(d['fa']), f'i18n/{lang}/home.json: keys differ from fa')
        for key, fa in d['fa'].items():
            self.assertTrue(key.startswith('home.'), f'{key}: group keys must be home.*')
            for lang in LANGS:
                self.assertIsInstance(d[lang][key], str)
                self.assertTrue(d[lang][key].strip(), f'{lang}:{key} is empty; Pashto and English must be filled in')
                self.assertEqual(placeholders(d[lang][key]), placeholders(fa), f'{lang}:{key}: placeholders differ from Dari')
        clash = sorted(set(d['fa']) & set(common_dict('fa')))
        self.assertEqual(clash, [], 'a group key would override a shared key of all screens')

    def test_keys_used_exist(self):
        js = HOME_JS.read_text(encoding='utf-8')
        used = set(re.findall(r"\bt\(\s*'([\w.]+)'", js))
        used |= {k for pair in re.findall(r"\?\s*'([\w.]+)'\s*:\s*'([\w.]+)'", js) for k in pair if '.' in k}
        used |= set(re.findall(r"\btitle: '([\w.]+)'", js))
        self.assertGreater(len(used), 30)
        for lang in LANGS:
            known = set(common_dict(lang)) | set(group_dict(lang))
            self.assertEqual(sorted(k for k in used if k not in known), [], f'{lang}: strings missing')

    def test_routes(self):
        js = HOME_JS.read_text(encoding='utf-8')
        for route in ROUTES:
            self.assertIn(f"route: '{route}'", js)
        names = re.findall(r"App\.screen\('([\w-]+)'", js)
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(n == 'home' or n.startswith('home-') for n in names), names)

    def test_css_logical_only(self):
        bad = re.compile(r'(?<![\w-])(margin|padding|border)-(left|right)\b|(?<![\w-])(left|right)\s*:|'
                         r'text-align\s*:\s*(left|right)|float\s*:\s*(left|right)|border-(top|bottom)-(left|right)-radius')
        text = re.sub(r'/\*.*?\*/', '', HOME_CSS.read_text(encoding='utf-8'), flags=re.S)
        self.assertEqual([m.group(0) for m in bad.finditer(text)], [])
        four = [m.group(0) for m in re.finditer(r'(?<![\w-])(?:margin|padding)\s*:\s*([^;}]+)', text)
                if len(m.group(1).split()) == 4]   # 4 values: different left and right sides
        self.assertEqual(four, [])
        text = re.sub(r'@keyframes[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}', '', text)
        foreign = []
        for sel in re.findall(r'(?:^|[;}])\s*([^;{}@]+?)\s*\{', text):
            for part in sel.split(','):
                first = re.sub(r'^\[dir="(ltr|rtl)"\]\s*', '', part.strip()).split()[0]
                if 'hm-' not in first:
                    foreign.append(part.strip())
        self.assertEqual(foreign, [], 'rules only inside our own screens (hm-* classes): shared components are not touched')


# ---------------------------------------------------------------- geofence rule of the client (Node.js)
GEO_NODE = r"""
const path = require('path');
globalThis.window = globalThis;
require(path.join(process.argv[1], 'js', 'core.js'));
require(path.join(process.argv[1], 'js', 'screens', 'home.js'));
const cases = JSON.parse(process.argv[2]);
const G = App.homeGeo;
const out = cases.map((c) => { const a = G.withDistance(c.alert, c.me); return [a.distance_km, a.inside]; });
process.stdout.write(JSON.stringify(out));
"""
CITIES = {'Kabul': KABUL, 'Herat': (34.3482, 62.1997), 'Fayzabad': (37.1166, 70.5800)}


@unittest.skipUnless(shutil.which('node'), 'no node')
class TestGeofenceRule(unittest.TestCase):
    """Experiment 02: the home screen decides "inside the 2 km radius" for live alert events. It must use the server's
    rule — unrounded haversine distance (R = 6371.0088 km) <= radius_km + 1 mm — and trust inside / distance_km
    when the server sent them. Probes at 1 999.6 / 2 000.0 / 2 000.4 m in 8 directions around three cities."""

    def run_node(self, cases):
        r = subprocess.run(['node', '-e', GEO_NODE, str(WEB), json.dumps(cases)], capture_output=True, text=True,
                           encoding='utf-8', timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_edge_probes_like_server(self):
        from server import alerts, geo
        cases, expect = [], []
        for name, me in CITIES.items():
            for bearing in range(0, 360, 45):
                for metres, inside in ((1999.6, True), (2000.0, True), (2000.4, False)):
                    lat, lon = destination(*me, metres / 1000, bearing)
                    server_km = geo.distance_km(me[0], me[1], lat, lon)
                    server_inside = server_km <= 2.0 + alerts.EDGE_KM
                    self.assertEqual(server_inside, inside, f'server rule {name} {bearing}° {metres} m')
                    cases.append({'me': {'lat': me[0], 'lon': me[1]}, 'alert': {'id': 1, 'lat': lat, 'lon': lon, 'radius_km': 2}})
                    expect.append((name, bearing, metres, server_km, server_inside))
        got = self.run_node(cases)
        self.assertEqual(len(got), len(expect))
        for (name, bearing, metres, server_km, server_inside), (client_km, client_inside) in zip(expect, got):
            where = f'{name} {bearing}° {metres} m'
            self.assertEqual(client_inside, server_inside, f'{where}: screen and server disagree on "inside"')
            self.assertAlmostEqual(client_km, server_km, delta=1e-9, msg=f'{where}: distance not unrounded or not the server formula')

    def test_server_values_win(self):
        me = {'lat': KABUL[0], 'lon': KABUL[1]}
        near = destination(*KABUL, 0.5, 90)
        far = destination(*KABUL, 30.0, 90)
        got = self.run_node([
            {'me': me, 'alert': {'id': 1, 'lat': near[0], 'lon': near[1], 'radius_km': 2, 'inside': False, 'distance_km': 2.5}},
            {'me': me, 'alert': {'id': 2, 'lat': far[0], 'lon': far[1], 'radius_km': 2, 'inside': True}},
            {'me': me, 'alert': {'id': 3, 'lat': far[0], 'lon': far[1], 'radius_km': 40}},
            {'me': None, 'alert': {'id': 4, 'lat': near[0], 'lon': near[1], 'radius_km': 2}},
        ])
        self.assertEqual(got[0], [2.5, False], 'server inside / distance_km were overridden')
        self.assertEqual(got[1][1], True, 'server inside was overridden')
        self.assertAlmostEqual(got[1][0], 30.0, delta=1e-9)
        self.assertEqual(got[2][1], True, 'radius_km of the alert not used')
        self.assertEqual(got[3], [None, False], 'no location: no distance, not inside')


# ---------------------------------------------------------------- browser
def http_json(base, path, data=None, cookie=None, method=None):
    req = urllib.request.Request(base.rstrip('/') + path, method=method or ('POST' if data is not None else 'GET'),
                                 data=json.dumps(data).encode('utf-8') if data is not None else None,
                                 headers={'Content-Type': 'application/json', **({'Cookie': cookie} if cookie else {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, json.loads(r.read().decode('utf-8') or '{}'), r.headers


@unittest.skipIf(os.environ.get('PANAH_SKIP_BROWSER'), 'PANAH_SKIP_BROWSER')
@unittest.skipUnless(find_chrome(), 'no Google Chrome')
class TestHomeScreens(unittest.TestCase):
    """Live server + headless Chrome over CDP: 05 → an alert nearby (live update) → 06 → alert with map →
    search 09 → videos → numbers → Pashto and English → location of a logged-in resident."""

    created = []

    @classmethod
    def setUpClass(cls):
        cls.srv = None
        if EXTERNAL:
            cls.base = EXTERNAL.rstrip('/') + '/'
        else:
            cls.srv = support.Running(engines=web.ENGINES, web_dir=WEB, config={'usgs': False}).__enter__()
            cls.base = f'http://127.0.0.1:{cls.srv.port}/'
            sample = (TESTS / 'data' / 'usgs_sample.geojson').read_text(encoding='utf-8')
            with cls.srv.app.transaction() as db:   # 15 real quakes in Afghanistan → "past alerts"
                cls.usgs = usgs.poll_once(db, fetch=lambda url: sample,
                                         now=datetime.datetime(2026, 11, 20, tzinfo=datetime.timezone.utc))
                # a video without a link (happens in an old database: the admin has not added the link yet): "ویدیو موجود نیست"
                db.execute("INSERT INTO videos (title, title_ps, url, kind, source, created_at) "
                           "VALUES ('ویدیو بدون لینک', 'ویډیو بې لینکه', '', 'youtube', '', '2026-01-01T00:00:00Z')")
        status, _, headers = http_json(cls.base, '/api/admin/login', {'username': 'admin', 'password': 'panah1234'})
        cls.admin_cookie = '; '.join(c.split(';')[0] for c in headers.get_all('Set-Cookie') or [])
        cls.videos = http_json(cls.base, '/api/videos')[1]['videos']
        SHOTS.mkdir(parents=True, exist_ok=True)
        cls.profile = STAND / f'chrome-home-{os.getpid()}'
        cls.proc = subprocess.Popen(
            [find_chrome(), '--headless=new', f'--user-data-dir={cls.profile}', '--remote-debugging-port=0',
             '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--hide-scrollbars',
             '--disable-background-networking', '--lang=fa', '--autoplay-policy=user-gesture-required', 'about:blank'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = cls.profile / 'DevToolsActivePort'
        end = time.time() + 30
        while not (port_file.is_file() and port_file.read_text().strip()) and time.time() < end:
            time.sleep(0.1)
        port = int(port_file.read_text().split()[0])
        pages = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=10))
        cls.ws = WS(next(p['webSocketDebuggerUrl'] for p in pages if p.get('type') == 'page'))
        cls.cdp = CDP(cls.ws)
        for m in ('Runtime.enable', 'Log.enable', 'Page.enable', 'Network.enable'):
            cls.cdp.call(m)
        origin = cls.base.rstrip('/')
        cls.cdp.call('Browser.grantPermissions', {'origin': origin, 'permissions': ['geolocation']})
        cls.set_place(*KABUL)
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        for alert_id in cls.created:      # end our own alerts (especially on a live server)
            try:
                http_json(cls.base, f'/api/admin/alerts/{alert_id}/end', {}, cookie=cls.admin_cookie)
            except Exception:
                pass
        try:
            cls.cdp.call('Browser.close', timeout=5)
        except Exception:
            pass
        cls.ws.close()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        if cls.srv:
            cls.srv.__exit__(None, None, None)
        for _ in range(20):
            shutil.rmtree(cls.profile, ignore_errors=True)
            if not cls.profile.exists():
                break
            time.sleep(0.3)

    # ------------------------------------------------------------ helpers
    @classmethod
    def set_place(cls, lat, lon):
        cls.cdp.call('Emulation.setGeolocationOverride', {'latitude': lat, 'longitude': lon, 'accuracy': 25})

    def open(self, route, ready, width=440, height=956):
        c = self.cdp
        type(self).n += 1
        c.call('Emulation.setDeviceMetricsOverride', {'width': width, 'height': height, 'deviceScaleFactor': 2, 'mobile': True})
        c.call('Page.navigate', {'url': f'{self.base}?t={self.n}{route}'})
        self.assertTrue(self.wait(ready, timeout=25), f'{route}: screen not ready ({ready}); errors: {self.errors()}')
        c.eval('document.fonts.ready.then(() => true)', wait=True)

    def errors(self, net=True):
        """Page errors: exceptions and console.error; network ones come from Network: our server answered ≥ 400 or a request
        failed (guest: /api/me → 401 is by the core's design). Other hosts (OSM tiles, YouTube) offline are not an error."""
        out, urls = [], {}
        own = self.base.rstrip('/')
        for e in self.cdp.events:
            m, p = e['method'], e.get('params', {})
            if not net:
                continue    # offline, our own requests are expected to fail: look only at code errors
            if m == 'Network.requestWillBeSent':
                urls[p['requestId']] = p['request']['url']
            elif m == 'Network.responseReceived':
                r = p['response']
                if (r['url'].startswith(own) and r['status'] >= 400
                        and not (r['status'] == 401 and r['url'].split('?')[0].endswith('/api/me'))):
                    out.append(f"http {r['status']} {r['url']}")
            elif m == 'Network.loadingFailed' and not p.get('canceled'):
                url = urls.get(p['requestId'], '')
                if url.startswith(own) and '/api/stream' not in url:
                    out.append(f"failed {p.get('errorText')} {url}")
        out += [x for x in self.cdp.errors() if not x.startswith('log: Failed to load resource')]
        for e in self.cdp.events:   # and page warnings (for example, "Unrecognized feature" in an iframe's allow)
            m, p = e['method'], e.get('params', {})
            if m == 'Log.entryAdded' and p.get('entry', {}).get('level') == 'warning':
                out.append(f"warning: {p['entry'].get('text', '')} {p['entry'].get('url', '')}")
            elif m == 'Runtime.consoleAPICalled' and p.get('type') == 'warning':
                out.append('console.warn: ' + ' '.join(str(a.get('value', a.get('description', ''))) for a in p.get('args', [])))
        return out

    def no_errors(self, what, net=True):
        self.assertEqual(self.errors(net), [], f'{what}: console errors')
        self.cdp.events.clear()

    def no_scroll(self, what, widths=(440, 360)):
        c = self.cdp
        for w in widths:
            c.call('Emulation.setDeviceMetricsOverride', {'width': w, 'height': 900, 'deviceScaleFactor': 2, 'mobile': True})
            time.sleep(0.5)
            r = c.eval(OVERFLOW_JS)
            self.assertLessEqual(r['sw'], w, f'{what}: horizontal scroll at {w}: {r}')
        c.call('Emulation.setDeviceMetricsOverride', {'width': 440, 'height': 956, 'deviceScaleFactor': 2, 'mobile': True})
        time.sleep(0.3)

    def shot(self, name):
        c = self.cdp
        c.eval('window.scrollTo(0, 0); document.querySelectorAll(".toast").forEach((t) => t.remove()); true')
        time.sleep(0.4)
        r = c.call('Page.captureScreenshot', {'format': 'png', 'clip': {'x': 0, 'y': 0, 'width': 440, 'height': 956, 'scale': 1}})
        (SHOTS / name).write_bytes(base64.b64decode(r['data']))

    def wait(self, expr, timeout=25):
        """Wait until the expression is true (a DOM node comes back by value as {}, hence the !!)."""
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.cdp.eval(f'!!({expr})'):
                    return True
            except RuntimeError:
                pass        # the page is still loading (no App yet): keep waiting
            time.sleep(0.2)
        return False

    def setUp(self):
        self.cdp.events.clear()

    def wait_async(self, expr, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            if self.cdp.eval(expr, wait=True):
                return True
            time.sleep(0.3)
        return False

    def text(self, selector):
        return self.cdp.eval(f'(document.querySelector({json.dumps(selector)}) || {{}}).textContent || ""')

    def t(self, key, lang='fa'):
        d = {**common_dict(lang), **group_dict(lang)}
        return d.get(key) or common_dict('fa').get(key) or group_dict('fa')[key]

    def add_alert(self, body):
        status, r, _ = http_json(self.base, '/api/admin/alerts', body, cookie=self.admin_cookie)
        self.assertEqual(status, 201)
        type(self).created.append(r['alert']['id'])
        return r['alert']

    def end_alert(self, alert_id):
        http_json(self.base, f'/api/admin/alerts/{alert_id}/end', {}, cookie=self.admin_cookie)
        if alert_id in self.created:
            self.created.remove(alert_id)

    # ------------------------------------------------------------ steps (in name order)
    def test_1_home_05_and_live_06(self):
        c = self.cdp
        c.events.clear()
        self.open('#/home', 'document.querySelector(".hm-home .banner")')
        # location from GPS (faked via CDP): distances on past alerts
        self.assertTrue(self.wait('Math.abs(((App.location.saved() || {}).lat || 0) - 34.5553) < 1e-3', 15), 'no GPS location')
        self.assertTrue(self.wait('document.querySelector(".hm-home .banner--ok")', 10), 'no green banner (05)')
        self.assertIn(self.t('home.noAlert'), self.text('.banner'))
        self.assertIn(self.t('home.niceDay'), self.text('.banner'))
        n_past = c.eval('document.querySelectorAll(".hm-past__card").length')
        badge = c.eval('(document.querySelector(".section-title .badge") || {}).textContent || ""')
        if not EXTERNAL:
            self.assertEqual(badge, '۱۵', 'the badge is the number of past alerts (15 USGS quakes)')
            self.assertEqual(n_past, 4)
            self.assertTrue(self.wait('[...document.querySelectorAll(".hm-past__card")].every((x) => x.querySelectorAll(".hm-past__meta").length === 2)', 10),
                            'past alerts have no distance')
            self.assertIn('بزرگی', self.text('.hm-past__card'))
            c.eval('document.querySelector(".section-title__toggle").click(); true')
            self.assertTrue(self.wait('document.querySelectorAll(".hm-past__card").length === 15'), 'the arrow did not expand the list')
            c.eval('document.querySelector(".section-title__toggle").click(); true')
            self.assertTrue(self.wait('document.querySelectorAll(".hm-past__card").length === 4'))
        self.assertEqual(c.eval('document.querySelector(".tabbar--resident .is-on").dataset.tab'), 'home')
        self.assertEqual(c.eval('document.querySelector(".hm-bar [data-go=\\"#/search\\"]") ? 1 : 0'), 1)
        self.shot('05-home-no-alert.png')
        self.no_scroll('05')
        self.no_errors('05')

        # an alert nearby, created by the admin; the banner changes by itself, no reload (live SSE update)
        c.eval('window.__same = 1; true')
        self.assertTrue(self.wait('App.live.status === "open"', 10), 'no live /api/stream stream')
        lat, lon = destination(*KABUL, 12.0, 90)
        image = http_json(self.base, '/api/upload', {'data_url': PNG_1PX, 'name': 'flood.png'}, cookie=self.admin_cookie)[1]['url']
        flood = self.add_alert({'type': 'flood', 'lat': lat, 'lon': lon, 'safe_place': ZARNEGAR[0], 'image': image,
                                'safe_lat': ZARNEGAR[1], 'safe_lon': ZARNEGAR[2]})
        self.assertTrue(self.wait('document.querySelector(".hm-home .banner--alert")', 15), 'the banner did not change by itself (06)')
        self.assertEqual(c.eval('window.__same'), 1, 'the page reloaded; a live update was expected')
        self.assertIn(self.t('home.alertFlood'), self.text('.banner'))
        self.assertIn('به دوری ۱۲ کیلو متر', self.text('.banner'))
        self.assertTrue(self.wait('[...document.querySelectorAll(".toast")].some((t) => t.textContent.includes("هشدار جدید"))', 5), 'no toast')
        self.assertEqual(c.eval('document.querySelector(".hm-home a.banner").getAttribute("href")'), f'#/alert/{flood["id"]}')
        self.shot('06-home-flood-alert.png')
        self.no_scroll('06')
        # an alert with the resident inside its radius: brighter and "شما در منطقه خطر هستید"
        lat, lon = destination(*KABUL, 0.4, 200)
        quake = self.add_alert({'type': 'earthquake', 'lat': lat, 'lon': lon, 'safe_place': ZARNEGAR[0],
                                'safe_lat': ZARNEGAR[1], 'safe_lon': ZARNEGAR[2]})
        self.assertTrue(self.wait('document.querySelector(".hm-home .banner--danger")', 15), 'no red "in the zone" banner')
        self.assertIn(self.t('home.inDanger'), self.text('.banner'))
        self.assertIn(self.t('home.alertQuake'), self.text('.banner'))
        self.shot('06a-home-in-danger.png')
        type(self).flood, type(self).quake = flood, quake
        self.no_errors('06')

    def test_2_alert_screen(self):
        c = self.cdp
        self.open('#/home', 'document.querySelector(".hm-home .banner--danger")')
        c.eval('document.querySelector(".hm-home a.banner").click(); true')
        self.assertTrue(self.wait(f'location.hash === "#/alert/{self.quake["id"]}"'), c.eval('location.hash'))
        self.assertTrue(self.wait('document.querySelector(".hm-alert .leaflet-container") && document.querySelectorAll(".hm-alert .leaflet-marker-icon").length >= 4'),
                        'no map with markers (alert, you, safe places)')
        self.assertTrue(self.wait('document.querySelector(".hm-alert path.leaflet-interactive")'), 'no radius circle')
        self.assertIn(self.t('home.inDanger'), self.text('.hm-alert .banner'))
        info = self.text('.hm-info')
        for key in ('home.alert.type', 'home.alert.time', 'home.alert.place', 'home.alert.distance', 'home.alert.safePlace'):
            self.assertIn(self.t(key), info)
        self.assertIn(ZARNEGAR[0], info)
        self.assertRegex(info, '[۰-۹]{4}', 'no solar-calendar date')
        self.assertTrue(any(m in info for m in [self.t(f'month.{i}') for i in range(1, 13)]), 'no month حمل…حوت')
        routes = c.eval('[...document.querySelectorAll(".hm-alert a.hm-route")].map((a) => [a.href, a.target])')
        self.assertGreaterEqual(len(routes), 2)
        for href, target in routes:
            self.assertTrue(href.startswith('https://www.openstreetmap.org/directions?'), href)
            self.assertIn('route=34.555', urllib.parse.unquote(href), "the route starts at the resident's location")
            self.assertEqual(target, '_blank')
        self.assertEqual(c.eval('document.querySelectorAll(".hm-place").length'), 3, '3 nearest safe places expected')
        self.wait('[...document.querySelectorAll(".hm-alert .leaflet-tile")].every((i) => i.complete)', timeout=10)
        self.shot('05a-alert-map.png')
        self.no_scroll('alert')
        # the admin ended the alert: the screen changes by itself
        self.end_alert(self.quake['id'])
        self.assertTrue(self.wait('document.querySelector(".hm-alert .hm-banner--ended")', 15), 'not shown that the alert has ended')
        self.assertIn(self.t('home.alert.ended'), self.text('.hm-alert .banner'))
        self.no_errors('alert')
        # the alert image (POST /api/upload → image) goes under the map
        self.open(f'#/alert/{self.flood["id"]}', 'document.querySelector(".hm-alert .leaflet-container")')
        self.assertTrue(self.wait('document.querySelector(".hm-figure img") && document.querySelector(".hm-figure img").complete '
                                  '&& document.querySelector(".hm-figure img").naturalWidth > 0'), 'no alert image')
        self.assertIn(self.t('home.alertFlood'), self.text('.hm-alert .banner'))
        self.assertIn('۱۲', self.text('.hm-alert .banner'))
        # the ended one is among the past ones on home; the banner is about the nearest active one again
        self.open('#/home', 'document.querySelector(".hm-home .banner--alert")')
        self.assertTrue(self.wait(f'!!document.querySelector(\'.hm-past__card[data-alert="{self.quake["id"]}"]\')'), 'the ended alert is not among the past ones')

    def test_3_search_09(self):
        c = self.cdp
        self.open('#/search?q=' + urllib.parse.quote('سیلاب'), 'document.querySelector(".hm-search .hm-results .hm-group, .hm-search .hm-results .empty")')
        self.assertEqual(c.eval('document.querySelector(".hm-sbar__input").value'), 'سیلاب')
        self.assertTrue(self.wait(f'!!document.querySelector(\'.hm-group[data-group="alerts"] a[href="#/alert/{self.flood["id"]}"]\')'),
                        'flood alert not found')
        self.assertEqual(c.eval('document.querySelector(".tabbar") ? 1 : 0'), 0, 'screen 09 must not have the bottom menu')
        self.shot('09-search.png')
        self.no_scroll('09')
        # typing in the field: places and numbers; × clears
        c.eval('(() => { const i = document.querySelector(".hm-sbar__input"); i.value = "۱۱۹"; i.dispatchEvent(new Event("input")); })(); true')
        self.assertTrue(self.wait('!!document.querySelector(\'.hm-group[data-group="numbers"] a[href="tel:119"]\')'), 'number 119 not found')
        self.assertIn(urllib.parse.quote('۱۱۹'), c.eval('location.hash'))
        c.eval('(() => { const i = document.querySelector(".hm-sbar__input"); i.value = "کابل"; i.dispatchEvent(new Event("input")); })(); true')
        self.assertTrue(self.wait('document.querySelectorAll(\'.hm-group[data-group="places"] a[target="_blank"]\').length > 0'), 'places not found')
        c.eval('document.querySelector(".hm-sbar__clear").click(); true')
        self.assertTrue(self.wait('document.querySelector(".hm-sbar__input").value === "" && !!document.querySelector(".hm-hint")'))
        c.eval('(() => { const i = document.querySelector(".hm-sbar__input"); i.value = "zzqqxx"; i.dispatchEvent(new Event("input")); })(); true')
        self.assertTrue(self.wait('!!document.querySelector(".hm-results .empty")'), 'no "نتیجه‌ای پیدا نشد"')
        self.no_errors('09')

    def test_4_videos(self):
        c = self.cdp
        self.open('#/videos', 'document.querySelector(".hm-videos .hm-vcard, .hm-videos .empty")')
        self.assertEqual(c.eval('document.querySelectorAll(".hm-vcard").length'), len(self.videos))
        self.assertEqual(c.eval('document.querySelector(".tabbar--resident .is-on").dataset.tab'), 'videos')
        self.assertRegex(self.text('.hm-vcard__date'), '[۰-۹]')
        self.shot('05b-videos.png')
        self.no_scroll('videos')
        yt = next((v for v in self.videos if v.get('youtube_id')), None)
        if yt:
            self.open(f'#/video/{yt["id"]}', 'document.querySelector(".hm-video .hm-player")')
            src = c.eval('document.querySelector(".hm-player iframe").src')
            self.assertTrue(src.startswith('https://www.youtube-nocookie.com/embed/' + yt['youtube_id']), src)
            self.assertEqual(c.eval('document.querySelector(".hm-player iframe").referrerPolicy'), 'strict-origin-when-cross-origin')
            time.sleep(3)   # the YouTube player loads from the network (offline: an empty black frame)
            self.shot('05c-video-player.png')
            self.no_scroll('player')
        no_link = next((v for v in self.videos if not v.get('youtube_id') and not (v.get('kind') == 'file' and v.get('url'))), None)
        if no_link:
            self.open(f'#/video/{no_link["id"]}', 'document.querySelector(".hm-video .hm-player")')
            self.assertIn(self.t('home.video.missing'), self.text('.hm-player'))
            self.no_scroll('video without a link')
        elif not EXTERNAL:
            self.fail('no video without a link in the test database')
        self.open('#/video/999999', 'document.querySelector(".screen .empty")')
        self.assertIn(self.t('home.video.notFound'), self.text('.screen'))
        self.no_errors('videos')

    def test_5_numbers_07(self):
        c = self.cdp
        self.open('#/numbers', 'document.querySelector(".hm-numbers .hm-num, .hm-numbers .empty")')
        numbers = http_json(self.base, '/api/numbers')[1]['numbers']
        self.assertEqual(c.eval('[...document.querySelectorAll(".hm-num")].map((a) => a.getAttribute("href"))'),
                         ['tel:' + n['number'] for n in numbers])
        self.assertIn(self.t('home.callNumbers'), self.text('.hm-lead'))
        self.assertEqual(c.eval('document.querySelector(".tabbar--resident .is-on").dataset.tab'), 'call')
        self.assertEqual(c.eval('document.querySelector(".hm-top__back").getBoundingClientRect().left < 100'), True, '← on the left, as in 08')
        self.shot('07-numbers.png')
        self.no_scroll('numbers')
        self.no_errors('numbers')

    def test_6_pashto_and_english(self):
        c = self.cdp
        screens = [('#/home', '.hm-home .banner'), (f'#/alert/{self.flood["id"]}', '.hm-alert .leaflet-container'),
                   ('#/search?q=' + urllib.parse.quote('سیلاب'), '.hm-results .hm-group'), ('#/videos', '.hm-vcard'),
                   ('#/numbers', '.hm-num')]
        try:
            for lang in ('ps', 'en'):
                c.eval(f'App.setLang("{lang}").then(() => true)', wait=True)
                for route, sel in screens:
                    self.open(route, f'document.querySelector("{sel}")', width=360, height=800)
                    self.assertEqual(c.eval('document.documentElement.dir'), 'ltr' if lang == 'en' else 'rtl')
                    self.no_scroll(f'{lang} {route}', widths=(360,))
                    self.assertEqual(c.eval('[...App.i18n.missing]'), [], f'{lang} {route}: strings missing')
                self.open('#/home', 'document.querySelector(".hm-home .banner--alert")')
                self.assertIn(self.t('home.alertFlood', lang), self.text('.banner'))
                if lang == 'en':
                    self.assertIn('12 km away', self.text('.banner'))
                self.no_errors(lang)
        finally:
            c.eval('App.setLang("fa").then(() => true)', wait=True)

    def test_7_resident_location(self):
        """Logged-in resident: location via POST /api/me/location, at most once per 5 minutes; the profile icon → #/profile."""
        c = self.cdp
        self.open('#/home', 'document.querySelector(".hm-home .banner")')
        phone = '0799' + ''.join(random.choice('0123456789') for _ in range(6))
        uid = c.eval('''(async () => {
            const r = await App.api('POST', '/api/auth/code', {phone: '%s'});
            const v = await App.api('POST', '/api/auth/verify', {phone: '%s', code: r.test_code});
            App.store.del('home.locSent');
            return v.user.id; })()''' % (phone, phone), wait=True)
        self.assertTrue(uid)
        try:
            self.open('#/home', 'App.state.user && document.querySelector(".hm-home .banner")')
            self.assertEqual(c.eval('document.querySelector(".hm-bar [data-go]").dataset.go'), '#/profile')
            me = 'App.api("GET", "/api/me").then((r) => (r.user || r).last_lat)'
            self.assertTrue(self.wait_async(f'{me}.then((v) => Math.abs((v || 0) - 34.5553) < 1e-3)'), 'location not sent to the server')
            # a minute later the resident is elsewhere: nothing is sent (5 minutes have not passed), but distances are recalculated
            self.set_place(34.6, 69.1)
            self.open('#/home', 'document.querySelector(".hm-home .banner")')
            self.assertTrue(self.wait('Math.abs(((App.location.saved() || {}).lat || 0) - 34.6) < 1e-3', 15))
            time.sleep(1.5)
            self.assertLess(abs((c.eval(me, wait=True) or 0) - KABUL[0]), 1e-3, 'location sent before 5 minutes passed')
            self.no_errors('resident')
        finally:
            self.set_place(*KABUL)
            c.eval('App.api("POST", "/api/auth/logout").then(() => true, () => true)', wait=True)

    def test_8_guest_without_place(self):
        """Guest without GPS (a phone over plain http) and without a saved location: home at once offers the address
        search and «انتخاب روی نقشه» (no sheet pops up by itself); a search result becomes "my place" and the
        distances appear. On the alert screen: a clear «پیدا کردن موقعیت من» button right on the map; the sheet has the
        search above the map; after a tap on the map the blue "you" dot with the label «شما» is on the map."""
        c = self.cdp
        origin = self.base.rstrip('/')
        c.call('Browser.setPermission', {'permission': {'name': 'geolocation'}, 'setting': 'denied', 'origin': origin})
        restore = None
        if not EXTERNAL:   # address search without the internet: real Nominatim answer from tests/data
            from server import geo
            canned = (TESTS / 'data' / 'nominatim_search_kabul_university.json').read_text(encoding='utf-8')
            restore = geo.search_places
            geo.search_places = lambda q, lang='fa', fetch=None, limit=geo.SEARCH_LIMIT: restore(
                q, lang, fetch=lambda url: canned, limit=limit)
        try:
            self.open('#/numbers', 'document.querySelector(".hm-num")')
            c.eval('localStorage.removeItem("panah.location"); true')
            self.open('#/home', 'document.querySelector(".hm-home [data-hm-locask] .hm-locsearch input")')
            time.sleep(1)
            self.assertEqual(c.eval('document.querySelectorAll(".sheet").length'), 0, 'a sheet popped up by itself')
            self.assertEqual(c.eval('document.querySelectorAll(".hm-past__card .ic-pin").length'), 0, 'a distance without a location?')
            self.assertIn(self.t('home.loc.unknown'), self.text('[data-hm-locask]'))
            self.assertIn(self.t('home.loc.onMap'), self.text('[data-hm-pick]'))
            self.shot('05e-home-find-my-place.png')
            self.no_scroll('find my place', widths=(360,))
            if not EXTERNAL:
                c.eval('(() => { const i = document.querySelector("[data-hm-locask] .hm-locsearch input"); i.value = "پوهنتون کابل";'
                       ' document.querySelector("[data-hm-locask] .hm-locsearch").requestSubmit(); })(); true')
                self.assertTrue(self.wait('document.querySelectorAll("[data-hm-locask] .hm-locrow").length > 0', 10), 'no search results')
                self.assertIn('کابل', self.text('[data-hm-locask] .hm-locresults'))
                c.eval('document.querySelector("[data-hm-locask] .hm-locrow").click(); true')
                self.assertTrue(self.wait('App.location.saved() && App.location.saved().source === "picked" && !document.querySelector("[data-hm-locask]")'),
                                'the search result did not become my place')
                self.assertTrue(self.wait('document.querySelectorAll(".hm-past__card .ic-pin").length > 0'), 'no distances after the search')
                self.assertTrue(abs(c.eval('App.location.saved().lat') - 34.51) < 0.05, 'not the found place')
            # alert screen without a location: the button right on the map
            c.eval('localStorage.removeItem("panah.location"); true')
            self.open(f'#/alert/{self.flood["id"]}', 'document.querySelector(".hm-alert .leaflet-container")')
            self.assertTrue(self.wait('document.querySelector(".hm-mapbtn.hm-mapbtn--big")'), 'no «find my location» button on the map')
            self.assertIn(self.t('home.loc.findMine'), self.text('.hm-mapbtn'))
            self.assertFalse(c.eval('!!document.querySelector(".hm-me")'), '"you" without a location?')
            c.eval('document.querySelector(".hm-mapbtn").click(); true')
            self.assertTrue(self.wait('document.querySelector(".sheet .hm-locsearch") && document.querySelector(".sheet [data-hm-pickmap].leaflet-container")'),
                            'the sheet has no search or map')
            self.assertTrue(c.eval('!!(document.querySelector(".sheet .hm-locsearch").compareDocumentPosition('
                                   'document.querySelector(".sheet [data-hm-pickmap]")) & Node.DOCUMENT_POSITION_FOLLOWING)'),
                            'the search must be above the map')
            self.assertFalse(c.eval('!!document.querySelector(".sheet [data-hm-gps]")'), 'GPS offered although the browser refused it')
            time.sleep(0.6)
            box = c.eval('(() => { const r = document.querySelector(".sheet [data-hm-pickmap]").getBoundingClientRect(); '
                         'return [r.left + r.width / 2, r.top + r.height / 2]; })()')
            for kind in ('mousePressed', 'mouseReleased'):
                c.call('Input.dispatchMouseEvent', {'type': kind, 'x': box[0], 'y': box[1], 'button': 'left', 'clickCount': 1})
            self.assertTrue(self.wait('!document.querySelector(".sheet [data-hm-ok]").disabled', 5), 'no point picked on the map')
            c.eval('document.querySelector(".sheet [data-hm-ok]").click(); true')
            self.assertTrue(self.wait('!document.querySelector(".sheet") && App.location.saved() && App.location.saved().source === "picked"'))
            self.assertTrue(self.wait('document.querySelector(".hm-alert .leaflet-marker-icon.hm-me .hm-me__label")'), 'no "you" marker after choosing')
            self.assertEqual(self.text('.hm-me__label'), self.t('home.alert.you'))
            self.assertTrue(self.wait('document.querySelector(".hm-mapbtn") && !document.querySelector(".hm-mapbtn--big")'))
            self.assertIn(self.t('home.alert.distance'), self.text('.hm-info'))
            c.wait_for('[...document.querySelectorAll(".hm-alert .leaflet-tile")].every((i) => i.complete)', timeout=8)
            self.shot('05f-alert-map-me.png')
            for lang, word in (('en', 'You'), ('ps', 'تاسو')):
                c.eval(f'App.setLang("{lang}").then(() => true)', wait=True)
                self.assertTrue(self.wait(f'(document.querySelector(".hm-me__label") || {{}}).textContent === "{word}"', 8), f'{lang}: "you" label')
            c.eval('App.setLang("fa").then(() => true)', wait=True)
            self.no_errors('guest without location')
        finally:
            if restore is not None:
                from server import geo
                geo.search_places = restore
            c.eval('App.setLang("fa").then(() => true, () => true)', wait=True)
            c.call('Browser.setPermission', {'permission': {'name': 'geolocation'}, 'setting': 'granted', 'origin': origin})
            self.set_place(*KABUL)

    def test_9a_slow_network_no_stale_banner(self):
        """Experiment 10: on Slow 3G an /api/alerts response requested before the alert ended arrives after the alert_end event;
        the ended alert must not reappear on the banner (request number + events on top of what was loaded)."""
        c = self.cdp
        self.open('#/home', 'document.querySelector(".hm-home .banner")')
        self.assertTrue(self.wait('App.live.status === "open"', 10))
        c.eval("""(() => {
          window.__tl = []; window.__ends = {}; window.__req = [];
          const rec = () => { const b = document.querySelector('.hm-home .banner'); window.__tl.push([Date.now(), b ? (b.getAttribute('href') || 'ok') : null]); };
          new MutationObserver(rec).observe(document.getElementById('app'), { childList: true, subtree: true });
          App.live.on('alert_end', (x) => { window.__ends[x.id] = Date.now(); }, { global: true });
          const f = window.fetch;
          window.fetch = (u, o) => { const r = { u: String(u), s: Date.now() }; window.__req.push(r);
            return f(u, o).then((x) => { r.d = Date.now(); return x; }); };
          return true; })()""")
        c.call('Network.emulateNetworkConditions', SLOW_3G)   # the open event stream gets no delay (experiment 10)
        ids = []
        try:
            for i in range(3):
                lat, lon = destination(*KABUL, 0.3 + 0.1 * i, 40 * i)
                a = self.add_alert({'type': 'earthquake', 'lat': lat, 'lon': lon, 'safe_place': ZARNEGAR[0],
                                    'safe_lat': ZARNEGAR[1], 'safe_lon': ZARNEGAR[2]})
                ids.append(a['id'])
                self.assertTrue(self.wait(f'document.querySelector(\'.hm-home .banner[href="#/alert/{a["id"]}"]\')', 5),
                                'the new alert is not on the banner right after the event')
                time.sleep(1.0)
                self.end_alert(a['id'])
                self.assertTrue(self.wait(f'window.__ends[{a["id"]}]', 5), 'no alert_end event')
                self.assertTrue(self.wait(f'!document.querySelector(\'.hm-home .banner[href="#/alert/{a["id"]}"]\')', 2),
                                'the alert end is not shown right after the event')
            time.sleep(7)   # late responses (requested before the alert ended) arrive 2–3 s later
            tl, ends, req = c.eval('[window.__tl, window.__ends, window.__req]')
            raced = [r for r in req if '/api/alerts' in r['u'] and r.get('d')
                     and any(r['s'] < ends[str(aid)] < r['d'] for aid in ids)]
            self.assertTrue(raced, f'no race happened, the check proves nothing: {req}')
            for aid in ids:
                back = [x for x in tl if x[0] > ends[str(aid)] and x[1] == f'#/alert/{aid}']
                self.assertEqual(back, [], f'ended alert {aid} popped up on the banner again')
            self.assertNotIn(c.eval('document.querySelector(".hm-home .banner").getAttribute("href")'),
                             [f'#/alert/{aid}' for aid in ids])
            self.assertTrue(self.wait(' && '.join(f'!!document.querySelector(\'.hm-past__card[data-alert="{aid}"]\')' for aid in ids)
                                      + ' || document.querySelectorAll(".hm-past__card").length >= 4', 10), 'the ended ones are not among the past ones')
            self.no_errors('slow network')
        finally:
            c.call('Network.emulateNetworkConditions', NO_LIMIT)

    def test_9b_reload_after_stream_reconnect(self):
        """The stream dropped (server restart); meanwhile an alert appeared that no event will announce
        (a page without Last-Event-ID, experiment 10); after the reconnect, home reloads the banner and the list."""
        if EXTERNAL:
            self.skipTest('needs our own server: dropping streams and writing to the database without an event')
        c = self.cdp
        self.open('#/home', 'document.querySelector(".hm-home .banner")')
        self.assertTrue(self.wait('App.live.status === "open"', 10))
        c.eval('window.__same = 1; true')
        self.srv.app.hub.close_all()        # the server dropped the streams, as on a restart
        self.assertTrue(self.wait('App.live.status !== "open"', 5), 'the page did not notice the stream drop')
        lat, lon = destination(*KABUL, 0.5, 120)
        now = utcnow()
        with self.srv.app.transaction() as db:
            cur = db.execute("INSERT INTO alerts (type, source, lat, lon, radius_km, starts_at, status, created_at) "
                             "VALUES ('earthquake', 'admin', ?, ?, 2.0, ?, 'active', ?)", (lat, lon, now, now))
            aid = cur.lastrowid
        type(self).created.append(aid)
        self.assertTrue(self.wait(f'document.querySelector(\'.hm-home .banner[href="#/alert/{aid}"]\')', 15),
                        'the banner was not reloaded after the stream reconnected')
        self.assertEqual(c.eval('window.__same'), 1, 'the page reloaded; a refresh without a reload was expected')
        self.end_alert(aid)
        self.assertTrue(self.wait(f'!document.querySelector(\'.hm-home .banner[href="#/alert/{aid}"]\')', 10))
        self.no_errors('reconnect')

    def test_9c_offline_cache(self):
        """Offline: the latest alerts, numbers and videos come from localStorage with "آخرین به‌روزرسانی: …"; numbers still call (tel:);
        when the connection is back, home reloads by itself and the mark goes away."""
        c = self.cdp
        numbers = http_json(self.base, '/api/numbers')[1]['numbers']
        for route, ready in (('#/numbers', '.hm-num'), ('#/videos', '.hm-vcard'), (f'#/alert/{self.flood["id"]}', '.hm-alert .banner'),
                             ('#/home', '.hm-home .banner')):
            self.open(route, f'document.querySelector("{ready}")')   # online: the cache gets filled
        self.assertTrue(self.wait('["alerts", "numbers", "videos", "alert"].every((k) => localStorage.getItem("panah.home.cache." + k))', 5),
                        'no cache in localStorage')
        stale = self.t('home.lastUpdate').split('{')[0].strip()
        c.events.clear()
        c.call('Network.emulateNetworkConditions', OFFLINE)
        try:
            self.assertTrue(self.wait('navigator.onLine === false', 5))
            self.assertTrue(self.wait('document.querySelector(".hm-home .hm-stale")', 5), 'home did not mark the data as stale')
            self.assertIn(stale, self.text('.hm-home .hm-stale'))
            self.assertIn(self.t('home.alertFlood'), self.text('.hm-home .banner'))
            c.eval('location.hash = "#/numbers"; true')
            self.assertTrue(self.wait('document.querySelector(".hm-numbers .hm-num") && document.querySelector(".hm-numbers .hm-stale")', 8),
                            'no numbers offline')
            self.assertIn(stale, self.text('.hm-numbers .hm-stale'))
            self.assertRegex(self.text('.hm-numbers .hm-stale'), '[۰-۹]', 'no last-update time')
            self.assertEqual(c.eval('[...document.querySelectorAll(".hm-num")].map((a) => a.getAttribute("href"))'),
                             ['tel:' + n['number'] for n in numbers], 'offline, the numbers must still call')
            self.shot('07a-numbers-offline.png')
            c.eval('location.hash = "#/videos"; true')
            self.assertTrue(self.wait('document.querySelector(".hm-videos .hm-vcard") && document.querySelector(".hm-videos .hm-stale")', 8))
            self.assertEqual(c.eval('document.querySelectorAll(".hm-vcard").length'), len(self.videos))
            c.eval(f'location.hash = "#/video/{self.videos[0]["id"]}"; true')
            self.assertTrue(self.wait('document.querySelector(".hm-video .hm-player") && document.querySelector(".hm-video .hm-stale")', 8))
            c.eval(f'location.hash = "#/alert/{self.flood["id"]}"; true')
            self.assertTrue(self.wait('document.querySelector(".hm-alert .banner") && document.querySelector(".hm-alert .hm-stale")', 8),
                            'offline, the alert opened earlier is missing')
            c.eval('location.hash = "#/home"; true')
            self.assertTrue(self.wait('document.querySelector(".hm-home .banner") && document.querySelector(".hm-home .hm-stale") '
                                      '&& document.querySelectorAll(".hm-past__card").length > 0', 8), 'no home offline')
            self.assertIn(self.t('home.alertFlood'), self.text('.hm-home .banner'))
            self.no_errors('offline', net=False)
        finally:
            c.call('Network.emulateNetworkConditions', NO_LIMIT)
        self.assertTrue(self.wait('navigator.onLine === true', 5))
        self.assertTrue(self.wait('document.querySelector(".hm-home .banner") && !document.querySelector(".hm-home .hm-stale")', 10),
                        'the connection is back, but home was not reloaded')
        c.eval('location.hash = "#/numbers"; true')
        self.assertTrue(self.wait('document.querySelector(".hm-numbers .hm-num") && !document.querySelector(".hm-numbers .hm-stale")', 8))

    def test_9d_geofence_edge_live_banner(self):
        """Experiment 02 in the browser: a live alert event at 1 999.6 / 2 000.0 / 2 000.4 m — the banner drawn from
        the event (before the server's answer: Slow 3G holds GET /api/alerts for 2 s) says "inside" exactly when the
        server does (red «شما در منطقه خطر هستید» vs orange)."""
        c = self.cdp
        self.open('#/home', 'document.querySelector(".hm-home .banner")')
        self.assertTrue(self.wait('App.live.status === "open"', 10))
        self.assertTrue(self.wait('Math.abs(((App.location.saved() || {}).lat || 0) - 34.5553) < 1e-9', 10))
        c.call('Network.emulateNetworkConditions', SLOW_3G)
        try:
            for metres in (1999.6, 2000.0, 2000.4):
                lat, lon = destination(*KABUL, metres / 1000, 90)
                a = self.add_alert({'type': 'earthquake', 'lat': lat, 'lon': lon, 'safe_place': ZARNEGAR[0],
                                    'safe_lat': ZARNEGAR[1], 'safe_lon': ZARNEGAR[2]})
                sel = f'.hm-home .banner[href="#/alert/{a["id"]}"]'
                self.assertTrue(self.wait(f'document.querySelector(\'{sel}\')', 1.5),
                                f'{metres} m: the banner did not follow the live event at once')
                client = c.eval(f'document.querySelector(\'{sel}\').classList.contains("banner--danger")')
                server = next(x for x in http_json(self.base, f'/api/alerts?lat={KABUL[0]}&lon={KABUL[1]}')[1]['active']
                              if x['id'] == a['id'])['inside']
                self.assertEqual(server, metres < 2000.3, f'{metres} m: server rule changed?')
                self.assertEqual(client, server, f'{metres} m: the screen says inside={client}, the server inside={server}')
                self.end_alert(a['id'])
                self.assertTrue(self.wait(f'!document.querySelector(\'{sel}\')', 3))
            self.no_errors('geofence edge')
        finally:
            c.call('Network.emulateNetworkConditions', NO_LIMIT)

    def test_9e_alert_notice_on_other_screens(self):
        """A new alert while the resident is NOT on home (videos, numbers…): a notice at the top — red «شما در منطقه
        خطر هستید» inside the radius, orange with the distance otherwise; tap → #/alert/:id; vibration after the person
        has touched the page; three languages; never on admin screens."""
        c = self.cdp
        made = []
        try:
            self.open('#/videos', 'document.querySelector(".hm-vcard")')
            self.assertTrue(self.wait('App.live.status === "open"', 10))
            # the person touches the page (Chrome allows vibration only then); the call is recorded, not felt
            c.eval('window.__vib = []; navigator.vibrate = (p) => { window.__vib.push(p); return true; }; true')
            for kind in ('mousePressed', 'mouseReleased'):
                c.call('Input.dispatchMouseEvent', {'type': kind, 'x': 220, 'y': 60, 'button': 'left', 'clickCount': 1})
            lat, lon = destination(*KABUL, 0.8, 300)
            a = self.add_alert({'type': 'earthquake', 'lat': lat, 'lon': lon, 'safe_place': ZARNEGAR[0],
                                'safe_lat': ZARNEGAR[1], 'safe_lon': ZARNEGAR[2]})
            made.append(a['id'])
            sel = f'.hm-flash[data-alert="{a["id"]}"]'
            self.assertTrue(self.wait(f'document.querySelector(\'{sel}\')', 5), 'no notice on #/videos')
            self.assertTrue(c.eval(f'document.querySelector(\'{sel}\').classList.contains("hm-flash--danger")'), 'inside: red')
            self.assertIn(self.t('home.inDanger'), self.text('.hm-flash'))
            self.assertIn(self.t('home.alertQuake'), self.text('.hm-flash'))
            self.assertEqual(c.eval('document.querySelector(".tabbar--resident .is-on").dataset.tab'), 'videos', 'stayed on #/videos')
            self.assertTrue(self.wait('window.__vib.length === 1', 3), 'no vibration')
            self.shot('05d-alert-notice-videos.png')
            self.no_scroll('notice')
            c.eval('document.querySelector(".hm-flash__body").click(); true')
            self.assertTrue(self.wait(f'location.hash === "#/alert/{a["id"]}"'), 'tap did not open the alert')
            self.assertTrue(self.wait(f'document.querySelector(\'.hm-alert .banner\') && !document.querySelector(".hm-flash")'))
            self.end_alert(a['id'])
            # a farther alert, English, on #/numbers: orange with the distance; × closes it
            c.eval('App.setLang("en").then(() => true)', wait=True)
            c.eval('location.hash = "#/numbers"; true')
            self.assertTrue(self.wait('document.querySelector(".hm-numbers .hm-num")'))
            lat, lon = destination(*KABUL, 12.0, 200)
            b = self.add_alert({'type': 'flood', 'lat': lat, 'lon': lon, 'safe_place': ZARNEGAR[0],
                                'safe_lat': ZARNEGAR[1], 'safe_lon': ZARNEGAR[2]})
            made.append(b['id'])
            sel = f'.hm-flash[data-alert="{b["id"]}"]'
            self.assertTrue(self.wait(f'document.querySelector(\'{sel}.hm-flash--alert\')', 5), 'no orange notice on #/numbers')
            self.assertIn('Flood alert', self.text('.hm-flash'))
            self.assertIn('12 km away', self.text('.hm-flash'))
            self.no_scroll('notice en', widths=(360,))
            c.eval('document.querySelector(".hm-flash__close").click(); true')
            self.assertTrue(self.wait('!document.querySelector(".hm-flash")'))
            # Pashto: the notice in Pashto
            c.eval('App.setLang("ps").then(() => true)', wait=True)
            lat, lon = destination(*KABUL, 5.0, 250)
            d = self.add_alert({'type': 'flood', 'lat': lat, 'lon': lon, 'safe_place': ZARNEGAR[0],
                                'safe_lat': ZARNEGAR[1], 'safe_lon': ZARNEGAR[2]})
            made.append(d['id'])
            self.assertTrue(self.wait(f'document.querySelector(\'.hm-flash[data-alert="{d["id"]}"]\')', 5))
            self.assertIn(self.t('home.alertFlood', 'ps'), self.text('.hm-flash'))
            self.assertIn('لرې', self.text('.hm-flash'))
            # the alert ends — its notice goes away by itself
            self.end_alert(d['id'])
            self.assertTrue(self.wait('!document.querySelector(".hm-flash")', 5), 'the notice of an ended alert stayed')
            c.eval('App.setLang("fa").then(() => true)', wait=True)
            # admin screens: no notice, no vibration
            c.eval('location.hash = "#/admin/login"; true')
            self.assertTrue(self.wait('location.hash.startsWith("#/admin")'))
            vib = c.eval('window.__vib.length')
            lat, lon = destination(*KABUL, 1.0, 10)
            e = self.add_alert({'type': 'earthquake', 'lat': lat, 'lon': lon, 'safe_place': ZARNEGAR[0],
                                'safe_lat': ZARNEGAR[1], 'safe_lon': ZARNEGAR[2]})
            made.append(e['id'])
            time.sleep(2)
            self.assertFalse(c.eval('!!document.querySelector(".hm-flash")'), 'a notice on an admin screen')
            self.assertEqual(c.eval('window.__vib.length'), vib, 'vibration on an admin screen')
            self.no_errors('notice')
        finally:
            c.eval('App.setLang("fa").then(() => true, () => true)', wait=True)
            for alert_id in made:
                if alert_id in self.created:
                    self.end_alert(alert_id)


if __name__ == '__main__':
    unittest.main()
