"""Tests for the sos screen group (figma 10, 11/13, 12): app/web/js/screens/sos.js, css/sos.css, i18n/<lang>/sos.json.

Run from the project root:  python -m unittest app.tests.test_screens_sos   (or discover app/tests)
Without network and Chrome, only the file checks run. With Google Chrome: the resident's path on a live server (in this
process, database in a temporary folder, Nominatim off, place name from our own data): headless Chrome, location faked
to Kabul (34.5550, 69.2070). Screenshots at 440 px: stand/screens/sos/NN-<name>.png; the Chrome profile is in stand/,
deleted afterwards. Waits the real 30 s of location sending, so the whole class takes about a minute and a half.
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from support import Client, Running  # noqa: E402  (adds app/ to sys.path)
from test_ui_core import CDP, OVERFLOW_JS, WS, find_chrome  # noqa: E402

ROOT = HERE.parents[1]
WEB = ROOT / 'app' / 'web'
STAND = ROOT / 'stand'
SHOTS = STAND / 'screens' / 'sos'
LANGS = ('fa', 'ps', 'en')
KABUL = (34.5550, 69.2070)


def group_dict(lang):
    return json.loads((WEB / 'i18n' / lang / 'sos.json').read_text(encoding='utf-8'))


def all_dict(lang):
    d = json.loads((WEB / 'i18n' / f'{lang}.json').read_text(encoding='utf-8'))
    d.update(group_dict(lang))
    return d


class TestSosFiles(unittest.TestCase):
    def test_strings_exist_in_three_languages(self):
        """Every t('…') key from sos.js exists in Dari; the group's new keys are filled in both Pashto and English."""
        src = (WEB / 'js' / 'screens' / 'sos.js').read_text(encoding='utf-8')
        used = set(re.findall(r"\bt\('([\w.]+)'", src))
        used |= set(re.findall(r"'(sos\.[\w.]+)'", src))
        self.assertGreater(len(used), 30)
        fa = all_dict('fa')
        self.assertEqual(sorted(k for k in used if k not in fa), [], 'strings missing in Dari')
        mine = group_dict('fa')
        for lang in ('ps', 'en'):
            d = group_dict(lang)
            self.assertEqual(sorted(d), sorted(mine), f'{lang}: keys differ from Dari')
            for k, v in d.items():
                self.assertTrue(v.strip(), f'{lang}:{k} is empty')
                self.assertEqual(set(re.findall(r'\{(\w+)\}', v)), set(re.findall(r'\{(\w+)\}', mine[k])), f'{lang}:{k}')

    def test_css_logical_only(self):
        bad = re.compile(r'(?<![\w-])(margin|padding|border)-(left|right)\b|(?<![\w-])(left|right)\s*:|'
                         r'text-align\s*:\s*(left|right)|float\s*:\s*(left|right)|border-(top|bottom)-(left|right)-radius')
        text = re.sub(r'/\*.*?\*/', '', (WEB / 'css' / 'sos.css').read_text(encoding='utf-8'), flags=re.S)
        self.assertEqual([m.group(0) for m in bad.finditer(text)], [])

    def test_routes_registered(self):
        src = (WEB / 'js' / 'screens' / 'sos.js').read_text(encoding='utf-8')
        for route in ("'#/sos/now'", "'#/sos'", "'#/sos/sent'"):
            self.assertIn(f'route: {route}', src)
        self.assertIn('EVERY_MS = 30000', src)


