"""Tests for auth: phone number, SMS code (limits, expiry, attempts), sessions and who, profile, admin login, HTTP.

Handlers are called directly with a simple req (types.SimpleNamespace), the way web builds it: user and admin come
from auth.who by token. Time is faked by replacing auth._now (Clock), no waiting.
"""
import datetime
import hashlib
import math
import re
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from server import auth, sms, store  # noqa: E402
from server.common import ApiError  # noqa: E402

UTC = datetime.timezone.utc
T0 = datetime.datetime(2026, 11, 6, 6, 0, 0, tzinfo=UTC)
PHONE = '+93777123454'


class Clock:
    """Fake clock: auth._now → clock.now; clock.tick(seconds) "moves the clock forward"."""

    def __init__(self, start=T0):
        self.now = start

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now += datetime.timedelta(seconds=seconds)


class PhoneTest(unittest.TestCase):
    def test_all_written_forms_of_one_number(self):
        forms = [
            '0777123454', '777123454', '+93777123454', '0093777123454', '93777123454',
            '077 712 3454', '077-712-3454', ' 0777 123-454 ', '(077) 712.3454',
            '۰۷۷۷۱۲۳۴۵۴', '۰۷۷۷ ۱۲۳ ۴۵۴', '٠٧٧٧١٢٣٤٥٤', '۰۷۷۷-123 ٤٥٤',
            '+93 (0) 777 123 454', '+93 0777123454', '930777123454', '00930777123454',
            '‏+93 777‑123–454‎', '⁧۰۷۷۷‌۱۲۳۴۵۴⁩', 777123454,
        ]
        for form in forms:
            self.assertEqual(auth.normalize_phone(form), PHONE, repr(form))

    def test_other_operators(self):
        self.assertEqual(auth.normalize_phone('0700123456'), '+93700123456')
        self.assertEqual(auth.normalize_phone('079 912 3456'), '+93799123456')
        self.assertEqual(auth.normalize_phone('+93 72 000 0001'), '+93720000001')

    def test_bad_numbers(self):
        bad = ['', '   ', None, True, 12.5, [], {}, 'abc', '07771234547', '۰۷۷۷ ۱۲۳ ۴۵۴۷', '077712345',
               '0577123454', '0201234567', '+1 202 555 0143', '+9377712345', '+930777123454 1', '07771234a5',
               '0093', '+93', '17771234540', '٧٧٧١٢٣٤٥', '++93777123454']
        for value in bad:
            with self.assertRaises(ApiError, msg=repr(value)) as cm:
                auth.normalize_phone(value)
            self.assertEqual((cm.exception.status, cm.exception.code), (400, 'bad_phone'), repr(value))
            self.assertTrue(cm.exception.message_fa)

    def test_digits_helpers(self):
        self.assertEqual(auth.to_ascii_digits('۱۲۳٤٥٦'), '123456')
        self.assertEqual(auth.dari_digits(45), '۴۵')


class AuthBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp = Path(self._tmp.name)
        self.db = store.connect(self.tmp / 'data' / 'panah.db')
        sms.init(self.db)
        auth.init(self.db)
        self.clock = Clock()
        patcher = mock.patch.object(auth, '_now', self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def req(self, body=None, token=None):
        """req as from web: user/admin via auth.who by the cookie token; set_session remembers the token."""
        found = auth.who(self.db, token) if token else {'user': None, 'admin': None}
        req = types.SimpleNamespace(
            db=self.db, user=found['user'], admin=found['admin'], body={} if body is None else body, query={},
            params={}, ip='127.0.0.1', device_id='dev-test-000001', app=None,
            cookies={'panah_session': token} if token else {}, issued=[], cleared=[])
        req.set_session = lambda value, max_age=None: req.issued.append(value)
        req.clear_session = lambda: req.cleared.append(True)
        return req

    def call(self, handler, body=None, token=None):
        req = self.req(body, token)
        status, data = handler(req)
        self.assertEqual(status, 200)
        return data, req

    def fails(self, status, code, handler, body=None, token=None):
        with self.assertRaises(ApiError) as cm:
            handler(self.req(body, token))
        self.assertEqual((cm.exception.status, cm.exception.code), (status, code))
        self.assertTrue(cm.exception.message_fa, 'every error has a Dari text')
        return cm.exception

    def ask_code(self, phone=PHONE):
        data, _ = self.call(auth.request_code, {'phone': phone})
        return data['test_code']

    def login(self, phone=PHONE):
        """Code → login → (token, verify response)."""
        code = self.ask_code(phone)
        data, req = self.call(auth.verify_code, {'phone': phone, 'code': code})
        self.assertEqual(len(req.issued), 1)
        return req.issued[0], data

    def other_code(self, code):
        return '%06d' % ((int(code) + 1) % 1000000)


class CodeTest(AuthBase):
    def test_request_code_answer(self):
        data, _ = self.call(auth.request_code, {'phone': '۰۷۷۷ ۱۲۳ ۴۵۴'})
        self.assertEqual(set(data), {'phone', 'expires_in', 'resend_in', 'test_code'})
        self.assertEqual((data['phone'], data['expires_in'], data['resend_in']), (PHONE, 300, 45))
        self.assertRegex(data['test_code'], r'^[0-9]{6}$')

    def test_only_hash_in_database(self):
        code = self.ask_code()
        row = self.db.execute('SELECT * FROM otp_codes').fetchone()
        self.assertNotIn(code, [str(value) for value in tuple(row)])
        self.assertEqual(row['code_hash'], hashlib.sha256(('%s:%s' % (row['salt'], code)).encode()).hexdigest())
        self.assertEqual((row['phone'], row['purpose'], row['status'], row['attempts']), (PHONE, 'user', 'active', 0))
        self.assertEqual(row['created_at'], '2026-11-06T06:00:00Z')
        self.assertEqual(row['expires_at'], '2026-11-06T06:05:00Z')

    def test_sms_in_dari_and_pashto(self):
        code = self.ask_code()
        row = self.db.execute('SELECT * FROM sms_outbox').fetchone()
        self.assertEqual((row['phone'], row['kind'], row['status']), (PHONE, 'otp', 'test'))
        self.assertEqual(row['text'], 'کد تأیید پناه: %s\nد پناه د تایید کوډ: %s' % (code, code))
        self.assertEqual(self.db.execute('SELECT sms_id FROM otp_codes').fetchone()[0], row['id'])
        log = (self.tmp / 'data' / 'sms.log').read_text(encoding='utf-8')
        self.assertIn('کد تأیید پناه: %s' % code, log)

    def test_codes_are_random(self):
        codes = set()
        for _ in range(5):
            codes.add(self.ask_code())
            self.clock.tick(46)
        self.assertGreater(len(codes), 1)

    def test_resend_not_before_45_seconds(self):
        self.ask_code()
        self.clock.tick(10)
        error = self.fails(429, 'too_soon', auth.request_code, {'phone': PHONE})
        self.assertEqual(error.extra['retry_after'], 35)
        self.assertIn('۳۵', error.message_fa)
        self.clock.tick(34)
        self.assertEqual(self.fails(429, 'too_soon', auth.request_code, {'phone': PHONE}).extra['retry_after'], 1)
        self.clock.tick(1)
        self.ask_code()

    def test_five_codes_per_hour(self):
        for _ in range(5):
            self.ask_code()
            self.clock.tick(46)
        error = self.fails(429, 'too_many', auth.request_code, {'phone': PHONE})
        self.assertEqual(error.extra['retry_after'], 3600 - 5 * 46)
        self.ask_code('0700123456')  # the limit is per number, another number is fine
        self.clock.tick(3600 - 5 * 46 - 1)
        self.fails(429, 'too_many', auth.request_code, {'phone': PHONE})
        self.clock.tick(1)
        self.ask_code()

    def test_new_code_replaces_old(self):
        first = self.ask_code()
        self.clock.tick(46)
        second = self.ask_code()
        if first != second:
            self.fails(400, 'wrong_code', auth.verify_code, {'phone': PHONE, 'code': first})
        data, _ = self.call(auth.verify_code, {'phone': PHONE, 'code': second})
        self.assertEqual(data['user']['phone'], PHONE)

    def test_provider_failure_gives_503_and_stores_nothing(self):
        class Down:
            def send(self, phone, text):
                raise OSError('provider down')

        class Up:
            def __init__(self):
                self.sent = []

            def send(self, phone, text):
                self.sent.append(text)

        self.addCleanup(sms.use_provider, None)
        sms.use_provider(Down())
        with self.assertLogs('server.sms', 'WARNING'):
            self.fails(503, 'sms_failed', auth.request_code, {'phone': PHONE})
        self.assertEqual(self.db.execute('SELECT count(*) FROM otp_codes').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM sms_outbox').fetchone()[0], 0)
        up = Up()
        sms.use_provider(up)
        data, _ = self.call(auth.request_code, {'phone': PHONE})   # right away: the failed try is not counted
        self.assertIsNone(data['test_code'], 'with a real provider the code comes only by SMS')
        self.assertEqual(len(up.sent), 1)
        self.call(auth.verify_code, {'phone': PHONE, 'code': re.search('[0-9]{6}', up.sent[0]).group()})

    def test_bad_phone(self):
        self.fails(400, 'bad_phone', auth.request_code, {'phone': '12345'})
        self.fails(400, 'bad_phone', auth.request_code, {})
        self.fails(400, 'bad_body', auth.request_code, ['0777123454'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM sms_outbox').fetchone()[0], 0)


class VerifyTest(AuthBase):
    def test_full_path_code_login_profile_logout(self):
        code = self.ask_code('077-712-3454')
        data, req = self.call(auth.verify_code, {'phone': '۰۷۷۷۱۲۳۴۵۴', 'code': code})
        self.assertTrue(data['is_new'])
        user = data['user']
        self.assertEqual((user['phone'], user['name'], user['health'], user['contacts']), (PHONE, '', [], []))
        token = req.issued[0]
        self.assertGreaterEqual(len(token), 40)
        self.assertEqual(auth.who(self.db, token), {'user': user, 'admin': None})

        data, _ = self.call(auth.me, token=token)
        self.assertEqual(data, {'user': user, 'admin': None})

        profile = {'name': ' احمد یوسفی ', 'blood_group': 'O+', 'health': ['diabetes', 'heart'],
                   'contacts': [{'name': 'همسر', 'phone': '0799 123 456'}],
                   'home_lat': 34.5550, 'home_lon': 69.2070, 'home_address': 'کارته چهار، کابل'}
        data, _ = self.call(auth.update_me, profile, token=token)
        user = data['user']
        self.assertEqual(user['name'], 'احمد یوسفی')
        self.assertEqual(user['blood_group'], 'O+')
        self.assertEqual(user['health'], ['diabetes', 'heart'])
        self.assertEqual(user['contacts'], [{'name': 'همسر', 'phone': '+93799123456'}])
        self.assertEqual((user['home_lat'], user['home_lon'], user['home_address']),
                         (34.555, 69.207, 'کارته چهار، کابل'))

        data, _ = self.call(auth.update_location, {'lat': 34.52, 'lon': 69.18, 'accuracy': 25}, token=token)
        self.assertEqual((data['user']['last_lat'], data['user']['last_lon']), (34.52, 69.18))
        self.assertEqual(auth.who(self.db, token)['user'], data['user'])

        data, req = self.call(auth.logout, token=token)
        self.assertEqual(data, {'ok': True})
        self.assertEqual(req.cleared, [True])
        self.assertEqual(auth.who(self.db, token), {'user': None, 'admin': None})
        data, _ = self.call(auth.me, token=token)
        self.assertEqual(data, {'user': None, 'admin': None}, 'after logout: guest, 200')
        self.fails(401, 'login_required', auth.update_me, {'name': 'x'}, token=token)
        self.assertEqual(self.db.execute('SELECT count(*) FROM sessions').fetchone()[0], 0)

    def test_second_login_same_user(self):
        _, first = self.login()
        self.clock.tick(46)
        _, second = self.login('0777123454')
        self.assertFalse(second['is_new'])
        self.assertEqual(second['user']['id'], first['user']['id'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM users').fetchone()[0], 1)

    def test_five_wrong_codes_burn_the_code(self):
        code = self.ask_code()
        wrong = self.other_code(code)
        left = []
        for _ in range(5):
            left.append(self.fails(400, 'wrong_code', auth.verify_code, {'phone': PHONE, 'code': wrong})
                        .extra['attempts_left'])
        self.assertEqual(left, [4, 3, 2, 1, 0])
        self.fails(410, 'expired', auth.verify_code, {'phone': PHONE, 'code': code})
        self.assertEqual(self.db.execute('SELECT status FROM otp_codes').fetchone()[0], 'burned')
        self.assertEqual(self.db.execute('SELECT count(*) FROM users').fetchone()[0], 0)

    def test_badly_typed_code_costs_no_attempt(self):
        code = self.ask_code()
        for value in ('12345', '1234567', 'abcdef', '', None, [], 12.5):
            self.fails(400, 'bad_code', auth.verify_code, {'phone': PHONE, 'code': value})
        self.assertEqual(self.db.execute('SELECT attempts FROM otp_codes').fetchone()[0], 0)
        self.call(auth.verify_code, {'phone': PHONE, 'code': code})

    def test_code_with_dari_digits_and_spaces(self):
        code = self.ask_code()
        typed = auth.dari_digits(code[:3]) + ' ' + code[3:]
        data, _ = self.call(auth.verify_code, {'phone': PHONE, 'code': typed})
        self.assertTrue(data['is_new'])

    def test_code_expires_after_5_minutes(self):
        code = self.ask_code()
        self.clock.tick(300)
        self.fails(410, 'expired', auth.verify_code, {'phone': PHONE, 'code': code})

    def test_code_valid_until_5_minutes(self):
        code = self.ask_code()
        self.clock.tick(299)
        self.call(auth.verify_code, {'phone': PHONE, 'code': code})

    def test_code_works_once(self):
        code = self.ask_code()
        self.call(auth.verify_code, {'phone': PHONE, 'code': code})
        self.fails(410, 'expired', auth.verify_code, {'phone': PHONE, 'code': code})

    def test_no_code_requested(self):
        self.fails(400, 'no_code', auth.verify_code, {'phone': PHONE, 'code': '123456'})

    def test_code_is_bound_to_number(self):
        code = self.ask_code()
        self.fails(400, 'no_code', auth.verify_code, {'phone': '0700123456', 'code': code})
        self.fails(400, 'bad_phone', auth.verify_code, {'phone': '07001', 'code': code})


class SessionTest(AuthBase):
    def test_who_rejects_bad_tokens(self):
        self.login()
        for token in (None, '', 'garbage', 123, 'x' * 300, b'bytes'):
            self.assertEqual(auth.who(self.db, token), {'user': None, 'admin': None}, repr(token))

    def test_token_kept_only_as_hash(self):
        token, _ = self.login()
        row = self.db.execute('SELECT * FROM sessions').fetchone()
        self.assertNotIn(token, [str(value) for value in tuple(row)])
        self.assertEqual(row['token_hash'], hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(row['expires_at'], '2026-12-06T06:00:00Z')
        self.assertEqual((row['ip'], row['device_id']), ('127.0.0.1', 'dev-test-000001'))

    def test_session_lives_30_days(self):
        token, data = self.login()
        self.clock.tick(30 * 24 * 3600 - 1)
        self.assertEqual(auth.who(self.db, token)['user']['id'], data['user']['id'])
        self.clock.tick(1)
        self.assertEqual(auth.who(self.db, token), {'user': None, 'admin': None})

    def test_logout_ends_only_this_session(self):
        phone_token, _ = self.login()
        self.clock.tick(46)
        laptop_token, _ = self.login()
        self.call(auth.logout, token=laptop_token)
        self.assertIsNone(auth.who(self.db, laptop_token)['user'])
        self.assertIsNotNone(auth.who(self.db, phone_token)['user'])

    def test_logout_without_login(self):
        data, req = self.call(auth.logout)
        self.assertEqual(data, {'ok': True})
        self.assertEqual(req.cleared, [True])

    def test_require_helpers(self):
        token, data = self.login()
        user_req = self.req(token=token)
        self.assertEqual(auth.require_user(user_req), data['user'])
        for req, helper, status, code in [(self.req(), auth.require_user, 401, 'login_required'),
                                          (self.req(), auth.require_admin, 403, 'admin_required'),
                                          (user_req, auth.require_admin, 403, 'admin_required')]:
            with self.assertRaises(ApiError) as cm:
                helper(req)
            self.assertEqual((cm.exception.status, cm.exception.code), (status, code))
        admin_token = self.admin_token()
        admin = auth.require_admin(self.req(token=admin_token))
        self.assertEqual(admin['username'], 'admin')
        self.fails(401, 'login_required', auth.update_me, {'name': 'x'}, token=admin_token)

    def admin_token(self):
        _, req = self.call(auth.admin_login, {'username': auth.DEMO_ADMIN_USERNAME,
                                              'password': auth.DEMO_ADMIN_PASSWORD})
        return req.issued[0]


class ProfileTest(AuthBase):
    def setUp(self):
        super().setUp()
        self.token, _ = self.login()

    def update(self, body):
        data, _ = self.call(auth.update_me, body, token=self.token)
        return data['user']

    def test_partial_update_keeps_other_fields(self):
        self.update({'name': 'فاطمه', 'blood_group': 'A+', 'home_lat': '34.5', 'home_lon': 69.2})
        user = self.update({'blood_group': 'B-'})
        self.assertEqual((user['name'], user['blood_group'], user['home_lat'], user['home_lon']),
                         ('فاطمه', 'B-', 34.5, 69.2))
        self.assertEqual(self.update({})['blood_group'], 'B-')
        user = self.update({'phone': '+93700000001', 'id': 99, 'created_at': 'x'})  # foreign fields are ignored
        self.assertEqual((user['phone'], user['id']), (PHONE, 1))

    def test_lenient_forms(self):
        user = self.update({'blood_group': 'ab−', 'health': ['Diabetes', 'heart', 'diabetes'],
                            'home_lat': '۳۴٫۵۵۵۰', 'home_lon': '٦٩.٢٠٧', 'name': None})
        self.assertEqual(user['blood_group'], 'AB-')
        self.assertEqual(user['health'], ['diabetes', 'heart'])
        self.assertEqual((user['home_lat'], user['home_lon']), (34.555, 69.207))
        self.assertEqual(user['name'], '')
        self.assertEqual(self.update({'blood_group': 'Unknown'})['blood_group'], 'unknown')
        self.assertEqual(self.update({'health': ['none', 'epilepsy']})['health'], ['epilepsy'])
        self.assertEqual(self.update({'health': ['none']})['health'], ['none'])
        self.assertEqual(self.update({'health': []})['health'], [])

    def test_contacts(self):
        user = self.update({'contacts': [{'name': 'پدر', 'phone': '۰۷۹۹۱۲۳۴۵۶'},
                                         {'phone': '0700 123 456'},
                                         {'name': 'دوباره', 'phone': '+93799123456'}]})
        self.assertEqual(user['contacts'], [{'name': 'پدر', 'phone': '+93799123456'},
                                            {'name': '', 'phone': '+93700123456'}])
        stored = self.db.execute('SELECT contacts FROM users').fetchone()[0]
        self.assertIn('پدر', stored, 'JSON in the database: Dari as is')
        self.assertEqual(self.update({'contacts': []})['contacts'], [])

    def test_clear_home(self):
        self.update({'home_lat': 34.5, 'home_lon': 69.2})
        user = self.update({'home_lat': None, 'home_lon': None})
        self.assertEqual((user['home_lat'], user['home_lon']), (None, None))

    def test_bad_fields(self):
        cases = [
            ({'name': 123}, 'bad_name'), ({'name': 'ا' * 101}, 'bad_name'),
            ({'blood_group': 'C+'}, 'bad_blood_group'), ({'blood_group': 7}, 'bad_blood_group'),
            ({'health': 'diabetes'}, 'bad_health'), ({'health': ['flu']}, 'bad_health'),
            ({'health': [1]}, 'bad_health'),
            ({'contacts': {'name': 'x'}}, 'bad_contacts'), ({'contacts': ['0799123456']}, 'bad_contacts'),
            ({'contacts': [{'name': 'x', 'phone': '123'}]}, 'bad_contacts'),
            ({'contacts': [{'name': 5, 'phone': '0799123456'}]}, 'bad_contacts'),
            ({'contacts': [{'phone': '07991234%02d' % n} for n in range(11)]}, 'bad_contacts'),
            ({'home_lat': 34.5}, 'bad_home'), ({'home_lon': 69.2}, 'bad_home'),
            ({'home_lat': 'abc', 'home_lon': 69.2}, 'bad_home'), ({'home_lat': 100, 'home_lon': 69.2}, 'bad_home'),
            ({'home_lat': 34.5, 'home_lon': -181}, 'bad_home'),
            ({'home_lat': float('nan'), 'home_lon': 69.2}, 'bad_home'),
            ({'home_lat': True, 'home_lon': 69.2}, 'bad_home'),
            ({'home_address': 12}, 'bad_address'), ({'home_address': 'آ' * 301}, 'bad_address'),
        ]
        for body, code in cases:
            error = self.fails(400, code, auth.update_me, body, token=self.token)
            self.assertTrue(error.message_fa, body)
        error = self.fails(400, 'bad_health', auth.update_me, {'health': ['flu']}, token=self.token)
        self.assertIn('epilepsy', error.extra['allowed'])
        error = self.fails(400, 'bad_contacts', auth.update_me,
                           {'contacts': [{'phone': '0799123456'}, {'phone': '1'}]}, token=self.token)
        self.assertEqual(error.extra['index'], 1)
        self.fails(400, 'bad_body', auth.update_me, ['name'], token=self.token)
        user = self.update({})
        self.assertEqual((user['name'], user['health'], user['home_lat']), ('', [], None), 'nothing written')

    def test_profile_needs_login(self):
        self.fails(401, 'login_required', auth.update_me, {'name': 'x'})
        self.fails(401, 'login_required', auth.update_location, {'lat': 34.5, 'lon': 69.2})

    def test_me_for_guest_is_200(self):
        for token in (None, 'garbage'):
            data, _ = self.call(auth.me, token=token)
            self.assertEqual(data, {'user': None, 'admin': None}, repr(token))
        self.clock.tick(30 * 24 * 3600)  # session expired: a guest too
        data, _ = self.call(auth.me, token=self.token)
        self.assertEqual(data, {'user': None, 'admin': None})

    def test_location_checks(self):
        for body in ({'lat': 91, 'lon': 69.2}, {'lat': 34.5}, {'lat': 'x', 'lon': 69.2}, {'lat': 34.5, 'lon': None},
                     {'lat': 34.5, 'lon': 69.2, 'accuracy': -1}, {'lat': 34.5, 'lon': 69.2, 'accuracy': 'far'}):
            self.fails(400, 'bad_location', auth.update_location, body, token=self.token)
        data, _ = self.call(auth.update_location, {'lat': '34.5', 'lon': 69, 'accuracy': None}, token=self.token)
        self.assertEqual((data['user']['last_lat'], data['user']['last_lon']), (34.5, 69.0))


class AdminTest(AuthBase):
    def admin_login(self, username=auth.DEMO_ADMIN_USERNAME, password=auth.DEMO_ADMIN_PASSWORD):
        return self.call(auth.admin_login, {'username': username, 'password': password})

    def wrong(self, username='admin'):
        return self.fails(400, 'wrong_login', auth.admin_login, {'username': username, 'password': 'nope'})

    def test_demo_admin_seeded_once(self):
        auth.init(self.db)
        auth.init(self.db)
        rows = self.db.execute('SELECT * FROM admins').fetchall()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row['username'], row['phone']), (auth.DEMO_ADMIN_USERNAME, auth.DEMO_ADMIN_PHONE))
        algorithm, iterations, salt, digest = row['password_hash'].split('$')
        self.assertEqual(algorithm, 'pbkdf2_sha256')
        self.assertGreaterEqual(int(iterations), 200000)
        self.assertRegex(salt, r'^[0-9a-f]{32}$')
        self.assertRegex(digest, r'^[0-9a-f]{64}$')
        self.assertNotIn(auth.DEMO_ADMIN_PASSWORD, row['password_hash'])
        self.assertTrue(auth.check_password(auth.DEMO_ADMIN_PASSWORD, row['password_hash']))
        self.assertFalse(auth.check_password('panah12345', row['password_hash']))
        self.assertFalse(auth.check_password('x', 'garbage'))
        self.assertNotEqual(auth.hash_password('same'), auth.hash_password('same'), 'each hash has its own salt')

    def test_no_demo_admin_when_others_exist(self):
        self.db.execute('DELETE FROM admins')
        auth.add_admin(self.db, 'Rescue', 'secret-1', '0799123456', 'تیم نجات')
        auth.init(self.db)
        names = [row[0] for row in self.db.execute('SELECT username FROM admins')]
        self.assertEqual(names, ['rescue'])
        self.assertEqual(self.db.execute('SELECT phone FROM admins').fetchone()[0], '+93799123456')

    def test_login_and_who(self):
        data, req = self.admin_login(' ADMIN ')
        self.assertEqual(data['admin'], {'id': 1, 'username': 'admin', 'name': auth.DEMO_ADMIN_NAME,
                                         'phone': auth.DEMO_ADMIN_PHONE})
        token = req.issued[0]
        self.assertEqual(auth.who(self.db, token), {'user': None, 'admin': data['admin']})
        me, _ = self.call(auth.me, token=token)
        self.assertEqual(me, {'user': None, 'admin': data['admin']})
        self.call(auth.logout, token=token)
        self.assertIsNone(auth.who(self.db, token)['admin'])

    def test_five_wrong_passwords_lock_for_5_minutes(self):
        self.assertEqual([self.wrong().extra['attempts_left'] for _ in range(4)], [4, 3, 2, 1])
        error = self.fails(429, 'locked', auth.admin_login, {'username': 'admin', 'password': 'nope'})
        self.assertEqual(error.extra['retry_after'], 300)
        self.clock.tick(120)
        error = self.fails(429, 'locked', auth.admin_login,
                           {'username': 'admin', 'password': auth.DEMO_ADMIN_PASSWORD})
        self.assertEqual(error.extra['retry_after'], 180, 'the correct password waits too')
        self.clock.tick(179)
        self.fails(429, 'locked', auth.admin_login, {'username': 'Admin', 'password': auth.DEMO_ADMIN_PASSWORD})
        self.clock.tick(1)
        self.admin_login()
        self.assertEqual(self.wrong().extra['attempts_left'], 4, 'after login the count starts over')

    def test_success_resets_the_count(self):
        for _ in range(3):
            self.wrong()
        self.admin_login()
        self.assertEqual([self.wrong().extra['attempts_left'] for _ in range(4)], [4, 3, 2, 1])

    def test_unknown_username_is_counted_too(self):
        for _ in range(4):
            self.wrong('ghost')
        self.fails(429, 'locked', auth.admin_login, {'username': 'ghost', 'password': 'nope'})
        self.admin_login()  # an unknown username does not lock out the real admin

    def test_empty_fields_are_not_counted(self):
        for body in ({}, {'username': 'admin'}, {'username': '', 'password': 'x'}, {'username': 'admin',
                                                                                   'password': 5}):
            self.fails(400, 'missing_login', auth.admin_login, body)
        self.assertEqual(self.wrong().extra['attempts_left'], 4)

    def test_dari_digits_in_password(self):
        self.assertTrue(auth.set_admin_password(self.db, 'admin', 'pass1234'))
        self.assertFalse(auth.set_admin_password(self.db, 'nobody', 'pass1234'))
        self.admin_login(password='pass۱۲۳۴')
        self.fails(400, 'wrong_login', auth.admin_login, {'username': 'admin', 'password': auth.DEMO_ADMIN_PASSWORD})

    def phone_code(self, phone=auth.DEMO_ADMIN_PHONE):
        """The code from the latest code SMS to the phone (sms_outbox) — what the admin reads on the phone."""
        row = self.db.execute("SELECT text FROM sms_outbox WHERE phone = ? AND kind = 'otp' ORDER BY id DESC LIMIT 1",
                              (phone,)).fetchone()
        return re.search('[0-9]{6}', row['text']).group()

    def test_admin_one_time_code(self):
        data, _ = self.call(auth.admin_otp, {'username': 'admin'})
        self.assertEqual(data, {'phone': '+9370*****00', 'expires_in': 300, 'resend_in': 45},
                         'no test_code for the admin, not even in test mode')
        self.assertTrue(sms.is_test_mode())
        code = self.phone_code()
        self.assertNotIn(code, repr(data))
        sms_row = self.db.execute('SELECT phone, kind, text FROM sms_outbox').fetchone()
        self.assertEqual((sms_row['phone'], sms_row['kind']), (auth.DEMO_ADMIN_PHONE, 'otp'))
        self.assertIn('کد تأیید پناه: %s' % code, sms_row['text'])
        self.assertIn(code, (self.tmp / 'data' / 'sms.log').read_text(encoding='utf-8'))
        # a user name alone does not open the admin panel
        for body in ({'username': 'admin'}, {'username': 'admin', 'code': ''}):
            self.fails(400, 'bad_code', auth.admin_otp_verify, body)
        error = self.fails(400, 'wrong_code', auth.admin_otp_verify,
                           {'username': 'admin', 'code': self.other_code(code)})
        self.assertEqual(error.extra['attempts_left'], 4)
        result, req = self.call(auth.admin_otp_verify, {'username': 'ADMIN', 'code': code})
        self.assertEqual(result['admin']['username'], 'admin')
        self.assertEqual(auth.who(self.db, req.issued[0])['admin']['id'], 1)
        self.clock.tick(10)
        self.fails(429, 'too_soon', auth.admin_otp, {'username': 'admin'})

    def test_admin_code_errors(self):
        self.fails(400, 'wrong_login', auth.admin_otp, {'username': 'ghost'})
        self.fails(400, 'wrong_login', auth.admin_otp_verify, {'username': 'ghost', 'code': '123456'})
        self.db.execute('UPDATE admins SET phone = NULL')
        self.fails(400, 'no_phone', auth.admin_otp, {'username': 'admin'})

    def test_resident_code_does_not_open_admin(self):
        code = self.ask_code(auth.DEMO_ADMIN_PHONE)  # a resident with the same number as the admin
        self.fails(400, 'no_code', auth.admin_otp_verify, {'username': 'admin', 'code': code})
        data, _ = self.call(auth.admin_otp, {'username': 'admin'})  # admin login has its own counter, not too_soon
        self.assertNotIn('test_code', data)
        admin_code = self.phone_code()
        self.call(auth.verify_code, {'phone': auth.DEMO_ADMIN_PHONE, 'code': code})  # the resident's code is still valid
        self.call(auth.admin_otp_verify, {'username': 'admin', 'code': admin_code})

    def test_admin_code_unlocks_after_wrong_passwords(self):
        for _ in range(4):
            self.wrong()
        self.fails(429, 'locked', auth.admin_login, {'username': 'admin', 'password': 'nope'})
        self.call(auth.admin_otp, {'username': 'admin'})
        self.call(auth.admin_otp_verify, {'username': 'admin', 'code': self.phone_code()})
        self.admin_login()


class RaceTest(unittest.TestCase):
    def test_double_tap_gives_one_code(self):
        """Two code requests for one number at once (separate connections, as in web) → one code and one too_soon."""
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'data' / 'panah.db'
        db = store.connect(path)
        sms.init(db)
        auth.init(db)
        db.commit()
        db.close()
        real_send, results, barrier = sms.send, [], threading.Barrier(2)

        def slow_send(*args, **kwargs):  # the first holds the write longer, the second gets to start
            time.sleep(0.3)
            return real_send(*args, **kwargs)

        def tap():
            conn = store.connect(path)
            try:
                barrier.wait(5)
                try:
                    auth.issue_code(conn, PHONE)
                    results.append('ok')
                except ApiError as exc:
                    results.append(exc.code)
                conn.commit()
            finally:
                conn.close()

        with mock.patch.object(sms, 'send', slow_send):
            threads = [threading.Thread(target=tap) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(15)
        self.assertEqual(sorted(results), ['ok', 'too_soon'])


class RoutesTest(unittest.TestCase):
    def test_routes(self):
        found = {(method, path) for method, path, handler in auth.routes()}
        self.assertEqual(found, {('POST', '/api/auth/code'), ('POST', '/api/auth/verify'),
                                 ('POST', '/api/auth/logout'), ('GET', '/api/me'), ('PUT', '/api/me'),
                                 ('POST', '/api/me/location'), ('POST', '/api/admin/login'),
                                 ('POST', '/api/admin/otp'), ('POST', '/api/admin/otp/verify')})
        for _, _, handler in auth.routes():
            self.assertTrue(callable(handler))


class HttpTest(unittest.TestCase):
    """Through a real web server on a free port: cookies, JSON responses, errors with extra fields."""

    def test_over_http(self):
        tests_dir = str(Path(__file__).resolve().parent)
        if tests_dir not in sys.path:
            sys.path.insert(0, tests_dir)
        try:
            import support
        except ImportError as exc:  # no web.py or support.py: this check waits for wave A
            self.skipTest('web: %s' % exc)
        with support.Running(engines=['sms', 'auth']) as srv:
            self.assertEqual(srv.app.failed, {})
            phone = srv.client()
            r = phone.post('/api/auth/code', {'phone': '۰۷۷۷ ۱۲۳ ۴۵۴'})
            self.assertEqual(r.status, 200, r.text)
            code = r.json()['test_code']
            r = phone.post('/api/auth/code', {'phone': '0777123454'})
            self.assertEqual(r.status, 429)
            self.assertEqual(r.json()['error'], 'too_soon')
            self.assertTrue(0 < r.json()['retry_after'] <= 45)

            r = phone.post('/api/auth/verify', {'phone': '0777123454', 'code': auth.dari_digits(code)})
            self.assertEqual(r.status, 200, r.text)
            self.assertTrue(r.json()['is_new'])
            self.assertIn('panah_session', phone.cookies)
            self.assertIn('HttpOnly', r.cookies()['panah_session'])

            r = phone.request('PUT', '/api/me', {'name': 'احمد', 'blood_group': 'A+'})
            self.assertEqual(r.status, 200, r.text)
            r = phone.get('/api/me')
            self.assertEqual(r.status, 200)
            self.assertEqual((r.json()['user']['phone'], r.json()['user']['name']), (PHONE, 'احمد'))
            self.assertEqual(phone.get('/api/admin/sms').status, 403)

            r = phone.post('/api/auth/logout')
            self.assertEqual(r.status, 200)
            self.assertNotIn('panah_session', phone.cookies)
            r = phone.get('/api/me')
            self.assertEqual((r.status, r.json()), (200, {'user': None, 'admin': None}), 'guest: 200, not 401')
            r = phone.request('PUT', '/api/me', {'name': 'x'})
            self.assertEqual((r.status, r.json()['error']), (401, 'login_required'))
            self.assertEqual(phone.get('/api/admin/sms').status, 403)

            admin = srv.client()
            r = admin.post('/api/admin/login', {'username': 'admin', 'password': 'wrong'})
            self.assertEqual((r.status, r.json()['attempts_left']), (400, 4))
            r = admin.post('/api/admin/login', {'username': auth.DEMO_ADMIN_USERNAME,
                                                'password': auth.DEMO_ADMIN_PASSWORD})
            self.assertEqual(r.status, 200, r.text)
            r = admin.get('/api/admin/sms?limit=5')
            self.assertEqual(r.status, 200, r.text)
            data = r.json()
            self.assertEqual((data['total'], data['test_mode']), (1, True))
            self.assertIn(code, data['items'][0]['text'])
            log = srv.tmp / 'data' / 'sms.log'
            self.assertIn(code, log.read_text(encoding='utf-8'))

            stranger = srv.client()  # knows only the user name
            r = stranger.post('/api/admin/otp', {'username': 'admin'})
            self.assertEqual(r.status, 200, r.text)
            self.assertNotIn('test_code', r.json())
            r = stranger.post('/api/admin/otp/verify', {'username': 'admin'})
            self.assertEqual((r.status, r.json()['error']), (400, 'bad_code'))
            self.assertNotIn('panah_session', stranger.cookies)
            db = store.connect(srv.app.db_path)
            try:
                text = db.execute("SELECT text FROM sms_outbox WHERE phone = ? AND kind = 'otp' ORDER BY id DESC LIMIT 1",
                                  (auth.DEMO_ADMIN_PHONE,)).fetchone()[0]
            finally:
                db.close()
            admin_code = re.search('[0-9]{6}', text).group()
            self.assertIn(admin_code, log.read_text(encoding='utf-8'))
            r = stranger.post('/api/admin/otp/verify', {'username': 'admin', 'code': admin_code})
            self.assertEqual(r.status, 200, r.text)
            self.assertEqual(stranger.get('/api/me').json()['admin']['username'], 'admin')


if __name__ == '__main__':
    unittest.main()
