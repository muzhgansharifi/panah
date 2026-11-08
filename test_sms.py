"""Tests for sms: test mode (a row in sms_outbox + a line in sms.log), stats, the admin list, the provider."""
import datetime
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from server import sms, store  # noqa: E402
from server.common import ApiError  # noqa: E402

UTC = datetime.timezone.utc
TIME = re.compile(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
ADMIN = {'id': 1, 'username': 'admin', 'name': '', 'phone': '+93700000000'}
OTP_TEXT = 'کد تأیید پناه: 123456\nد پناه د تایید کوډ: 123456'


def make_req(db, admin=None, user=None, query=None):
    return types.SimpleNamespace(db=db, user=user, admin=admin, body={}, query=query or {}, params={},
                                 ip='127.0.0.1', device_id='dev-test-000001', app=None)


class FakeProvider:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def send(self, phone, text):
        self.calls.append((phone, text))
        if self.fail:
            raise OSError('network is down')


class SmsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / 'data' / 'panah.db'
        self.db = store.connect(self.db_path)
        sms.init(self.db)
        self.log = self.tmp / 'data' / 'sms.log'

    def tearDown(self):
        sms.use_provider(None)
        self.db.close()
        self._tmp.cleanup()

    def log_lines(self):
        return self.log.read_text(encoding='utf-8').splitlines()

    # --- sending in test mode

    def test_send_test_mode_row_and_log(self):
        self.assertTrue(sms.is_test_mode())
        sms_id = sms.send(self.db, '+93777123454', OTP_TEXT, 'otp')
        row = self.db.execute('SELECT * FROM sms_outbox WHERE id = ?', (sms_id,)).fetchone()
        self.assertEqual((row['phone'], row['text'], row['kind'], row['status']),
                         ('+93777123454', OTP_TEXT, 'otp', 'test'))
        self.assertRegex(row['created_at'], TIME)
        lines = self.log_lines()
        self.assertEqual(len(lines), 1, 'one SMS = one log line, even if the text has two lines')
        fields = lines[0].split('\t')
        self.assertEqual(fields[0], row['created_at'])
        self.assertEqual(fields[1:5], ['#%d' % sms_id, 'otp', 'test', '+93777123454'])
        self.assertEqual(fields[5], 'کد تأیید پناه: 123456 | د پناه د تایید کوډ: 123456')

    def test_each_send_appends_a_line(self):
        ids = [sms.send(self.db, '+9377712345%d' % n, 'هشدار سیلاب %d' % n, 'alert') for n in range(3)]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), 3)
        lines = self.log_lines()
        self.assertEqual([line.split('\t')[1] for line in lines], ['#%d' % i for i in ids])
        self.assertEqual(self.db.execute('SELECT count(*) FROM sms_outbox').fetchone()[0], 3)

    def test_bad_input_raises_value_error(self):
        for phone, text, kind in [('+93777123454', 'x', 'email'), ('', 'x', 'otp'), (None, 'x', 'otp'),
                                  ('  ', 'x', 'otp'), ('+93777123454', '', 'otp'), ('+93777123454', None, 'sos'),
                                  ('+93777123454', '   ', 'other')]:
            with self.assertRaises(ValueError, msg=repr((phone, text, kind))):
                sms.send(self.db, phone, text, kind)
        self.assertEqual(self.db.execute('SELECT count(*) FROM sms_outbox').fetchone()[0], 0)
        self.assertFalse(self.log.exists())

    def test_all_kinds_and_default(self):
        for kind in sms.KINDS:
            sms.send(self.db, '+93777123454', 'متن', kind)
        default_id = sms.send(self.db, '+93777123454', 'متن')
        self.assertEqual(self.db.execute('SELECT kind FROM sms_outbox WHERE id = ?', (default_id,)).fetchone()[0],
                         'other')

    def test_init_twice_keeps_data(self):
        sms.send(self.db, '+93777123454', 'متن', 'other')
        sms.init(self.db)
        self.db.commit()
        sms.init(self.db)
        self.assertEqual(self.db.execute('SELECT count(*) FROM sms_outbox').fetchone()[0], 1)

    def test_memory_database_has_no_log(self):
        db = store.connect(':memory:')
        self.addCleanup(db.close)
        sms.init(db)
        self.assertIsNone(sms.log_path(db))
        self.assertEqual(sms.send(db, '+93777123454', 'متن', 'other'), 1)

    def test_log_next_to_database(self):
        self.assertEqual(sms.log_path(self.db), self.log)

    def test_start_uses_app_data_dir(self):
        other = self.tmp / 'elsewhere'
        key = sms._path_key(self.db_path)
        self.addCleanup(sms._data_dirs.pop, key, None)
        sms.start(types.SimpleNamespace(config={'data_dir': other, 'db_path': self.db_path}))
        sms.send(self.db, '+93777123454', 'متن', 'other')
        self.assertTrue((other / 'sms.log').is_file())
        self.assertFalse(self.log.exists())

    # --- statistics

    def test_stats_today_is_kabul_day(self):
        times = ['2026-11-05T19:29:59', '2026-11-05T19:30:00', '2026-11-06T09:00:00']
        kinds = ['otp', 'alert', 'alert']
        for moment, kind in zip(times, kinds):
            with mock.patch.object(sms, '_now', return_value=datetime.datetime.fromisoformat(moment)
                                   .replace(tzinfo=UTC)):
                sms.send(self.db, '+93777123454', 'متن', kind)
        # 10:00 UTC = 14:30 in Kabul on Nov 6; Kabul midnight = Nov 5 19:30 UTC → the first SMS is "yesterday"
        now = datetime.datetime(2026, 11, 6, 10, 0, tzinfo=UTC)
        with mock.patch.object(sms, '_now', return_value=now):
            result = sms.stats(self.db)
        self.assertEqual(result, {'total': 3, 'today': 2,
                                  'by_kind': {'otp': 1, 'alert': 2, 'sos': 0, 'other': 0}})

    def test_stats_empty(self):
        self.assertEqual(sms.stats(self.db), {'total': 0, 'today': 0,
                                              'by_kind': {'otp': 0, 'alert': 0, 'sos': 0, 'other': 0}})

    # --- GET /api/admin/sms

    def test_admin_list_requires_admin(self):
        for req in (make_req(self.db), make_req(self.db, user={'id': 5, 'phone': '+93777123454'})):
            with self.assertRaises(ApiError) as cm:
                sms.admin_sms(req)
            self.assertEqual((cm.exception.status, cm.exception.code), (403, 'admin_required'))

    def test_admin_list(self):
        first = sms.send(self.db, '+93777123454', OTP_TEXT, 'otp')
        sms.send(self.db, '+93777123455', 'هشدار زلزله', 'alert')
        last = sms.send(self.db, '+93777123456', 'SOS', 'sos')
        status, data = sms.admin_sms(make_req(self.db, admin=ADMIN))
        self.assertEqual(status, 200)
        self.assertEqual([item['id'] for item in data['items']], [last, last - 1, first], 'newest first')
        self.assertEqual(set(data['items'][0]), {'id', 'phone', 'text', 'kind', 'status', 'created_at'})
        self.assertEqual(data['items'][2]['text'], OTP_TEXT)
        self.assertEqual((data['total'], data['today'], data['test_mode']), (3, 3, True))
        self.assertEqual(data['by_kind']['alert'], 1)

        _, data = sms.admin_sms(make_req(self.db, admin=ADMIN, query={'limit': '2'}))
        self.assertEqual(len(data['items']), 2)
        _, data = sms.admin_sms(make_req(self.db, admin=ADMIN, query={'limit': ['1']}))  # like parse_qs
        self.assertEqual([item['id'] for item in data['items']], [last])
        _, data = sms.admin_sms(make_req(self.db, admin=ADMIN, query={'limit': '0'}))
        self.assertEqual(len(data['items']), 1, 'limit below 1 gives one')
        _, data = sms.admin_sms(make_req(self.db, admin=ADMIN, query={'kind': 'otp'}))
        self.assertEqual([item['id'] for item in data['items']], [first])
        for query, code in [({'limit': 'abc'}, 'bad_limit'), ({'kind': 'email'}, 'bad_kind')]:
            with self.assertRaises(ApiError) as cm:
                sms.admin_sms(make_req(self.db, admin=ADMIN, query=query))
            self.assertEqual((cm.exception.status, cm.exception.code), (400, code))

    def test_admin_list_limit_capped(self):
        for n in range(3):
            sms.send(self.db, '+93777123454', 'متن %d' % n, 'other')
        with mock.patch.object(sms, 'MAX_LIMIT', 2):
            _, data = sms.admin_sms(make_req(self.db, admin=ADMIN, query={'limit': '1000'}))
        self.assertEqual(len(data['items']), 2)

    def test_routes(self):
        self.assertIn(('GET', '/api/admin/sms', sms.admin_sms), sms.routes())

    # --- the real provider ("future")

    def test_provider_sends(self):
        fake = FakeProvider()
        self.assertIsNone(sms.use_provider(fake))
        self.assertFalse(sms.is_test_mode())
        sms_id = sms.send(self.db, '+93777123454', 'متن', 'alert')
        self.assertEqual(fake.calls, [('+93777123454', 'متن')])
        self.assertEqual(self.db.execute('SELECT status FROM sms_outbox WHERE id = ?', (sms_id,)).fetchone()[0],
                         'sent')
        self.assertIs(sms.use_provider(None), fake)
        self.assertTrue(sms.is_test_mode())

    def test_provider_failure_raises_sms_error(self):
        sms.use_provider(FakeProvider(fail=True))
        with self.assertLogs('server.sms', 'WARNING'), self.assertRaises(sms.SmsError) as cm:
            sms.send(self.db, '+93777123454', 'متن', 'sos')
        error = cm.exception
        self.assertEqual((error.phone, error.kind), ('+93777123454', 'sos'))
        self.assertIsInstance(error.cause, OSError)
        self.assertIs(error.__cause__, error.cause)
        self.assertIn('network is down', str(error))
        self.assertEqual(self.db.execute('SELECT count(*) FROM sms_outbox').fetchone()[0], 0, 'a failure is not a send')
        self.assertEqual(sms.stats(self.db)['total'], 0)
        fields = self.log_lines()[0].split('\t')
        self.assertEqual(fields[1:5], ['#-', 'sos', 'failed', '+93777123454'], 'the attempt is in the log')
        self.assertIn('network is down', fields[-1])

    def test_twilio_is_a_stub(self):
        twilio = sms.TwilioProvider('ACxxxxxxxx', 'token', '+15550000000')
        with self.assertRaises(NotImplementedError) as cm:
            twilio.send('+93777123454', 'متن')
        self.assertIn('TwilioProvider', str(cm.exception))
        self.assertIn('api.twilio.com', sms.TwilioProvider.__doc__)
        sms.use_provider(twilio)
        with self.assertLogs('server.sms', 'WARNING'), self.assertRaises(sms.SmsError) as cm:
            sms.send(self.db, '+93777123454', 'متن', 'otp')
        self.assertIsInstance(cm.exception.cause, NotImplementedError)
        self.assertEqual(self.db.execute('SELECT count(*) FROM sms_outbox').fetchone()[0], 0)