@unittest.skipIf(os.environ.get('PANAH_SKIP_BROWSER'), 'PANAH_SKIP_BROWSER')
@unittest.skipUnless(find_chrome(), 'no Google Chrome')
class TestSosBrowser(unittest.TestCase):
    """Guest resident path: chat → first message → 12 → chat (one "back") → 13; rescuer reply arrives by itself; after
    'reconnect' the chat reloads; SOS with and without cancel → 10; location every 30 s, also on another screen; closing;
    offline: queue and call 119, then the normal path without duplicates; 360 and 440 px; English; no console errors."""

    @classmethod
    def setUpClass(cls):
        from server import geo, web
        cls.geo, cls.fetch = geo, geo.fetch_json

        def offline(url, timeout=None):  # no network: the place name comes from our own data (content)
            raise OSError('test: no network')
        geo.fetch_json = offline
        cls.srv = Running(engines=[n for n in web.ENGINES if n != 'usgs'], web_dir=WEB).__enter__()
        cls.base = f'http://127.0.0.1:{cls.srv.port}/'
        cls.admin = Client(cls.srv.port)
        r = cls.admin.post('/api/admin/login', {'username': 'admin', 'password': 'panah1234'})
        assert r.status == 200, r.text
        SHOTS.mkdir(parents=True, exist_ok=True)
        cls.profile = STAND / f'chrome-sos-{os.getpid()}'
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
        cls.cdp = CDP(cls.ws)
        for m in ('Runtime.enable', 'Log.enable', 'Page.enable', 'Network.enable'):
            cls.cdp.call(m)
        cls.cdp.call('Browser.grantPermissions', {'origin': cls.base.rstrip('/'), 'permissions': ['geolocation']})
        cls.cdp.call('Emulation.setGeolocationOverride', {'latitude': KABUL[0], 'longitude': KABUL[1], 'accuracy': 12})

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
        cls.srv.__exit__(None, None, None)
        cls.geo.fetch_json = cls.fetch
        for _ in range(20):  # Windows holds the profile files a bit longer
            shutil.rmtree(cls.profile, ignore_errors=True)
            if not cls.profile.exists():
                break
            time.sleep(0.3)

    # ---------------------------------------------------------------- helpers
    def size(self, w, h=956):
        self.cdp.call('Emulation.setDeviceMetricsOverride', {'width': w, 'height': h, 'deviceScaleFactor': 2, 'mobile': True})

    def wait(self, expr, timeout=15, msg=None):
        self.assertTrue(self.cdp.wait_for(expr, timeout), msg or expr)

    def shot(self, name, full=True):
        """Screenshot at 440 px (×2, like the Figma frames). full: the window is as tall as the content, so the chat field
        pinned to the bottom stays at the bottom, as on a phone, not in the middle of a long screenshot."""
        c = self.cdp
        if full:
            c.eval('window.scrollTo(0, 0); true')
            time.sleep(0.3)
            self.size(440, max(956, int(c.call('Page.getLayoutMetrics')['cssContentSize']['height'])))
        time.sleep(0.5)
        h = c.eval('innerHeight')
        r = c.call('Page.captureScreenshot', {'format': 'png', 'clip': {'x': 0, 'y': 0, 'width': 440, 'height': h, 'scale': 1}})
        (SHOTS / name).write_bytes(base64.b64decode(r['data']))
        if full:
            self.size(440)

    def no_overflow(self, label):
        for w in (360, 440):
            self.size(w)
            time.sleep(0.4)
            r = self.cdp.eval(OVERFLOW_JS)
            self.assertLessEqual(r['sw'], w, f'{label}: horizontal scroll at {w}: {r}')
        self.size(440)

    def send(self, text):
        self.cdp.eval('''(() => { const i = document.querySelector(".chatbar__input"); i.focus(); i.value = %s;
          i.dispatchEvent(new Event("input", {bubbles: true}));
          document.querySelector(".chatbar").requestSubmit(); return true; })()''' % json.dumps(text))

    def text(self, sel='.screen'):
        return self.cdp.eval(f'(document.querySelector({json.dumps(sel)}) || {{}}).innerText || ""')

    def offline(self, on):
        self.cdp.call('Network.emulateNetworkConditions', {'offline': on, 'latency': 0, 'downloadThroughput': -1,
                                                           'uploadThroughput': -1})

    def fresh_guest(self):
        """New device: no cookie (a new panah_device) and no localStorage."""
        c = self.cdp
        c.call('Network.clearBrowserCookies')
        c.eval('localStorage.clear(); true')
        c.call('Page.navigate', {'url': self.base + '#/home'})
        self.wait('document.readyState === "complete" && !!document.querySelector(".screen")')

    def user_texts(self, cid):
        return [m['text'] for m in self.case(cid)['messages'] if m['sender'] == 'user']

    def case(self, cid):
        r = self.admin.get(f'/api/admin/sos/{cid}')
        self.assertEqual(r.status, 200, r.text)
        return r.json()

    # ---------------------------------------------------------------- resident path
    def test_1_resident_path(self):
        c = self.cdp
        self.size(440)
        c.call('Page.navigate', {'url': self.base + '#/home'})
        self.wait('document.readyState === "complete" && !!document.querySelector(".screen")')
        c.eval('App.go("#/sos"); true')
        self.wait('!!document.querySelector(".chatbar")', msg='no chat')
        self.assertEqual(self.admin.get('/api/admin/sos?status=all').json()['cases'], [])
        self.assertEqual(c.eval('document.documentElement.dir'), 'rtl')

        # 11/13 → first message → 12
        self.send('کمک کنید!')
        self.wait('location.hash === "#/sos/sent" && !!document.querySelector(".sos-done")', 20, 'no screen 12')
        t12 = self.text()
        for s in ('پیام ارسال شد!', 'موقعیت زنده شما در حال اشتراک با تیم نجات است...', 'به‌روزرسانی: هر ۳۰ ثانیه',
                  'پیام خودکار از تیم نجات:', 'پیغام شما دریافت شد.', 'موقعیت زنده در حال اشتراک',
                  'به‌روزرسانی خودکار: فعال'):
            self.assertIn(s, t12)
        cases = self.admin.get('/api/admin/sos?status=all').json()['cases']
        self.assertEqual(len(cases), 1)
        cid = cases[0]['case']['id']
        first = self.case(cid)
        self.assertEqual(first['case']['kind'], 'custom')
        self.assertAlmostEqual(first['case']['lat'], KABUL[0], places=4)
        self.assertAlmostEqual(first['case']['lon'], KABUL[1], places=4)
        self.assertEqual(first['messages'][0]['text'], 'کمک کنید!')
        self.shot('12-sos-message-sent.png')
        self.no_overflow('12')

        # 12 goes to the chat by itself
        self.wait('location.hash === "#/sos" && !!document.querySelector(".msg--out")', 15, '12 did not go to the chat')
        # chat → 12 → chat leaves one history entry: one "back" takes us where we came from
        c.eval('document.querySelector("[data-back]").click(); true')
        self.wait('location.hash === "#/home"', 5, 'back from the chat did not work the first time: ' + str(c.eval('location.hash')))
        c.eval('App.go("#/sos"); true')
        self.wait('!!document.querySelector(".msg--out")', 10)
        chat = self.text()
        self.assertIn('کاربر:', chat)
        self.assertIn('کمک کنید!', chat)
        self.assertIn('موقعیت زنده ضمیمه شد', chat)
        self.assertIn('۳۴٫۵۵۵۰°N', chat)
        self.size(440, 620)  # 11: the same chat, the phone keyboard takes the bottom
        c.eval('document.querySelector(".chatbar__input").focus(); true')
        self.shot('11-sos-chat-keyboard.png', full=False)
        self.size(440)

        # second message → auto-reply 13 with the ✓ list and number 119 from the server text
        self.send('زیر آوار مانده‌ایم.')
        self.wait('document.querySelectorAll(".sos-direct").length === 1', 10, 'no auto-reply 13')
        chat = self.text()
        sign = all_dict('fa')['sos.teamSign']  # «تیم نجات پناه» — taken from the dictionary: the system name may change, the test should not depend on it
        for s in ('تیم نجات (خودکار):', 'در همین حال:', 'باتری خود را حفظ کنید', 'شماره تماس مستقیم:', sign):
            self.assertIn(s, chat)
        self.assertEqual(c.eval('document.querySelector(".sos-direct__tel").getAttribute("href")'), 'tel:119')
        self.shot('13-sos-rescue-chat.png')
        self.no_overflow('13')

        # paperclip: image → POST /api/upload → message with the /uploads/… path → image in the chat
        self.assertFalse(c.eval('document.querySelector(".chatbar__attach").hidden'), 'no paperclip')
        c.eval('''(() => {
          const png = atob("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==");
          const dt = new DataTransfer();
          dt.items.add(new File([Uint8Array.from(png, (ch) => ch.charCodeAt(0))], "photo.png", {type: "image/png"}));
          const inp = document.querySelector("[data-file]");
          inp.files = dt.files; inp.dispatchEvent(new Event("change")); return true; })()''')
        self.wait('[...document.querySelectorAll(".sos-img img")].some((i) => i.complete && i.naturalWidth > 0)', 10, 'image did not arrive')
        self.assertRegex(self.case(cid)['messages'][-1]['text'], r'^/uploads/[\w.-]+\.png$')

        # the rescuer's reply arrives in the open chat by itself
        r = self.admin.post(f'/api/admin/sos/{cid}/messages', {'text': 'تیم ما در راه است.'})
        self.assertEqual(r.status, 200, r.text)
        self.wait('document.querySelector(".chat").innerText.includes("تیم ما در راه است.")', 10, "rescuer's reply did not arrive")
        self.assertIn('تیم نجات:', self.text())
        self.shot('13b-sos-rescuer-reply.png')

        # App.live 'reconnect' (stream reopened after a drop) → the chat reloads: a message sent past the stream shows up
        from server import sos as sos_engine, store
        from server.common import utcnow
        db = store.connect(self.srv.app.db_path)
        try:
            sos_engine._add_message(db, cid, 'rescuer', 'پیام بدون جریان زنده', None, utcnow())
            db.commit()
        finally:
            db.close()
        time.sleep(1)
        self.assertNotIn('پیام بدون جریان زنده', self.text('.chat'))
        c.eval('App.live.restart(); true')  # drop and reopen the stream → the core sends 'reconnect'
        self.wait('document.querySelector(".chat").innerText.includes("پیام بدون جریان زنده")', 10, 'reconnect did not reload the chat')

        # SOS from the chat header: cancel within 3 s, nothing is sent
        before = len(self.case(cid)['messages'])
        c.eval('document.querySelector(".sos-chat__sos").click(); true')
        self.wait('!!document.querySelector(".sos-count")', 5, 'no countdown')
        self.assertIn('۳', self.text('.sos-count__ring'))
        self.shot('10a-sos-countdown.png', full=False)
        c.eval('document.querySelector("[data-cancel]").click(); true')
        self.wait('location.hash === "#/sos"', 5)
        time.sleep(3.5)
        self.assertEqual(len(self.case(cid)['messages']), before, 'cancelled SOS was sent')

        # SOS without cancel → 10 (same case, an SOS message added)
        c.eval('location.hash = "#/sos/now"; true')
        self.wait('!!document.querySelector(".sos-card")', 20, 'no card 10')
        t10 = self.text()
        for s in ('ارسال‌شده', 'کمک فوری!', 'موقعیت زنده من:', 'شمالی', 'شرقی', '۳۴٫۵۵۵۰°', '۶۹٫۲۰۷۰°',
                  'به‌روزرسانی: همین حالا', 'زمان ارسال:', 'وضعیت: ارسال موفق', 'لطفاً سریعاً کمک بفرستید!'):
            self.assertIn(s, t10)
        now = self.case(cid)
        self.assertEqual(now['messages'][-1]['text_key'], 'sos_default')
        self.assertEqual(len(now['messages']), before + 1)
        self.shot('10-sos-sent.png')
        self.no_overflow('10')
        c.eval('App.setLang("en").then(() => true)', wait=True)
        self.wait('document.documentElement.dir === "ltr"', 5)
        self.no_overflow('10 en')
        c.eval('App.setLang("fa").then(() => true)', wait=True)

        # location every 30 s, also when the resident is on another screen
        n0 = len(self.case(cid)['track'])
        c.eval('location.hash = "#/home"; true')
        deadline = time.time() + 45
        while len(self.case(cid)['track']) <= n0 and time.time() < deadline:
            time.sleep(1)
        self.assertGreater(len(self.case(cid)['track']), n0, 'location not sent within 45 s')

        # closing by the rescuer: shown in the chat, timer stopped, a new case can be started
        c.eval('location.hash = "#/sos"; true')
        self.wait('!!document.querySelector(".chatbar") && !document.querySelector(".chatbar").hidden', 10)
        self.assertEqual(self.admin.post(f'/api/admin/sos/{cid}/close').status, 200)
        self.wait('!document.querySelector("[data-closed]").hidden', 10, 'closing not shown')
        self.assertFalse(c.eval('!!App.sos.state.timer'), 'location timer not stopped')
        self.assertTrue(c.eval('document.querySelector(".chatbar").hidden'))
        self.shot('13c-sos-closed.png')
        c.eval('App.setLang("en").then(() => true)', wait=True)
        self.wait('!!document.querySelector("[data-new]") && !document.querySelector("[data-closed]").hidden', 10)
        self.no_overflow('13 en')
        self.shot('13d-sos-closed-en.png')
        c.eval('App.setLang("fa").then(() => true)', wait=True)
        # after a language change the screen is redrawn (and after a reload): the closed case is visible again
        self.wait('!!document.querySelector("[data-new]") && !document.querySelector("[data-closed]").hidden', 10)
        self.assertIn('این درخواست کمک بسته شد', self.text())
        c.eval('document.querySelector("[data-new]").click(); true')
        self.wait('!document.querySelector(".chatbar").hidden && !!document.querySelector(".sos-hint")', 5)

        # 401 from /api/me for a guest is the core's "who is logged in" request, not a screen error
        self.assertEqual(c.errors(ignore=('/api/me',)), [], 'console errors')


    def test_2_offline_queue(self):
        """Offline: SOS and messages go to the queue (banner and "تماس با ۱۱۹"); online they go by themselves, the normal way;
        a response cut off after the server saved it: the retry does not create a second message."""
        c = self.cdp
        self.size(440)
        self.fresh_guest()
        before = len(self.admin.get('/api/admin/sos?status=all').json()['cases'])

        # SOS offline → queue, banner, call 119; nothing reached the server
        self.offline(True)
        self.wait('navigator.onLine === false', 5)
        c.eval('location.hash = "#/sos/now"; true')
        self.wait('!!document.querySelector("[data-offline]")', 20, 'no "no network" banner')
        self.assertIn('اینترنت وصل نیست — پیام شما در صف ارسال است', self.text())
        self.assertEqual(c.eval('document.querySelector(".sos-offline__call").getAttribute("href")'), 'tel:119')
        self.assertIn('تماس با ۱۱۹', re.sub('[\u2066-\u2069]', '', self.text('.sos-offline__call')))
        self.assertEqual(c.eval('App.sos.queue.items().length'), 1)
        self.assertEqual(c.eval('JSON.parse(localStorage.getItem("panah.sosQueue")).length'), 1, 'queue not in localStorage')
        self.shot('10b-sos-offline.png', full=False)
        self.no_overflow('10 offline')
        self.assertEqual(len(self.admin.get('/api/admin/sos?status=all').json()['cases']), before)

        # network back → sent by itself → the usual card 10; the server has one new case with one SOS
        self.offline(False)
        self.wait('!!document.querySelector(".sos-card")', 25, 'queued SOS not sent')
        self.assertEqual(c.eval('App.sos.queue.items().length'), 0)
        cases = self.admin.get('/api/admin/sos?status=all').json()['cases']
        self.assertEqual(len(cases), before + 1)
        cid = cases[0]['case']['id']
        self.assertEqual([m['text_key'] for m in self.case(cid)['messages'] if m['sender'] == 'user'], ['sos_default'])

        # chat offline → "در صف ارسال" → network → delivered once
        c.eval('location.hash = "#/sos"; true')
        self.wait('!!document.querySelector(".chatbar") && !!document.querySelector(".msg--out")', 10)
        self.offline(True)
        self.wait('navigator.onLine === false', 5)
        self.send('بدون انترنت نوشتم')
        self.wait('!!document.querySelector(".sos-pending") && !!document.querySelector("[data-offline]")', 10, 'no "در صف ارسال"')
        self.shot('13e-sos-offline-queued.png')
        self.assertNotIn('بدون انترنت نوشتم', self.user_texts(cid))
        self.offline(False)
        self.wait('!document.querySelector(".sos-pending") && document.querySelector(".chat").innerText.includes("بدون انترنت نوشتم")', 25, 'queued message not sent')
        self.assertEqual(self.user_texts(cid).count('بدون انترنت نوشتم'), 1)

        # the response was cut off but the server had saved it → queued with a mark; the retry finds it, no second one
        c.call('Fetch.enable', {'patterns': [{'urlPattern': '*/api/sos/*/messages*', 'requestStage': 'Response'}]})
        try:
            self.send('پاسخ گم شد')
            failed, end = False, time.time() + 10
            while not failed and time.time() < end:
                c.eval('1')
                for e in [x for x in c.events if x.get('method') == 'Fetch.requestPaused']:
                    c.events.remove(e)
                    rid = e['params']['requestId']
                    if e['params']['request']['method'] == 'POST':
                        c.call('Fetch.failRequest', {'requestId': rid, 'errorReason': 'ConnectionReset'})
                        failed = True
                    else:
                        c.call('Fetch.continueRequest', {'requestId': rid})
                time.sleep(0.1)
            self.assertTrue(failed, 'POST not intercepted')
        finally:
            c.call('Fetch.disable')
        self.wait('!!document.querySelector(".sos-pending")', 10, 'cut-off message not in the queue')
        self.assertTrue(c.eval('App.sos.queue.items()[0].maybe'), 'no "may have arrived" mark')
        self.assertEqual(self.user_texts(cid).count('پاسخ گم شد'), 1)  # the server saved it before the cut
        c.eval('App.sos.queue.flush().then(() => true)', wait=True)
        self.wait('!document.querySelector(".sos-pending")', 10)
        self.assertEqual(self.user_texts(cid).count('پاسخ گم شد'), 1, 'duplicate on the server')
        self.assertEqual(c.eval('document.querySelector(".chat").innerText.split("پاسخ گم شد").length - 1'), 1)

        self.assertEqual(c.errors(ignore=('/api/me', 'ERR_INTERNET_DISCONNECTED', 'ERR_CONNECTION_RESET')), [],
                         'console errors')


if __name__ == '__main__':
    unittest.main()
