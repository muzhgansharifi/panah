"""Tests for web and common: engines, routes with {id}, JSON and errors, session, device, upload, static files, startup."""
import base64
import datetime
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from support import APP_DIR, Client, Running, fake_auth, wait_until  # support adds app/ to sys.path

import main as launcher  # noqa: E402  (app/main.py)
from server import store, web  # noqa: E402
from server.common import ApiError, parse_utc, to_utc, utcnow  # noqa: E402

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + bytes(range(40))
JPEG = b'\xff\xd8\xff\xe0' + bytes(range(40))
WEBP = b'RIFF\x24\x00\x00\x00WEBPVP8 ' + bytes(range(40))


def data_url(raw, mime='image/png'):
    return 'data:%s;base64,%s' % (mime, base64.b64encode(raw).decode('ascii'))


def demo_engine():
    """Fake engine: init, start and routes for every case."""
    mod = types.ModuleType('demo')
    mod.started = []

    def init(db):
        db.execute('CREATE TABLE IF NOT EXISTS demo_items (id INTEGER PRIMARY KEY, title TEXT)')
        if db.execute('SELECT count(*) FROM demo_items').fetchone()[0] == 0:
            db.execute("INSERT INTO demo_items (title) VALUES ('اول')")

    def item(req):
        row = req.db.execute('SELECT * FROM demo_items WHERE id = ?', (req.params['id'],)).fetchone()
        if row is None:
            raise ApiError(404, 'not_found', 'پیدا نشد.', id=req.params['id'])
        return 200, row

    def boom(req):
        req.db.execute("INSERT INTO demo_items (title) VALUES ('boom')")
        raise RuntimeError('boom in demo')

    def refuse(req):
        req.db.execute("INSERT INTO demo_items (title) VALUES ('counted')")
        raise ApiError(429, 'too_soon', 'لطفاً کمی صبر کنید.', resend_in=30)

    def titles(req):
        return 200, {'titles': [row['title'] for row in req.db.execute('SELECT title FROM demo_items ORDER BY id')]}

    def need_user(req):
        req.require_user()
        return 200, {'ok': True}

    def need_admin(req):
        req.require_admin()
        return 200, {'ok': True}

    mod.init = init
    mod.start = mod.started.append
    mod.routes = lambda: [
        ('GET', '/api/demo/items/{id}', item),
        ('GET', '/api/demo/num/{n:int}', lambda req: (200, {'n': req.params['n'], 'type': type(req.params['n']).__name__})),
        ('POST', '/api/demo/echo', lambda req: (200, {'body': req.body, 'query': req.query, 'method': req.method})),
        ('PUT', '/api/demo/echo', lambda req: (200, {'method': req.method})),
        ('POST', '/api/demo/boom', boom),
        ('POST', '/api/demo/refuse', refuse),
        ('GET', '/api/demo/titles', titles),
        ('GET', '/api/demo/nothing', lambda req: None),
        ('GET', '/api/demo/plain', lambda req: {'plain': True}),
        ('GET', '/api/demo/header', lambda req: (201, {'ok': True}, {'X-Demo': 'yes'})),
        ('GET', '/api/demo/me', lambda req: (200, {'user': req.user, 'admin': req.admin, 'device': req.device_id})),
        ('GET', '/api/demo/user', need_user),
        ('GET', '/api/demo/admin', need_admin),
    ]
    return mod


