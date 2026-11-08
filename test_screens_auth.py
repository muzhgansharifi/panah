"""Screens of the auth group (02 login, 03 quick sign-up, 04 SMS code, 01 profile) on a live server.

Run from the project root:  python -m unittest app.tests.test_screens_auth   (or discover app/tests)
The server is real (all engines, no USGS) on a free port, the database in a temporary folder. The browser is headless
Google Chrome over CDP (its own temporary profile in stand/, deleted afterwards; opens no windows). No Chrome: the browser
part is skipped; dictionaries and files are always checked. Screenshots at 440 px (×2, like the Figma frames):
stand/screens/auth/NN-<name>.png.

Paths (in order, in one browser):
  1. start without login → #/login (02) → wrong number → number in Dari digits → #/verify (04): the number and the test
     code on screen, timer; wrong code → "4 attempts left"; backspace; code pasted from the clipboard → new resident →
     #/profile?new=1 (01): "at least 1 contact", name, blood group, health, contact → "ساختن حساب" → #/home;
     the profile is saved on the server.
  2. #/signup (03) → Dari + Pashto pairs → "رد کردن" → #/home as a guest.
  3. 03 → "شروع" (fake GPS location) → 04 → code typed → 01 → skip → #/home; the resident's location is on the server.
  4. logged in: start → #/home; #/profile edit ("حساب من"): language switch (پښتو, English, دری) keeps the typed text →
     "خروج" → #/login, no session.
  5. English and پښتو on 02 and 03: direction, no horizontal scroll.
Everywhere: no console errors (except the expected 4xx API responses), no horizontal scroll at 360 and 440 px.
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import support  # noqa: E402,F401  (puts app/ on sys.path)
from test_ui_core import CDP, OVERFLOW_JS, TILE_HOSTS, WS, find_chrome  # noqa: E402
from server import web  # noqa: E402

ROOT = TESTS.parents[1]
WEB = ROOT / 'app' / 'web'
STAND = ROOT / 'stand'
SHOTS = STAND / 'screens' / 'auth'
LANGS = ('fa', 'ps', 'en')
FA_DIGITS = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
KABUL_UNI = (34.5175, 69.1360)  # fake GPS for path 3


class TestFilesAndDicts(unittest.TestCase):
    """Without a browser: the group files exist, group dictionaries have the same keys and placeholders, screen strings exist."""

    def test_files(self):
        for rel in ('js/screens/auth.js', 'css/auth.css', 'i18n/fa/auth.json', 'i18n/ps/auth.json', 'i18n/en/auth.json'):
            self.assertTrue((WEB / rel).is_file(), rel)
        index = (WEB / 'index.html').read_text(encoding='utf-8')
        self.assertIn('css/auth.css', index)
        self.assertIn('js/screens/auth.js', index)

    def test_dicts(self):
        group = {lang: json.loads((WEB / 'i18n' / lang / 'auth.json').read_text(encoding='utf-8')) for lang in LANGS}
        common = {lang: json.loads((WEB / 'i18n' / f'{lang}.json').read_text(encoding='utf-8')) for lang in LANGS}
        ph = lambda s: set(re.findall(r'\{(\w+)\}', s))  # noqa: E731
        for lang in ('ps', 'en'):
            self.assertEqual(set(group[lang]), set(group['fa']), f'{lang}/auth.json: keys differ from fa/auth.json')
        for key, value in group['fa'].items():
            self.assertTrue(value.strip(), f'fa:{key} is empty')
            for lang in ('ps', 'en'):
                self.assertTrue(group[lang][key].strip(), f'{lang}:{key} is empty')
                self.assertEqual(ph(group[lang][key]), ph(value), f'{lang}:{key}: placeholders differ from Dari')
        # every App.t('…') string from auth.js is in the dictionaries (shared + group) of all three languages
        code = (WEB / 'js' / 'screens' / 'auth.js').read_text(encoding='utf-8')
        keys = set(re.findall(r"\bt\('([\w.]+)'", code)) | {k for _, k in re.findall(r"\['(\w+)', '(health\.\w+)'\]", code)}
        self.assertGreater(len(keys), 40)
        for lang in LANGS:
            have = set(common[lang]) | set(group[lang])
            self.assertEqual(sorted(keys - have), [], f'{lang}: strings missing')
        # a number has 10 digits, example "۰۷۷۷ ۱۲۳ ۴۵۶" (the 11-digit Figma example is wrong)
        self.assertEqual(group['fa']['auth.phonePlaceholder'], '۰۷۷۷ ۱۲۳ ۴۵۶')
        self.assertEqual(group['fa']['auth.verify.testCode'], 'حالت آزمایشی — کد: {code}')

    def test_css_logical_only(self):
        text = re.sub(r'/\*.*?\*/', '', (WEB / 'css' / 'auth.css').read_text(encoding='utf-8'), flags=re.S)
        bad = re.compile(r'(?<![\w-])(margin|padding|border)-(left|right)\b|(?<![\w-])(left|right)\s*:|'
                         r'text-align\s*:\s*(left|right)|float\s*:\s*(left|right)|border-(top|bottom)-(left|right)-radius')
        self.assertEqual([m.group(0) for m in bad.finditer(text)], [])


def api_errors(errors):
    """Chrome logs the expected 4xx API responses (wrong code → 400) to the console; they are not counted."""
    return [e for e in errors if not (e.startswith('log: Failed to load resource: the server responded with a status of 4')
                                      and '/api/' in e)]


@unittest.skipIf(os.environ.get('PANAH_SKIP_BROWSER'), 'PANAH_SKIP_BROWSER')
@unittest.skipUnless(find_chrome(), 'no Google Chrome')
class TestAuthScreens(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.app = web.App(Path(cls.tmp.name) / 'data' / 'panah.db', config={'usgs': False})
        cls.app.setup()
        cls.server = web.bind(cls.app, '127.0.0.1', 0)
        cls.app.start()
        cls.thread = web.serve_in_thread(cls.server)
        cls.origin = 'http://127.0.0.1:%d' % cls.server.server_address[1]
        STAND.mkdir(exist_ok=True)
        SHOTS.mkdir(parents=True, exist_ok=True)
        cls.profile = STAND / f'chrome-auth-{os.getpid()}'
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
        for m in ('Runtime.enable', 'Log.enable', 'Page.enable'):
            cls.cdp.call(m)
        cls.cdp.call('Emulation.setFocusEmulationEnabled', {'enabled': True})  # focus in a code cell, as on 04
        cls.n = 0

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
        for _ in range(20):  # Windows holds the profile files a bit longer
            shutil.rmtree(cls.profile, ignore_errors=True)
            if not cls.profile.exists():
                break
            time.sleep(0.3)
        cls.app.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(5)
        cls.tmp.cleanup()

    # ------------------------------------------------------------ helpers
    def open(self, hash_='', width=440):
        """Full app load (a fresh App.start run) at the address hash_."""
        c = self.cdp
        type(self).n += 1
        c.events.clear()
        c.call('Emulation.setDeviceMetricsOverride', {'width': width, 'height': 900, 'deviceScaleFactor': 2, 'mobile': True})
        c.call('Page.navigate', {'url': f'{self.origin}/?t={self.n}{hash_}'})
        self.assertTrue(c.wait_for('document.readyState === "complete" && !!window.App && !!document.querySelector("#app .screen")'),
                        'app did not load')

    def wait(self, expr, msg=None, timeout=25):
        ok = self.cdp.wait_for(expr, timeout=timeout)
        if not ok:
            where = self.cdp.eval('location.hash + " | " + ((document.querySelector("#app .screen") || {}).dataset || {}).screen')
            self.fail(f'{msg or expr} (now: {where}; errors: {self.cdp.errors()})')

    def js(self, expr, wait=False):
        return self.cdp.eval(expr, wait=wait)

    def click(self, sel):
        self.assertTrue(self.js(f'(() => {{ const e = document.querySelector({json.dumps(sel)}); if (!e) return false; e.click(); return true; }})()'),
                        f'no element {sel}')

    def set_value(self, sel, value):
        self.assertTrue(self.js(f'''(() => {{ const e = document.querySelector({json.dumps(sel)}); if (!e) return false;
            e.focus(); e.value = {json.dumps(value)}; e.dispatchEvent(new Event('input', {{bubbles: true}})); return true; }})()'''),
                        f'no field {sel}')

    def type_text(self, text):
        for ch in text:
            self.cdp.call('Input.insertText', {'text': ch})

    def key(self, key, code, vk):
        for kind in ('rawKeyDown', 'keyUp'):
            self.cdp.call('Input.dispatchKeyEvent', {'type': kind, 'key': key, 'code': code, 'windowsVirtualKeyCode': vk})

    def text(self, sel):
        return self.js(f'(document.querySelector({json.dumps(sel)}) || {{}}).textContent || ""')

    def otp(self):
        return self.js('[...document.querySelectorAll(".otp__cell")].map(i => i.value).join("")')

    def check_width(self, label):
        """No horizontal scroll at 440 and at 360 px (leaves the width at 440)."""
        c = self.cdp
        for width in (360, 440):
            c.call('Emulation.setDeviceMetricsOverride', {'width': width, 'height': 900, 'deviceScaleFactor': 2, 'mobile': True})
            time.sleep(0.25)
            r = c.eval(OVERFLOW_JS)
            self.assertLessEqual(r['sw'], width, f'{label}: horizontal scroll at {width}: {r}')
            self.assertEqual(r['bad'], [], f'{label}: elements past the edge at {width}: {r}')

    def shot(self, name, focus=None):
        """Full-page screenshot at 440 px, ×2, like the Figma frames (no toasts; focus as in the reference)."""
        c = self.cdp
        c.call('Emulation.setDeviceMetricsOverride', {'width': 440, 'height': 956, 'deviceScaleFactor': 2, 'mobile': True})
        c.eval('document.fonts.ready.then(() => true)', wait=True)
        c.eval('document.querySelectorAll(".toast").forEach((t) => t.remove()), true')
        c.eval(f'document.activeElement && document.activeElement.blur && document.activeElement.blur(), '
               f'{json.dumps(focus)} && document.querySelector({json.dumps(focus)}).focus(), true')
        time.sleep(0.4)
        h = max(956, c.call('Page.getLayoutMetrics')['cssContentSize']['height'])
        shot = c.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': True,
                                                 'clip': {'x': 0, 'y': 0, 'width': 440, 'height': h, 'scale': 1}})
        (SHOTS / f'{name}.png').write_bytes(base64.b64decode(shot['data']))

    def no_errors(self, label):
        self.assertEqual(api_errors(self.cdp.errors(ignore=TILE_HOSTS)), [], f'{label}: console errors')

    def me(self):
        return self.js('fetch("/api/me").then(r => r.json().then(j => ({status: r.status, body: j})))', wait=True)

    def answer(self, path, data, action, timeout=15):
        """Fake the server response to the request path (CDP Fetch) during action() → the request URL. This way the check
        does not go to Nominatim and does not depend on the network."""
        c = self.cdp
        c.call('Fetch.enable', {'patterns': [{'urlPattern': f'*{path}*', 'requestStage': 'Request'}]})
        try:
            action()
            end, req = time.time() + timeout, None
            while time.time() < end and req is None:
                c.eval('1')  # pump CDP events
                req = next((e for e in c.events if e['method'] == 'Fetch.requestPaused'), None)
                if req is None:
                    time.sleep(0.05)
            self.assertIsNotNone(req, f'no request {path}')
            c.events.remove(req)
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            c.call('Fetch.fulfillRequest', {'requestId': req['params']['requestId'], 'responseCode': 200,
                                            'responseHeaders': [{'name': 'Content-Type', 'value': 'application/json; charset=utf-8'}],
                                            'body': base64.b64encode(body).decode('ascii')})
            return req['params']['request']['url']
        finally:
            c.call('Fetch.disable')

    # ------------------------------------------------------------ paths
    def test_1_login_verify_profile_home(self):
        self.open('')  # start without login → #/login
        self.wait('location.hash === "#/login" && !!document.querySelector("[data-screen=login] #au-login-phone")', 'guest start is not on #/login')
        self.assertEqual(self.js('document.documentElement.dir'), 'rtl')
        self.assertEqual(self.text('.auth-hero__title'), 'سیستم هشدار اضطراری')
        self.assertEqual(self.js('document.querySelector("#au-login-phone").placeholder'), '۰۷۷۷ ۱۲۳ ۴۵۶')
        self.assertFalse(self.js('!!document.querySelector("[data-screen=login] input[type=password]")'), 'there must be no password field')
        self.assertEqual(self.js('document.querySelector("#au-login-new").getAttribute("data-go")'), '#/signup')
        self.assertEqual(self.js('document.querySelector("#au-login-sos").getAttribute("data-go")'), '#/sos/now')
        self.assertEqual(self.js('[...document.querySelectorAll(".langs__item")].map(b => b.dataset.lang).join(",")'), 'en,fa,ps')
        # languages: a separate strip at the bottom of the screen, not in the card (as in Figma frame 02)
        self.assertFalse(self.js('!!document.querySelector(".auth-card .langs")'), 'language switcher inside the card')
        self.assertGreater(self.js('document.querySelector(".auth-langs").getBoundingClientRect().bottom'), 860, 'languages not at the bottom of the screen')
        self.check_width('02')
        self.shot('02-login')

        self.set_value('#au-login-phone', '12345')  # wrong number: error under the field, nothing sent to the server
        self.click('#au-login-submit')
        self.wait('!document.querySelector("[data-screen=login] .field__error").hidden', 'no wrong-number error')
        self.set_value('#au-login-phone', '۰۷۷۷ ۱۲۳ ۴۵۶')  # Dari digits
        self.click('#au-login-submit')
        self.wait('location.hash === "#/verify" && !!document.querySelector(".auth-test__code")', 'no move to 04 with the test code')
        code = self.js('App.fmt.latin(document.querySelector(".auth-test__code").textContent)')
        self.assertRegex(code, r'^\d{6}$')
        self.assertIn('حالت آزمایشی — کد:', self.text('.auth-test'))
        self.assertIn('۰۷۷۷ ۱۲۳ ۴۵۶', self.text('.auth-sent__phone'))
        self.assertRegex(self.text('.auth-timer__text'), r'^ارسال مجدد کد تا [۴۳][۰-۹] ثانیه$')
        self.assertEqual(self.js('document.activeElement && document.activeElement.id'), 'au-otp-1')
        self.check_width('04')
        self.shot('04-verify-phone', focus='#au-otp-1')

        # wrong code typed in: auto-advance through the cells
        wrong = '000000' if code != '000000' else '111111'
        self.js('document.querySelector("#au-otp-1").focus(), true')
        self.type_text(wrong)
        self.assertEqual(self.otp(), wrong.translate(FA_DIGITS))
        self.click('#au-confirm')
        self.wait('!document.querySelector(".auth-code__error").hidden', 'no wrong-code error')
        self.assertEqual(self.text('.auth-code__error'), 'کد نادرست است. ۴ بار دیگر می‌توانید کوشش کنید.')
        self.assertEqual(self.otp(), '')
        # erasing backwards: two digits, Backspace on the empty third → the second is erased, focus on it
        self.type_text('12')
        self.key('Backspace', 'Backspace', 8)
        self.assertEqual(self.otp(), '۱')
        self.assertEqual(self.js('document.activeElement.id'), 'au-otp-2')
        # paste from the clipboard: Dari digits with a space
        pasted = self.js(f'''(() => {{ const dt = new DataTransfer(); dt.setData('text/plain', {json.dumps(code[:3].translate(FA_DIGITS) + ' ' + code[3:].translate(FA_DIGITS))});
            const c = document.querySelector('#au-otp-1'); c.focus();
            c.dispatchEvent(new ClipboardEvent('paste', {{clipboardData: dt, bubbles: true, cancelable: true}}));
            return [...document.querySelectorAll('.otp__cell')].map(i => App.fmt.latin(i.value)).join(''); }})()''')
        self.assertEqual(pasted, code)
        self.click('#au-confirm')
        self.wait('location.hash === "#/profile?new=1" && !!document.querySelector("[data-screen=profile] #au-save")', 'new resident is not on #/profile')

        # 01: new resident
        self.assertEqual(self.text('.hdr__title'), 'ساختن حساب جدید')
        self.assertEqual(self.js('document.querySelector("#au-phone").value'), '۰۷۷۷ ۱۲۳ ۴۵۶')
        self.assertTrue(self.js('document.querySelector("#au-phone").readOnly'))
        self.assertTrue(self.js('document.querySelector("input[name=au-health][value=none]").checked'), '"بدون مشکل خاص" by default')
        self.click('#au-save')  # no contact: the "at least 1" error
        self.wait('!document.querySelector("#au-c-error").hidden', 'no "at least 1 contact" check')
        self.assertEqual(self.text('#au-c-error'), 'حداقل یک مخاطب اضطراری اضافه کنید.')
        self.set_value('#au-name', 'احمد')
        self.click('input[name=au-blood][value="A+"]')
        self.click('input[name=au-health][value=diabetes]')
        self.assertFalse(self.js('document.querySelector("input[name=au-health][value=none]").checked'), '"بدون مشکل خاص" was not unchecked')
        self.click('input[name=au-health][value=heart]')
        self.click('#au-c-add')
        self.set_value('#au-c-name', 'همسر')
        self.set_value('#au-c-phone', '0799 123 456')
        self.click('#au-c-ok')
        self.click('#au-c-add')
        self.set_value('#au-c-name', 'پدر')
        self.set_value('#au-c-phone', '0799123456')  # the same number: refused
        self.click('#au-c-ok')
        self.assertEqual(self.text('#au-cform .field__error'), 'این شماره قبلاً اضافه شده است.')
        self.set_value('#au-c-phone', '+93 700 222 333')
        self.click('#au-c-ok')
        self.assertEqual(self.js('document.querySelectorAll(".auth-contact").length'), 2)
        self.click('.auth-contact:last-child .auth-contact__del')
        self.click('#au-c-add')
        self.set_value('#au-c-name', 'پدر')
        self.set_value('#au-c-phone', '0700 222 333')
        self.click('#au-c-ok')
        self.assertEqual(self.js('document.querySelectorAll(".auth-contact").length'), 2)
        self.assertIn('همسر:', self.text('.auth-contact:first-child'))
        self.check_width('01')
        self.shot('01-create-account')
        self.no_errors('02 → 04 → 01')

        self.click('#au-save')
        self.wait('location.hash === "#/home"', 'not on #/home after "ساختن حساب"')
        r = self.me()
        self.assertEqual(r['status'], 200)
        u = r['body']['user']
        self.assertEqual(u['phone'], '+93777123456')
        self.assertEqual(u['name'], 'احمد')
        self.assertEqual(u['blood_group'], 'A+')
        self.assertEqual(sorted(u['health']), ['diabetes', 'heart'])
        self.assertEqual(u['contacts'], [{'name': 'همسر', 'phone': '+93799123456'}, {'name': 'پدر', 'phone': '+93700222333'}])

    def test_2_signup_skip_guest(self):
        self.js('fetch("/api/auth/logout", {method: "POST"}).then(r => r.status)', wait=True)
        self.js('sessionStorage.clear(), true')
        self.open('#/signup')
        self.wait('!!document.querySelector("[data-screen=signup] #au-quick-start")', 'no screen 03')
        self.assertEqual(self.js('[...document.querySelectorAll(".auth-hero__pair .pair > span")].map(s => s.textContent).join(" | ")'),
                         'ثبت نام سریع | چټک نوم لیکنه')
        self.assertEqual(self.js('document.querySelectorAll(".auth--signup .pair [lang=ps-AF]").length'), 4, 'Dari + Pashto pairs')
        self.assertIn('شروع / پیل', self.text('#au-quick-start'))
        self.assertIn('(Just one tap!)', self.text('#au-quick-start'))
        self.check_width('03')
        self.shot('03-quick-signup')
        self.no_errors('03')
        self.click('#au-quick-skip')
        self.wait('location.hash === "#/home"', '"رد کردن" does not lead to #/home')
        r = self.me()
        self.assertEqual((r['status'], r['body']), (200, {'user': None, 'admin': None}), 'skip: as a guest, no login')

    def test_3_signup_start_location(self):
        c = self.cdp
        c.call('Browser.grantPermissions', {'origin': self.origin, 'permissions': ['geolocation']})
        c.call('Emulation.setGeolocationOverride', {'latitude': KABUL_UNI[0], 'longitude': KABUL_UNI[1], 'accuracy': 25})
        self.js('sessionStorage.clear(), true')
        self.open('#/signup')
        self.wait('!!document.querySelector("[data-screen=signup] #au-quick-phone")')
        self.set_value('#au-quick-phone', '0700 111 222')
        self.click('#au-quick-start')
        self.wait('location.hash === "#/verify" && !!document.querySelector(".auth-test__code")', '03 → 04 failed')
        code = self.js('App.fmt.latin(document.querySelector(".auth-test__code").textContent)')
        self.assertEqual(self.js('JSON.stringify(App.location.saved() && [App.location.saved().lat, App.location.saved().lon])'),
                         json.dumps(list(KABUL_UNI)).replace(' ', ''), 'location not saved')
        self.js('document.querySelector("#au-otp-1").focus(), true')
        self.type_text(code)
        self.click('#au-confirm')
        self.wait('location.hash === "#/profile?new=1" && !!document.querySelector("#au-skip")', '04 → 01 failed')
        end = time.time() + 10
        while True:  # POST /api/me/location goes right after login, without waiting
            u = self.me()['body'].get('user') or {}
            if u.get('last_lat') is not None or time.time() > end:
                break
            time.sleep(0.2)
        self.assertAlmostEqual(u['last_lat'], KABUL_UNI[0], places=4)
        self.assertAlmostEqual(u['last_lon'], KABUL_UNI[1], places=4)
        # "استفاده از موقعیت فعلی" → home = GPS; the place name is the /api/geo/name response (faked: no Nominatim)
        url = self.answer('/api/geo/name', {'name': 'پوهنتون کابل', 'lat': KABUL_UNI[0], 'lon': KABUL_UNI[1]},
                          lambda: self.click('#au-h-gps'))
        self.assertIn('lat=34.5175', url)
        self.wait('document.querySelector("#au-h-chosen").textContent.includes("پوهنتون کابل")', 'GPS did not set home')
        self.assertIn('۳۴٫۵۱۷۵°N', self.text('#au-h-chosen'))
        # "وارد کردن آدرس": network unavailable (offline) → a clear text and "انتخاب روی نقشه"
        self.click('#au-h-addr')
        self.set_value('#au-h-q', 'کارته چهار')
        self.answer('/api/geo/search', {'results': [], 'offline': True}, lambda: self.click('#au-h-go'))
        self.wait('!document.querySelector("#au-h-status").hidden && !!document.querySelector("#au-h-status [data-au-pick]")',
                  'no "offline" text with the pick-on-map option')
        self.assertIn('بدون انترنت', self.text('#au-h-status'))
        time.sleep(1.2)  # at most once per second
        found = {'results': [{'name': 'کارته چهار، کابل', 'lat': 34.5086, 'lon': 69.1497, 'full': 'کارته چهار، ناحیه سوم، کابل'}],
                 'offline': False}
        self.answer('/api/geo/search', found, lambda: self.click('#au-h-go'))
        self.wait('!!document.querySelector(".auth-home__result")', 'no address search results')
        self.click('.auth-home__result')
        self.wait('document.querySelector("#au-h-chosen").textContent.includes("ناحیه سوم")', 'the address did not become home')
        self.no_errors('03 → 04 → 01')
        self.click('#au-skip')
        self.wait('location.hash === "#/home"', 'skipping 01 does not lead to #/home')
        c.call('Emulation.clearGeolocationOverride')

    def test_4_logged_in_start_edit_logout(self):
        self.open('')  # logged in (path 3) → start on #/home
        self.wait('location.hash === "#/home"', 'logged-in start is not on #/home')
        self.open('#/profile')
        self.wait('!!document.querySelector("[data-screen=profile] #au-logout")', 'no profile edit')
        self.assertEqual(self.text('.hdr__title'), 'حساب من')
        self.assertEqual(self.js('document.querySelector("#au-phone").value'), '۰۷۰۰ ۱۱۱ ۲۲۲')
        self.assertFalse(self.js('!!document.querySelector("#au-skip")'))
        self.check_width('01 edit')
        self.shot('01-my-account')
        # language from "حساب من" (above "خروج"): پښتو → English → دری; the typed text survives the redraw
        self.assertTrue(self.js('!!document.querySelector(".auth-langs--profile + #au-logout")'), 'languages not above "خروج"')
        self.set_value('#au-name', 'تست')
        self.click('.auth-langs--profile .langs__item[data-lang=ps]')
        self.wait('document.documentElement.dir === "rtl" && (document.querySelector(".hdr__title") || {}).textContent === "زما حساب"',
                  'profile did not switch to پښتو')
        self.assertEqual(self.js('document.querySelector("#au-save").textContent.trim()'), 'بدلونونه خوندي کړئ')
        self.assertEqual(self.js('document.querySelector("#au-name").value'), 'تست', 'the typed name was lost on the language switch')
        self.click('.auth-langs--profile .langs__item[data-lang=en]')
        self.wait('document.documentElement.dir === "ltr" && (document.querySelector(".hdr__title") || {}).textContent === "My account"',
                  'profile did not switch to English')
        self.check_width('01 edit en')
        self.click('.auth-langs--profile .langs__item[data-lang=fa]')
        self.wait('document.documentElement.dir === "rtl" && (document.querySelector(".hdr__title") || {}).textContent === "حساب من"',
                  'profile did not return to دری')
        self.assertEqual(self.js('document.querySelector("#au-name").value'), 'تست')
        self.no_errors('01 edit')
        self.click('#au-logout')
        self.wait('location.hash === "#/login"', '"خروج" does not lead to #/login')
        r = self.me()
        self.assertEqual((r['status'], r['body']), (200, {'user': None, 'admin': None}), 'session still there after "خروج"')
        self.open('#/profile')  # not logged in → login with a way back (?next=)
        self.wait('location.hash.startsWith("#/login?next=")', '#/profile without login does not lead to login')

    def test_5_languages(self):
        self.open('#/login')
        self.wait('!!document.querySelector(".langs__item[data-lang=en]")')
        self.click('.langs__item[data-lang=en]')
        self.wait('document.documentElement.dir === "ltr" && document.querySelector("#au-login-submit").textContent.trim() === "Sign in"')
        self.check_width('02 en')
        self.open('#/signup')
        self.wait('!!document.querySelector("#au-quick-start")')
        self.assertEqual(self.js('document.querySelectorAll(".auth--signup .pair > span").length'), 4, 'no pairs in English')
        self.check_width('03 en')
        self.open('#/login')
        self.click('.langs__item[data-lang=ps]')
        self.wait('document.documentElement.dir === "rtl" && document.querySelector("#au-login-submit").textContent.trim() === "سیستم ته ننوتل"')
        self.open('#/signup')
        self.wait('!!document.querySelector("#au-quick-start")')
        self.assertIn('پیل / شروع', self.text('#au-quick-start'))
        self.check_width('03 ps')
        self.no_errors('languages')
        self.js('App.setLang("fa").then(() => true)', wait=True)


if __name__ == '__main__':
    unittest.main()