KABUL = (34.5553, 69.2075)
FLOOD = {'type': 'flood', 'place': 'کارته چهار، کابل', 'lat': KABUL[0], 'lon': KABUL[1],
         'safe_place': 'پارک شهر نو', 'safe_lat': 34.5340, 'safe_lon': 69.1680}


class FlakyProvider:
    """Fails the first `fails` sends (None: every send), then takes them."""

    def __init__(self, fails=None):
        self.fails, self.calls = fails, []

    def send(self, phone, text):
        self.calls.append(phone)
        if self.fails is None or len(self.calls) <= self.fails:
            raise OSError('provider down (call %d)' % len(self.calls))


class AlertQueueTest(unittest.TestCase):
    """sms with the alerts queue, through alerts.create: a provider failure is retried, never counted as sent."""

    def setUp(self):
        try:
            from server import alerts, geo
        except ImportError as exc:
            self.skipTest('alerts: %s' % exc)
        self.alerts, self.geo = alerts, geo
        self.real_retries = alerts.SMS_RETRY_S
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp = Path(self._tmp.name)
        self.db = store.connect(self.tmp / 'panah.db')
        for engine in (sms, geo, alerts):
            engine.init(self.db)
        self.db.execute("INSERT INTO users (phone, last_lat, last_lon) VALUES ('+93700000001', ?, ?)", KABUL)
        self.db.commit()
        geo.reset_network_state()
        patchers = (mock.patch.object(geo, 'fetch_json', side_effect=OSError('tests run offline')),
                    mock.patch.object(alerts, 'SMS_RETRY_S', (0,) * len(self.real_retries)))  # retries without waiting
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.cleanup)    # runs first: the queue is drained before the patches go

    def cleanup(self):
        self.db.commit()
        self.alerts.wait_sms(self.db, 10)
        sms.use_provider(None)
        self.geo.reset_network_state()
        self.db.close()
        self._tmp.cleanup()

    def create_and_wait(self):
        result = self.alerts.create(self.db, dict(FLOOD), 'admin')
        self.db.commit()
        self.assertTrue(self.alerts.wait_sms(self.db, 10), 'the alert SMS queue did not finish')
        self.assertEqual((result['notified'], result['sms']), (1, 1))
        notification = self.db.execute('SELECT sms_status, sms_attempts, sms_id, sms_error FROM notifications')
        return notification.fetchone()

    def outbox(self):
        return [tuple(row) for row in self.db.execute('SELECT id, phone, kind, status FROM sms_outbox')]

    def log_statuses(self):
        path = self.tmp / 'sms.log'
        return [line.split('\t')[3] for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []

    def test_retry_delays_are_2_10_60(self):
        self.assertEqual(tuple(self.real_retries), (2, 10, 60))

    def test_test_mode_unchanged(self):
        row = self.create_and_wait()
        outbox = self.outbox()
        self.assertEqual([item[1:] for item in outbox], [('+93700000001', 'alert', 'test')])
        self.assertEqual(tuple(row), ('sent', 0, outbox[0][0], None))

    def test_two_failures_then_one_send(self):
        provider = FlakyProvider(fails=2)
        sms.use_provider(provider)
        with self.assertLogs('server.alerts', 'WARNING'), self.assertLogs('server.sms', 'WARNING'):
            row = self.create_and_wait()
        self.assertEqual(len(provider.calls), 3, '2 failures and 1 success')
        outbox = self.outbox()
        self.assertEqual([item[1:] for item in outbox], [('+93700000001', 'alert', 'sent')], 'exactly one send')
        self.assertEqual(tuple(row), ('sent', 2, outbox[0][0], None))
        self.assertEqual(self.log_statuses(), ['failed', 'failed', 'sent'])

    def test_provider_always_down_is_marked_failed(self):
        provider = FlakyProvider()
        sms.use_provider(provider)
        with self.assertLogs('server.alerts', 'WARNING') as logs, self.assertLogs('server.sms', 'WARNING'):
            row = self.create_and_wait()
        attempts = 1 + len(self.real_retries)    # the first try and 3 retries (after 2/10/60 s in real time)
        self.assertEqual(len(provider.calls), attempts)
        self.assertEqual((row['sms_status'], row['sms_attempts'], row['sms_id']), ('failed', attempts, None))
        self.assertIn('SmsError', row['sms_error'])
        self.assertIn('provider down', row['sms_error'])
        self.assertEqual(self.outbox(), [], 'no false "sent"')
        self.assertEqual(sms.stats(self.db)['total'], 0)
        self.assertEqual(self.log_statuses(), ['failed'] * attempts)
        self.assertTrue(any('marked failed' in line for line in logs.output))

if __name__ == '__main__':
    unittest.main()