class CommonTest(unittest.TestCase):
    def test_api_error(self):
        exc = ApiError(429, 'too_soon', 'لطفاً صبر کنید.', resend_in=30)
        self.assertEqual((exc.status, exc.code, exc.message_fa), (429, 'too_soon', 'لطفاً صبر کنید.'))
        self.assertEqual(exc.to_dict(), {'error': 'too_soon', 'message': 'لطفاً صبر کنید.', 'resend_in': 30})
        self.assertEqual(ApiError(404, 'not_found').to_dict(), {'error': 'not_found', 'message': ''})
        self.assertIsInstance(exc, Exception)

    def test_utc_strings(self):
        now = utcnow()
        self.assertRegex(now, r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertLess(utcnow(-60), now)
        self.assertGreater(utcnow(300), now)
        self.assertEqual(to_utc(0), '1970-01-01T00:00:00Z')
        self.assertEqual(to_utc(datetime.datetime(2026, 11, 6, 5, 30)), '2026-11-06T05:30:00Z')
        kabul = datetime.timezone(datetime.timedelta(hours=4, minutes=30))
        self.assertEqual(to_utc(datetime.datetime(2026, 11, 6, 5, 30, tzinfo=kabul)), '2026-11-06T01:00:00Z')

    def test_parse_utc(self):
        expected = datetime.datetime(2026, 11, 6, 1, 0, tzinfo=datetime.timezone.utc)
        for text in ('2026-11-06T01:00:00Z', '2026-11-06T05:30:00+04:30', '2026-11-06T01:00:00.000Z',
                     '2026-11-06T01:00:00.0Z', '2026-11-06T01:00:00', '2026-11-06T01:00'):
            self.assertEqual(parse_utc(text), expected, text)
        now = utcnow()
        self.assertEqual(to_utc(parse_utc(now)), now, 'round trip gives the same value')
        for bad in ('', 'yesterday', '2026-13-01T00:00:00Z'):
            with self.assertRaises(ValueError):
                parse_utc(bad)


class EngineLoadingTest(unittest.TestCase):
    """Skipping engines not written yet, disabling broken ones, init order."""

    GOOD = (
        'from server import store\n'
        'NAME = %r\n'
        'def init(db):\n'
        "    store.set_setting(db, 'order', store.get_setting(db, 'order', []) + [NAME])\n"
        'def routes():\n'
        "    return [('GET', '/api/' + NAME, lambda req: (200, {'engine': NAME}))]\n"
    )
    BADINIT = (
        'from server import store\n'
        'def init(db):\n'
        "    store.set_setting(db, 'badinit', True)\n"
        "    raise RuntimeError('init boom')\n"
        'def routes():\n'
        "    return [('GET', '/api/badinit', lambda req: (200, {}))]\n"
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.package = 'panah_fake_%s' % secrets.token_hex(4)
        root = Path(self._tmp.name) / self.package
        root.mkdir()
        files = {
            '__init__.py': '',
            'good.py': self.GOOD % 'good',
            'good2.py': self.GOOD % 'good2',
            'broken.py': 'import panah_no_such_module_xyz\n',
            'syntax.py': 'def oops(:\n',
            'badinit.py': self.BADINIT,
        }
        for name, text in files.items():
            (root / name).write_text(text, encoding='utf-8')
        sys.path.insert(0, self._tmp.name)

    def tearDown(self):
        sys.path.remove(self._tmp.name)
        for name in list(sys.modules):
            if name == self.package or name.startswith(self.package + '.'):
                del sys.modules[name]
        self._tmp.cleanup()

    def test_engine_list_order(self):
        self.assertEqual(web.ENGINES, ['sms', 'auth', 'geo', 'alerts', 'usgs', 'sos', 'content'])

    def test_missing_skipped_broken_off_others_work(self):
        names = ['missing', 'broken', 'badinit', 'good', 'syntax', 'good2']
        with self.assertLogs('panah.web', 'ERROR') as logs:
            srv = Running(engines=names, package=self.package).__enter__()
        try:
            app = srv.app
            self.assertEqual(app.skipped, ['missing'])
            self.assertEqual(sorted(app.failed), ['badinit', 'broken', 'syntax'])
            self.assertIn('panah_no_such_module_xyz', app.failed['broken'])
            self.assertEqual(list(app.engines), ['good', 'good2'])
            text = '\n'.join(logs.output)
            for name in ('broken', 'syntax', 'badinit'):
                self.assertIn('engine %s' % name, text)
            self.assertNotIn('engine missing', text, 'an engine not written yet is not an error')
            client = srv.client()
            self.assertEqual(client.get('/api/good').json(), {'engine': 'good'})
            self.assertEqual(client.get('/api/good2').json(), {'engine': 'good2'})
            self.assertEqual(client.get('/api/badinit').status, 404, 'a disabled engine has no routes')
            db = app.connect()
            try:
                self.assertEqual(store.get_setting(db, 'order'), ['good', 'good2'], 'init runs in order')
                self.assertIsNone(store.get_setting(db, 'badinit'), 'a failed init is rolled back')
            finally:
                db.close()
            health = client.get('/api/health').json()
            self.assertEqual(health['engines'], ['good', 'good2'])
            self.assertEqual(health['not_built'], ['missing'])
            self.assertEqual(sorted(health['failed']), ['badinit', 'broken', 'syntax'])
        finally:
            srv.__exit__(None, None, None)

    def test_real_package_skips_engines_not_built(self):
        app = web.App(Path(self._tmp.name) / 'real.db', engines=['no_such_engine_xyz'])
        app.setup()
        self.assertEqual(app.skipped, ['no_such_engine_xyz'])
        self.assertEqual(app.failed, {})


class RoutingTest(unittest.TestCase):
    def setUp(self):
        self.demo = demo_engine()
        self.srv = Running(engines=[('demo', self.demo)]).__enter__()
        self.client = self.srv.client()

    def tearDown(self):
        self.srv.__exit__(None, None, None)

    def test_param_route_json_utf8(self):
        resp = self.client.get('/api/demo/items/1')
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers['Content-Type'], 'application/json; charset=utf-8')
        self.assertEqual(resp.headers['Cache-Control'], 'no-store')
        self.assertIn('اول'.encode('utf-8'), resp.body, 'Dari in the response: UTF-8, not \\uXXXX')
        self.assertEqual(resp.json(), {'id': 1, 'title': 'اول'})
        self.assertEqual(self.client.get('/api/demo/items/1/').status, 200, 'an extra trailing / does no harm')

    def test_api_error_and_params(self):
        resp = self.client.get('/api/demo/items/999')
        self.assertEqual(resp.status, 404)
        self.assertEqual(resp.json(), {'error': 'not_found', 'message': 'پیدا نشد.', 'id': '999'})
        resp = self.client.get('/api/demo/items/%D8%A7%D9%88%D9%84')
        self.assertEqual(resp.json()['id'], 'اول', 'path parameter decoded')

    def test_int_param(self):
        self.assertEqual(self.client.get('/api/demo/num/42').json(), {'n': 42, 'type': 'int'})
        resp = self.client.get('/api/demo/num/4x2')
        self.assertEqual((resp.status, resp.json()['error']), (404, 'not_found'))

    def test_body_and_query(self):
        resp = self.client.post('/api/demo/echo?x=1&y=&q=%D8%B3%DB%8C%D9%84%D8%A7%D8%A8', {'text': 'سلام', 'n': [1]})
        self.assertEqual(resp.json(), {'body': {'text': 'سلام', 'n': [1]}, 'query': {'x': '1', 'y': '', 'q': 'سیلاب'},
                                       'method': 'POST'})
        self.assertEqual(self.client.request('POST', '/api/demo/echo').json()['body'], {}, 'empty body → {}')
        self.assertEqual(self.client.request('PUT', '/api/demo/echo', body={}).json(), {'method': 'PUT'})

    def test_bad_bodies(self):
        for raw in (b'{oops', b'[1, 2]', b'"text"', b'\xff\xfe{}'):
            resp = self.client.request('POST', '/api/demo/echo', raw=raw, headers={'Content-Type': 'application/json'})
            self.assertEqual((resp.status, resp.json()['error']), (400, 'bad_json'), raw)
            self.assertTrue(resp.json()['message'])

    def test_not_found_and_method(self):
        resp = self.client.get('/api/nothing/here')
        self.assertEqual((resp.status, resp.json()['error']), (404, 'not_found'))
        resp = self.client.get('/api/demo/echo')
        self.assertEqual((resp.status, resp.json()['error']), (405, 'method_not_allowed'))
        self.assertEqual(resp.headers['Allow'], 'POST, PUT')
        self.assertEqual(self.client.request('DELETE', '/api/demo/plain').status, 405)

    def test_exception_is_500_logged_and_rolled_back(self):
        with self.assertLogs('panah.web', 'ERROR') as logs:
            resp = self.client.post('/api/demo/boom')
        self.assertEqual(resp.status, 500)
        self.assertEqual(resp.json()['error'], 'server_error')
        self.assertTrue(resp.json()['message'])
        self.assertIn('boom in demo', '\n'.join(logs.output), 'traceback in the log')
        self.assertNotIn('boom', self.client.get('/api/demo/titles').json()['titles'], 'write rolled back')

    def test_api_error_keeps_writes(self):
        resp = self.client.post('/api/demo/refuse')
        self.assertEqual(resp.status, 429)
        self.assertEqual(resp.json(), {'error': 'too_soon', 'message': 'لطفاً کمی صبر کنید.', 'resend_in': 30})
        self.assertIn('counted', self.client.get('/api/demo/titles').json()['titles'],
                      'on ApiError, work done so far is kept (attempt counters)')

    def test_result_forms(self):
        resp = self.client.get('/api/demo/nothing')
        self.assertEqual((resp.status, resp.body), (204, b''))
        self.assertEqual(self.client.get('/api/demo/plain').json(), {'plain': True})
        resp = self.client.get('/api/demo/header')
        self.assertEqual((resp.status, resp.headers['X-Demo']), (201, 'yes'))

    def test_require_user_admin_for_guest(self):
        resp = self.client.get('/api/demo/user')
        self.assertEqual((resp.status, resp.json()['error']), (401, 'login_required'))
        resp = self.client.get('/api/demo/admin')
        self.assertEqual((resp.status, resp.json()['error']), (403, 'admin_required'))

    def test_init_and_start(self):
        self.assertEqual(self.demo.started, [self.srv.app], 'start(app) called with app')
        self.assertEqual(self.client.get('/api/demo/titles').json()['titles'], ['اول'], 'init(db) changes saved')

    def test_route_conflict_first_wins(self):
        dup = types.ModuleType('dup')
        dup.routes = lambda: [('GET', '/api/demo/items/{key}', lambda req: (200, {'dup': True}))]
        with self.assertLogs('panah.web', 'ERROR') as logs:
            srv = Running(engines=[('demo', demo_engine()), ('dup', dup)]).__enter__()
        try:
            self.assertIn('already taken', '\n'.join(logs.output))
            self.assertEqual(srv.client().get('/api/demo/items/1').json()['title'], 'اول')
        finally:
            srv.__exit__(None, None, None)


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.auth = fake_auth()
        self.srv = Running(engines=[('auth', self.auth), ('demo', demo_engine())]).__enter__()
        self.client = self.srv.client()

    def tearDown(self):
        self.srv.__exit__(None, None, None)

    def test_login_who_logout(self):
        me = self.client.get('/api/test/me').json()
        self.assertEqual((me['user'], me['admin']), (None, None))
        resp = self.client.post('/api/test/login', {'token': 'tok-user-1'})
        cookie = resp.cookies()['panah_session']
        for part in ('panah_session=tok-user-1', 'Path=/', 'HttpOnly', 'SameSite=Lax', 'Max-Age='):
            self.assertIn(part, cookie)
        me = self.client.get('/api/test/me').json()
        self.assertEqual(me['user'], {'id': 1, 'name': 'احمد'})
        self.assertIsNone(me['admin'])
        self.assertEqual(self.client.get('/api/demo/user').status, 200)
        self.assertEqual(self.client.get('/api/demo/admin').status, 403)
        resp = self.client.post('/api/test/logout')
        self.assertIn('Max-Age=0', resp.cookies()['panah_session'])
        self.assertNotIn('panah_session', self.client.cookies)
        self.assertIsNone(self.client.get('/api/test/me').json()['user'])

    def test_admin_and_unknown_token(self):
        self.client.post('/api/test/login', {'token': 'tok-admin'})
        me = self.client.get('/api/test/me').json()
        self.assertEqual((me['user'], me['admin']), (None, {'id': 1, 'username': 'admin'}))
        self.assertEqual(self.client.get('/api/demo/admin').status, 200)
        self.client.cookies['panah_session'] = 'tok-nobody'
        me = self.client.get('/api/test/me').json()
        self.assertEqual((me['user'], me['admin']), (None, None))
        self.assertIn('tok-nobody', self.auth.calls, 'who got the token from the cookie')

    def test_bad_token_never_reaches_cookie(self):
        with self.assertLogs('panah.web', 'ERROR'):
            resp = self.client.post('/api/test/login', {'token': 'x; Path=/evil'})
        self.assertEqual(resp.status, 500)
        self.assertNotIn('panah_session', resp.cookies())

    def test_without_auth_everyone_is_guest(self):
        with Running(engines=[('demo', demo_engine())]) as srv:
            client = srv.client()
            client.cookies['panah_session'] = 'tok-user-1'
            me = client.get('/api/demo/me').json()
        self.assertEqual((me['user'], me['admin']), (None, None))


class DeviceCookieTest(unittest.TestCase):
    def test_device_id_given_once_and_kept(self):
        with Running(engines=[('demo', demo_engine())]) as srv:
            client = srv.client()
            first = client.get('/api/demo/me')
            cookie = first.cookies()['panah_device']
            for part in ('Path=/', 'Max-Age=31536000', 'HttpOnly', 'SameSite=Lax'):
                self.assertIn(part, cookie)
            device = first.json()['device']
            self.assertRegex(device, r'^[A-Za-z0-9_-]{16,64}$')
            self.assertEqual(client.cookies['panah_device'], device)
            second = client.get('/api/demo/me')
            self.assertNotIn('panah_device', second.cookies(), 'cookie present: no new one issued')
            self.assertEqual(second.json()['device'], device)
            client.cookies['panah_device'] = 'bad'
            third = client.get('/api/demo/me').json()['device']
            self.assertNotEqual(third, 'bad')
            other = srv.client().get('/api/demo/me').json()['device']
            self.assertNotEqual(other, device, 'another device gets its own id')


class UploadTest(unittest.TestCase):
    def setUp(self):
        self.srv = Running().__enter__()
        self.client = self.srv.client()

    def tearDown(self):
        self.srv.__exit__(None, None, None)

    def test_upload_and_serve(self):
        for raw, mime, ext in ((PNG, 'image/png', '.png'), (JPEG, 'image/jpeg', '.jpg'), (WEBP, 'image/webp', '.webp')):
            resp = self.client.post('/api/upload', {'data_url': data_url(raw, mime), 'name': '../../عکس.png'})
            self.assertEqual(resp.status, 200, resp.body)
            info = resp.json()
            self.assertRegex(info['url'], r'^/uploads/\d{8}-[0-9a-f]{16}\%s$' % ext)
            self.assertEqual((info['type'], info['size']), (mime, len(raw)))
            self.assertTrue((self.srv.app.uploads_dir / info['url'].rsplit('/', 1)[1]).is_file(), 'in data/uploads/')
            got = self.client.get(info['url'])
            self.assertEqual((got.status, got.body, got.headers['Content-Type']), (200, raw, mime))

    def test_type_detected_by_content(self):
        resp = self.client.post('/api/upload', {'data_url': data_url(JPEG, 'image/png')})
        self.assertTrue(resp.json()['url'].endswith('.jpg'), 'type from the content, not the declared one')

    def test_rejected(self):
        cases = [
            ({}, 400, 'no_image'),
            ({'data_url': ''}, 400, 'no_image'),
            ({'data_url': data_url(b'GIF89a' + bytes(20), 'image/gif')}, 400, 'bad_image'),
            ({'data_url': data_url('سلام'.encode('utf-8'), 'text/plain')}, 400, 'bad_image'),
            ({'data_url': 'data:image/png;base64,@@not-base64@@'}, 400, 'bad_image'),
            ({'data_url': 'data:image/png,plain-not-base64'}, 400, 'bad_image'),
            ({'data_url': data_url(PNG + bytes(3 * 1024 * 1024))}, 413, 'image_too_large'),
        ]
        for body, status, code in cases:
            resp = self.client.post('/api/upload', body)
            self.assertEqual((resp.status, resp.json()['error']), (status, code), str(body)[:60])
            self.assertTrue(resp.json()['message'])
        self.assertEqual(list(self.srv.app.uploads_dir.glob('*')) if self.srv.app.uploads_dir.exists() else [], [])

    def test_body_over_limit(self):
        resp = self.client.request('POST', '/api/upload', raw=b'{' + b' ' * (web.MAX_BODY + 10) + b'}')
        self.assertEqual((resp.status, resp.json()['error']), (413, 'too_large'))

    def test_uploads_folder_is_closed(self):
        folder = self.srv.app.uploads_dir
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'note.txt').write_text('secret', encoding='utf-8')
        for path in ('/uploads/note.txt', '/uploads/../panah.db', '/uploads/%2e%2e/panah.db', '/uploads/',
                     '/uploads/..%2fpanah.db', '/uploads/missing.png'):
            self.assertEqual(self.client.get(path).status, 404, path)


class RateLimitTest(unittest.TestCase):
    """Upload limit: a sliding window per key (device, IP)."""

    def setUp(self):
        self.now = [1000.0]
        self.limit = web.RateLimit(3, 60, clock=lambda: self.now[0])

    def test_each_key_counted_and_window_slides(self):
        for _ in range(3):
            self.assertTrue(self.limit.take(['device:a', 'ip:1'])[0])
        self.assertEqual(self.limit.take(['device:a', 'ip:2']), (False, 60), 'device a used up its limit')
        self.assertEqual(self.limit.take(['device:b', 'ip:1']), (False, 60), 'IP 1 used up its limit')
        self.assertTrue(self.limit.take(['device:b', 'ip:2'])[0], 'another device and IP: allowed')
        self.now[0] += 45
        self.assertEqual(self.limit.take(['device:a', 'ip:3']), (False, 15), 'retry_after: until a slot frees up')
        self.now[0] += 15
        self.assertTrue(self.limit.take(['device:a', 'ip:3'])[0], 'the hour (a minute here) has passed: allowed again')

    def test_release_and_no_limit(self):
        ok, ticket = self.limit.take(['k'])
        self.limit.release(ticket)
        for _ in range(3):
            self.assertTrue(self.limit.take(['k'])[0], 'a released take does not count')
        self.assertFalse(self.limit.take(['k'])[0])
        free = web.RateLimit(None, 60)
        self.assertTrue(all(free.take(['k'])[0] for _ in range(100)))


class UploadLimitTest(unittest.TestCase):
    """POST /api/upload: at most 20 successful uploads per hour per device and per IP; beyond that, 429 with retry_after."""

    def upload(self, client, raw=PNG):
        return client.post('/api/upload', {'data_url': data_url(raw)})

    def test_twenty_per_hour_then_429(self):
        with Running(engines=[('auth', fake_auth())]) as srv:
            self.assertEqual(srv.app.config['upload_limit'], 20)
            self.assertEqual(srv.app.config['upload_window'], 3600)
            guest = srv.client()
            for _ in range(3):
                self.assertEqual(self.upload(guest, b'not an image').status, 400)
            for n in range(20):
                self.assertEqual(self.upload(guest).status, 200, 'upload %d of 20' % (n + 1))
            with self.assertLogs('panah.web', 'WARNING'):
                resp = self.upload(guest)
            self.assertEqual(resp.status, 429)
            body = resp.json()
            self.assertEqual(body['error'], 'too_many_uploads')
            self.assertTrue(body['message'])
            self.assertTrue(1 <= body['retry_after'] <= 3600, body)
            self.assertEqual(resp.headers['Retry-After'], str(body['retry_after']))
            self.assertEqual(len(list(srv.app.uploads_dir.glob('*.png'))), 20, 'nothing over the limit reached the disk')
            other_device = srv.client()
            with self.assertLogs('panah.web', 'WARNING'):
                self.assertEqual(self.upload(other_device).status, 429, 'same IP: limited too')
            admin = srv.client()
            admin.post('/api/test/login', {'token': 'tok-admin'})
            self.assertEqual(self.upload(admin).status, 200, 'admin: no limit')

    def test_limit_from_config(self):
        with Running(config={'upload_limit': 2}) as srv:
            client = srv.client()
            with self.assertLogs('panah.web', 'WARNING'):
                self.assertEqual([self.upload(client).status for _ in range(3)], [200, 200, 429])
        with Running(config={'upload_limit': None}) as srv:
            client = srv.client()
            self.assertEqual({self.upload(client).status for _ in range(22)}, {200})


class StaticTest(unittest.TestCase):
    FILES = {
        'index.html': '<!doctype html><html dir="rtl"><body>سلام</body></html>',
        'js/app.js': 'window.App = {};',
        'css/app.css': 'body{direction:rtl}',
        'img/logo.svg': '<svg xmlns="http://www.w3.org/2000/svg"/>',
        'i18n/fa.json': '{"hello": "سلام"}',
        'manifest.webmanifest': '{"name": "PANAH"}',
        'fonts/v.woff2': 'wOF2',
        '.hidden': 'hidden',
    }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self._tmp.name)
        self.web_dir = root / 'site' / 'web'
        for name, text in self.FILES.items():
            path = self.web_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        (root / 'site' / 'secret.txt').write_text('TOP-SECRET', encoding='utf-8')
        (root / 'secret.txt').write_text('TOP-SECRET', encoding='utf-8')
        self.srv = Running(web_dir=self.web_dir).__enter__()
        self.client = self.srv.client()

    def tearDown(self):
        self.srv.__exit__(None, None, None)
        self._tmp.cleanup()

    def test_index_and_types(self):
        expected = {
            '/': 'text/html; charset=utf-8',
            '/index.html': 'text/html; charset=utf-8',
            '/js/app.js': 'text/javascript; charset=utf-8',
            '/css/app.css': 'text/css; charset=utf-8',
            '/img/logo.svg': 'image/svg+xml',
            '/i18n/fa.json': 'application/json; charset=utf-8',
            '/manifest.webmanifest': 'application/manifest+json; charset=utf-8',
            '/fonts/v.woff2': 'font/woff2',
        }
        for path, content_type in expected.items():
            resp = self.client.get(path)
            self.assertEqual(resp.status, 200, path)
            self.assertEqual(resp.headers['Content-Type'], content_type, path)
            self.assertEqual(resp.headers['Cache-Control'], 'no-cache', path)
        self.assertEqual(self.client.get('/').text, self.FILES['index.html'])

    def test_etag_and_head(self):
        first = self.client.get('/js/app.js')
        again = self.client.get('/js/app.js', headers={'If-None-Match': first.headers['ETag']})
        self.assertEqual((again.status, again.body), (304, b''))
        head = self.client.request('HEAD', '/')
        self.assertEqual((head.status, head.body), (200, b''))
        self.assertEqual(head.headers['Content-Length'], str(len(self.FILES['index.html'].encode('utf-8'))))

    def test_no_way_out_of_folder(self):
        attempts = ['/../secret.txt', '/../../secret.txt', '/%2e%2e/secret.txt', '/js/../../secret.txt',
                    '/..%2fsecret.txt', '/..%5csecret.txt', '/%2e%2e%5c%2e%2e%5csecret.txt', '/..\\secret.txt',
                    '/C:/secret.txt', '/C:%5csecret.txt', '/%00index.html', '/.hidden', '/js/.%2e/.%2e/secret.txt',
                    '/index.html%00.js', '/index.html.', '//secret.txt', '/%2fsecret.txt']
        for path in attempts:
            resp = self.client.get(path)
            self.assertEqual(resp.status, 404, path)
            self.assertNotIn(b'TOP-SECRET', resp.body, path)
            self.assertNotIn(b'hidden', resp.body, path)

    def test_missing_and_post(self):
        self.assertEqual(self.client.get('/nope.js').status, 404)
        self.assertEqual(self.client.request('POST', '/index.html', body={}).status, 405)

    def test_safe_path_directly(self):
        root = self.web_dir
        self.assertEqual(web.safe_path(root, '/js/app.js'), (root / 'js' / 'app.js').resolve())
        self.assertEqual(web.safe_path(root, '/'), root.resolve())
        for bad in ('/../x', '/a/../../x', '/%2e%2e/x', '/C:%5cWindows', '/a%5c..%5c..%5cx', '/a:b', '/con.'):
            self.assertIsNone(web.safe_path(root, bad), bad)


class ConfigAndBindTest(unittest.TestCase):
    def test_config(self):
        with Running() as srv:
            config = srv.app.config
            self.assertTrue({'port', 'db_path', 'data_dir', 'base_dir', 'usgs'} <= set(config))
            self.assertEqual(config['port'], srv.port)
            self.assertEqual(config['db_path'], srv.app.db_path)
            self.assertEqual(srv.app.db_path, (srv.tmp / 'data' / 'panah.db').resolve())
            self.assertEqual(config['data_dir'], srv.app.db_path.parent)
            self.assertTrue((config['base_dir'] / 'app' / 'main.py').is_file())
            self.assertIs(config['usgs'], True)
            self.assertEqual(srv.app.uploads_dir, srv.app.data_dir / 'uploads')

    def test_start_skipped_when_off_in_config(self):
        usgs = types.ModuleType('usgs')
        usgs.started = []
        usgs.start = usgs.started.append
        with Running(engines=[('usgs', usgs)], config={'usgs': False}) as srv:
            self.assertIs(srv.app.config['usgs'], False)
        self.assertEqual(usgs.started, [])
        with Running(engines=[('usgs', usgs)]):
            pass
        self.assertEqual(len(usgs.started), 1)

    def test_start_failure_logged_server_lives(self):
        bad = types.ModuleType('bad')

        def start(app):
            raise RuntimeError('no thread for you')
        bad.start = start
        with self.assertLogs('panah.web', 'ERROR'):
            srv = Running(engines=[('bad', bad)]).__enter__()
        try:
            self.assertEqual(srv.client().get('/api/health').status, 200)
            self.assertIn('bad', srv.app.failed)
        finally:
            srv.__exit__(None, None, None)

    def test_busy_port_takes_next(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(('127.0.0.1', 0))
        blocker.listen(1)
        busy = blocker.getsockname()[1]
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                app = web.App(Path(tmp) / 'panah.db', engines=[])
                server = web.bind(app, '127.0.0.1', busy, tries=10)
                try:
                    self.assertNotEqual(server.server_address[1], busy)
                    self.assertGreater(server.server_address[1], busy)
                    self.assertEqual(app.config['port'], server.server_address[1])
                finally:
                    server.server_close()
        finally:
            blocker.close()


class PoolAndQueueTest(unittest.TestCase):
    """req.db from the pool: the connection is reused, parallel writes are not lost; the connection backlog is 1024."""

    def setUp(self):
        mod = types.ModuleType('writer')

        def init(db):
            db.execute('CREATE TABLE IF NOT EXISTS writes (id INTEGER PRIMARY KEY, n INTEGER)')

        def write(req):
            req.db.execute('INSERT INTO writes (n) VALUES (?)', (req.body.get('n', 0),))
            return 200, {'conn': id(req.db)}

        def count(req):
            return 200, {'count': req.db.execute('SELECT count(*) FROM writes').fetchone()[0], 'conn': id(req.db)}

        mod.init = init
        mod.routes = lambda: [('POST', '/api/w', write), ('GET', '/api/w', count)]
        self.srv = Running(engines=[('writer', mod)]).__enter__()
        self.client = self.srv.client()

    def tearDown(self):
        self.srv.__exit__(None, None, None)

    def test_listen_queue_size(self):
        self.assertGreaterEqual(web.Server.request_queue_size, 1024)

    def test_connection_reused(self):
        first = self.client.post('/api/w', {'n': 1}).json()['conn']
        second = self.client.get('/api/w').json()
        self.assertEqual(second['count'], 1, 'the write is visible to the next request')
        self.assertEqual(second['conn'], first, 'the same connection from the pool')
        self.assertGreaterEqual(self.srv.app.pool.idle(), 1)

    def test_parallel_writes(self):
        errors = []

        def worker(n):
            client = Client(self.srv.port)
            for i in range(10):
                status = client.post('/api/w', {'n': n * 100 + i}).status
                if status != 200:
                    errors.append(status)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(self.client.get('/api/w').json()['count'], 160)

    def test_burst_of_connections_is_not_dropped(self):
        socks, slowest = [], 0.0
        try:
            for _ in range(150):  # more than the old backlog of 64, less than the Windows cap (~200)
                started = time.monotonic()
                socks.append(socket.create_connection(('127.0.0.1', self.srv.port), timeout=10))
                slowest = max(slowest, time.monotonic() - started)
            for sock in socks:
                sock.sendall(b'GET /api/health HTTP/1.0\r\nHost: x\r\n\r\n')
            answers = []
            for sock in socks:
                data = b''
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                answers.append(data.split(b'\r\n', 1)[0])
        finally:
            for sock in socks:
                sock.close()
        self.assertLess(slowest, 0.9, 'no connection waited for a TCP retry (1 s)')
        self.assertEqual(set(answers), {b'HTTP/1.0 200 OK'})

    def test_stop_closes_pool(self):
        self.client.get('/api/w')
        self.srv.app.stop()
        self.assertEqual(self.srv.app.pool.idle(), 0)


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


FAKE_CLOUDFLARED = """
import os, sys, time
from pathlib import Path
Path(os.environ['FAKE_TUNNEL_RECORD']).write_text(' '.join(sys.argv[1:]) + '|' + os.environ.get('NO_AUTOUPDATE', ''),
                                                  encoding='utf-8')
print('2026-11-07T00:00:00Z INF Requesting new quick Tunnel on trycloudflare.com...', flush=True)
if os.environ.get('FAKE_TUNNEL_MODE') == 'fail':
    print('2026-11-07T00:00:01Z ERR failed to request quick Tunnel', flush=True)
    sys.exit(3)
print('2026-11-07T00:00:01Z INF |  https://Gentle-Fox-Example-1234.trycloudflare.com    |', flush=True)
deadline = time.time() + 60  # safety net: never outlive a test run
while time.time() < deadline:
    time.sleep(0.2)
"""


def fake_cloudflared(folder):
    """A stand-in for cloudflared: records its arguments, prints a quick-tunnel address → (command list, exe path)."""
    folder = Path(folder)
    script = folder / 'fake_cloudflared.py'
    script.write_text(FAKE_CLOUDFLARED, encoding='utf-8')
    if os.name == 'nt':
        exe = folder / 'fake-cloudflared.bat'
        exe.write_text('@"%s" "%s" %%*\r\n' % (sys.executable, script), encoding='utf-8')
    else:
        exe = folder / 'fake-cloudflared'
        exe.write_text('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, script), encoding='utf-8')
        exe.chmod(0o755)
    return [sys.executable, str(script)], exe


class Launcher:
    """python app/main.py in its own process, with its own environment."""

    def __init__(self, args, env=None, port=None, prepare=None):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.tmp.name) / 'data' / 'panah.db'
        if prepare is not None:
            self.db.parent.mkdir(parents=True)
            prepare(self.db.parent)   # e.g. a database left from an earlier version
        full_env = dict(os.environ)
        full_env.pop('PORT', None)
        full_env.pop('ADMIN_PASSWORD', None)
        full_env.update(env or {})
        self.port = port
        self.proc = subprocess.Popen([sys.executable, str(APP_DIR / 'main.py'), '--no-browser', '--no-usgs',
                                      '--db', str(self.db)] + list(args),
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=full_env)

    def wait_health(self, timeout=20):
        client, deadline = Client(self.port), time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                return None
            try:
                return client.get('/api/health', timeout=2).json()
            except OSError:
                time.sleep(0.1)
        return None

    def finish(self, timeout=10):
        if self.proc.poll() is None:
            self.proc.terminate()
        out, err = self.proc.communicate(timeout=timeout)
        self.tmp.cleanup()
        return out.decode('utf-8', 'replace'), err.decode('utf-8', 'replace')


class MainTest(unittest.TestCase):
    """python app/main.py starts the server: /api/health, /api/stream keeps the connection, data goes to the database folder."""

    def test_main_runs(self):
        port = free_port()
        run = Launcher(['--host', '127.0.0.1', '--port', str(port)], port=port)
        try:
            health = run.wait_health()
            self.assertIsNotNone(health, 'server did not start')
            self.assertTrue(health['ok'])
            self.assertEqual(health['port'], port)
            client = Client(port)
            sse = client.stream(timeout=3)
            try:
                self.assertEqual(sse.headers['Content-Type'], 'text/event-stream')
                self.assertEqual(sse.headers['X-Accel-Buffering'], 'no')
                self.assertEqual(client.get('/api/health').json()['live'], 1, 'the stream stays open')
            finally:
                sse.close()
            self.assertTrue(run.db.is_file())
            self.assertTrue((run.db.parent / 'server.log').is_file())
            log_text = (run.db.parent / 'server.log').read_text(encoding='utf-8')
            self.assertIn('GET /api/health -> 200', log_text)
        finally:
            out, err = run.finish()
        self.assertIn('PANAH is running: http://127.0.0.1:%d/' % port, out)

    def test_old_database_taken_over(self):
        """A data folder from before the rename: main.py renames the database to panah.db, the data stays and the
        demo admin gets the password from the README."""
        def old_data(folder):
            app = web.App(folder / launcher.OLD_DB_NAME, config={'usgs': False}).setup()
            if 'auth' not in app.engines:
                self.skipTest('auth engine is not built')
            with app.transaction() as db:
                db.execute("INSERT INTO users (phone, name) VALUES ('+93700000001', 'Old resident')")
                app.engines['auth'].set_admin_password(db, 'admin', launcher.OLD_DEMO_PASSWORD)

        port = free_port()
        run = Launcher(['--host', '127.0.0.1', '--port', str(port)], port=port, prepare=old_data)
        try:
            self.assertIsNotNone(run.wait_health(), 'server did not start')
            client = Client(port)
            old = client.post('/api/admin/login', {'username': 'admin', 'password': launcher.OLD_DEMO_PASSWORD})
            new = client.post('/api/admin/login', {'username': 'admin', 'password': 'panah1234'})
            names = sorted(p.name for p in run.db.parent.iterdir())
            check = sqlite3.connect(str(run.db))
            try:
                user = check.execute("SELECT name FROM users WHERE phone = '+93700000001'").fetchone()
            finally:
                check.close()
        finally:
            out, err = run.finish()
        self.assertIn('panah.db', names)
        self.assertNotIn(launcher.OLD_DB_NAME, names, 'renamed, not copied')
        self.assertEqual(user, ('Old resident',), 'the earlier data is kept')
        self.assertEqual((old.status, new.status), (400, 200), 'the README password signs in')
        self.assertIn('renamed to panah.db', err)

    def test_port_from_environment_is_exact(self):
        port = free_port()
        run = Launcher(['--host', '127.0.0.1'], env={'PORT': str(port)}, port=port)
        try:
            health = run.wait_health()
            self.assertIsNotNone(health, 'server did not start on PORT')
            self.assertEqual(health['port'], port)
        finally:
            out, err = run.finish()
        self.assertIn('PANAH is running: http://127.0.0.1:%d/' % port, out)

    def test_busy_port_from_environment_fails_instead_of_moving(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(('127.0.0.1', 0))
        blocker.listen(1)
        busy = blocker.getsockname()[1]
        try:
            run = Launcher(['--host', '127.0.0.1'], env={'PORT': str(busy)}, port=busy)
            code = run.proc.wait(30)
            out, err = run.finish()
        finally:
            blocker.close()
        self.assertEqual(code, 1)
        self.assertIn('cannot listen on port %d' % busy, err)

    def test_bad_port_in_environment(self):
        run = Launcher(['--host', '127.0.0.1'], env={'PORT': 'eighty'})
        code = run.proc.wait(30)
        out, err = run.finish()
        self.assertNotEqual(code, 0)
        self.assertIn('PORT must be a port number', err)

    def test_tunnel_link_printed(self):
        port = free_port()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, exe = fake_cloudflared(tmp)
            record = Path(tmp) / 'args.txt'
            run = Launcher(['--host', '127.0.0.1', '--port', str(port), '--tunnel', str(exe)],
                           env={'FAKE_TUNNEL_RECORD': str(record)}, port=port)
            try:
                self.assertIsNotNone(run.wait_health(), 'server did not start')
                self.assertTrue(wait_until(lambda: (run.db.parent / 'tunnel.log').is_file() and
                                           'trycloudflare.com' in (run.db.parent / 'tunnel.log').read_text(
                                               encoding='utf-8'), 15))
            finally:
                out, err = run.finish()
            self.assertEqual(record.read_text(encoding='utf-8'), 'tunnel --url http://127.0.0.1:%d|true' % port)
        self.assertIn('https://gentle-fox-example-1234.trycloudflare.com', out)
        self.assertIn('Secure link for phones', out)
        if os.name != 'nt':  # SIGTERM stops main.py cleanly, and it stops cloudflared too
            self.assertIn('Stopping...', out)

    def test_help(self):
        out = subprocess.run([sys.executable, str(APP_DIR / 'main.py'), '--help'], capture_output=True, timeout=30)
        self.assertEqual(out.returncode, 0)
        for flag in (b'--port', b'--host', b'--db', b'--no-browser', b'--no-usgs', b'--tunnel', b'PANAH'):
            self.assertIn(flag, out.stdout)


class LauncherUnitTest(unittest.TestCase):
    def test_resolve_port(self):
        self.assertEqual(launcher.resolve_port(None, {}), (8000, False))
        self.assertEqual(launcher.resolve_port(None, {'PORT': ' 10000 '}), (10000, True), 'cloud: exact port')
        self.assertEqual(launcher.resolve_port(9001, {'PORT': '10000'}), (9001, False), '--port wins')
        for bad in ('abc', '0', '70000', '-5'):
            with self.assertRaises(SystemExit):
                launcher.resolve_port(None, {'PORT': bad})

    def test_host_option(self):
        self.assertEqual(launcher.parse_args([]).host, '0.0.0.0')
        self.assertEqual(launcher.parse_args(['--host', '127.0.0.1']).host, '127.0.0.1')
        self.assertIsNone(launcher.parse_args([]).port)

    def test_old_database_renamed_with_wal(self):
        """The old file goes to panah.db with its -wal and -shm: data still only in the -wal (the window was closed
        while the server ran) is kept."""
        def kept(path):
            db = sqlite3.connect(str(path))
            try:
                row = db.execute("SELECT value FROM settings WHERE key = 'kept'").fetchone()
            except sqlite3.OperationalError:  # not even the table: it is in the -wal too
                row = None
            finally:
                db.close()
            return row

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            source, data = Path(tmp) / 'source', Path(tmp) / 'data'
            data.mkdir()
            old = source / launcher.OLD_DB_NAME
            db = store.connect(old)
            try:
                db.execute('PRAGMA wal_autocheckpoint = 0')  # nothing moves from the -wal into the file
                store.set_setting(db, 'kept', 'yes')
                db.commit()
                for suffix in ('', '-wal', '-shm'):  # copied while open: as a server stopped by closing its window
                    shutil.copyfile(str(old) + suffix, str(data / (old.name + suffix)))
            finally:
                db.close()
            shutil.copyfile(str(data / old.name), str(Path(tmp) / 'alone.db'))
            self.assertIsNone(kept(Path(tmp) / 'alone.db'), 'the row is only in the -wal')
            self.assertTrue(launcher.adopt_old_database(data / 'panah.db'))
            self.assertEqual(sorted(p.name for p in data.iterdir()), ['panah.db', 'panah.db-shm', 'panah.db-wal'])
            self.assertEqual(kept(data / 'panah.db'), ('"yes"',))

    def test_old_database_left_alone(self):
        """Nothing to take over: no old file, another name was asked for (--db other.db), or panah.db is there."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            data = Path(tmp)
            self.assertFalse(launcher.adopt_old_database(data / 'panah.db'), 'no old file')
            (data / launcher.OLD_DB_NAME).write_bytes(b'old')
            self.assertFalse(launcher.adopt_old_database(data / 'other.db'), 'another name is not ours to fill')
            (data / 'panah.db').write_bytes(b'new')
            self.assertFalse(launcher.adopt_old_database(data / 'panah.db'), 'panah.db is never replaced')
            self.assertEqual((data / 'panah.db').read_bytes(), b'new')
            self.assertEqual((data / launcher.OLD_DB_NAME).read_bytes(), b'old', 'the old file is not deleted')
            self.assertFalse((data / 'other.db').exists())

    def test_old_demo_password_renewed(self):
        """The former demo password becomes the README one; a password the owner chose stays."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            app = web.App(Path(tmp) / 'panah.db', web_dir=tmp, config={'usgs': False}).setup()
            auth = app.engines.get('auth')
            if auth is None:
                self.skipTest('auth engine is not built')

            def password_is(password):
                with app.transaction() as db:
                    row = db.execute("SELECT password_hash FROM admins WHERE username = 'admin'").fetchone()
                return auth.check_password(password, row['password_hash'])

            with app.transaction() as db:
                auth.set_admin_password(db, 'admin', launcher.OLD_DEMO_PASSWORD)
            self.assertTrue(launcher.renew_old_demo_password(app))
            self.assertTrue(password_is(auth.DEMO_ADMIN_PASSWORD))
            self.assertFalse(launcher.renew_old_demo_password(app), 'only once')
            with app.transaction() as db:
                auth.set_admin_password(db, 'admin', 'Own-Secret-77')
            self.assertFalse(launcher.renew_old_demo_password(app))
            self.assertTrue(password_is('Own-Secret-77'), 'the owner\'s password stays')

    def test_tunnel_finds_address_and_stops(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            command, _ = fake_cloudflared(tmp)
            record, found, ended = Path(tmp) / 'args.txt', [], []
            with mock.patch.dict(os.environ, {'FAKE_TUNNEL_RECORD': str(record)}):
                tunnel = launcher.Tunnel(command, 8123, Path(tmp) / 'tunnel.log', found.append, ended.append).start()
            try:
                self.assertTrue(tunnel.found.wait(15), 'address not found')
            finally:
                tunnel.stop()
            self.assertEqual(found, ['https://gentle-fox-example-1234.trycloudflare.com'])
            self.assertEqual(record.read_text(encoding='utf-8'), 'tunnel --url http://127.0.0.1:8123|true')
            self.assertIsNotNone(tunnel.proc.poll(), 'cloudflared stopped with the server')
            self.assertIn('Requesting new quick Tunnel', (Path(tmp) / 'tunnel.log').read_text(encoding='utf-8'))
            self.assertEqual(ended, [], 'a stop on purpose is not a failure')

    def test_tunnel_failure_reported(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            command, _ = fake_cloudflared(tmp)
            found, ended = [], []
            env = {'FAKE_TUNNEL_RECORD': str(Path(tmp) / 'args.txt'), 'FAKE_TUNNEL_MODE': 'fail'}
            with mock.patch.dict(os.environ, env):
                tunnel = launcher.Tunnel(command, 8123, Path(tmp) / 'tunnel.log', found.append, ended.append).start()
            self.assertTrue(wait_until(lambda: ended, 15))
            tunnel.stop()
        self.assertEqual((found, ended), ([], [3]))


class AdminPasswordTest(unittest.TestCase):
    """ADMIN_PASSWORD: on a public address the demo password from the README must not open the admin panel."""

    def test_admin_password_from_environment(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            app = web.App(Path(tmp) / 'panah.db', web_dir=tmp, config={'usgs': False})
            app.setup()
            if 'auth' not in app.engines:
                self.skipTest('auth engine is not built')
            self.assertFalse(launcher.apply_admin_password(app, ''))
            self.assertTrue(launcher.apply_admin_password(app, 'New-Secret-77'))
            server = web.bind(app, '127.0.0.1', 0)
            web.serve_in_thread(server)
            try:
                client = Client(server.server_address[1])
                old = client.post('/api/admin/login', {'username': 'admin', 'password': 'panah1234'})
                new = client.post('/api/admin/login', {'username': 'admin', 'password': 'New-Secret-77'})
            finally:
                app.stop()
                server.shutdown()
                server.server_close()
        self.assertEqual(old.status, 400, 'the demo password no longer works')
        self.assertEqual(new.status, 200)


class ClientIpTest(unittest.TestCase):
    """Behind cloudflared on this computer every request comes from 127.0.0.1: then CF-Connecting-IP is the client."""

    def test_client_ip_rules(self):
        header = {'CF-Connecting-IP': '203.0.113.7'}
        self.assertEqual(web.client_ip('127.0.0.1', header), '203.0.113.7')
        self.assertEqual(web.client_ip('::ffff:127.0.0.1', header), '203.0.113.7')
        self.assertEqual(web.client_ip('::1', {'CF-Connecting-IP': '2001:DB8::1'}), '2001:db8::1')
        self.assertEqual(web.client_ip('192.168.1.5', header), '192.168.1.5', 'from the network the header is ignored')
        self.assertEqual(web.client_ip('127.0.0.1', {'CF-Connecting-IP': 'not-an-ip'}), '127.0.0.1')
        self.assertEqual(web.client_ip('127.0.0.1', {}), '127.0.0.1')
        self.assertEqual(web.client_ip('::ffff:10.0.0.2', {}), '10.0.0.2')

    def test_upload_limit_per_tunnel_client(self):
        with Running(config={'upload_limit': 1}) as srv:
            first, second = srv.client(), srv.client()
            body = {'data_url': data_url(PNG)}
            ip1, ip2 = {'CF-Connecting-IP': '203.0.113.1'}, {'CF-Connecting-IP': '203.0.113.2'}
            self.assertEqual(first.post('/api/upload', body, headers=ip1).status, 200)
            with self.assertLogs('panah.web', 'WARNING'):
                self.assertEqual(first.post('/api/upload', body, headers=ip1).status, 429)
            self.assertEqual(second.post('/api/upload', body, headers=ip2).status, 200,
                             'another phone through the tunnel is not blocked by the first one')


class RunBatTest(unittest.TestCase):
    def test_run_bat(self):
        bat = APP_DIR.parent / 'run.bat'
        raw = bat.read_bytes()
        self.assertNotIn(b'\n', raw.replace(b'\r\n', b''), 'Windows line endings (CRLF)')
        text = raw.decode('ascii')
        for part in ('chcp 65001', 'cd /d "%~dp0"', 'py -3', 'python app\\main.py', 'https://www.python.org',
                     'pause'):
            self.assertIn(part, text)

    def test_share_online_bat(self):
        raw = (APP_DIR.parent / 'share-online.bat').read_bytes()
        self.assertNotIn(b'\n', raw.replace(b'\r\n', b''), 'Windows line endings (CRLF)')
        text = raw.decode('ascii')
        for part in ('chcp 65001', 'cd /d "%~dp0"', 'cloudflared.exe', 'where cloudflared.exe',
                     'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe',
                     'app\\main.py --host 127.0.0.1 --tunnel "%TUNNEL%"', 'https://www.python.org', 'pause'):
            self.assertIn(part, text)


if __name__ == '__main__':
    unittest.main()
