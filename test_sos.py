"""Tests for the sos engine: SOS without and with login, one open case, auto-reply, access, rescuer reply as an event,
location and track, closing, stats; one check over HTTP (server on a free port, SSE stream)."""
import datetime
import json
import math
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.parse
from pathlib import Path

import support  # noqa: F401 — adds app/ to sys.path

import server
from server import sos, store
from server.common import ApiError

KABUL_UNI = (34.5154398, 69.1338400)


def swap(test, **modules):
    """Replace server.<name> for the duration of a test: a fake module or None (no engine)."""
    for name, module in modules.items():
        full = 'server.' + name
        had_attr, old_attr = hasattr(server, name), getattr(server, name, None)
        had_mod, old_mod = full in sys.modules, sys.modules.get(full)

        def restore(name=name, full=full, had_attr=had_attr, old_attr=old_attr, had_mod=had_mod, old_mod=old_mod):
            if had_mod:
                sys.modules[full] = old_mod
            else:
                sys.modules.pop(full, None)
            if had_attr:
                setattr(server, name, old_attr)
            elif hasattr(server, name):
                delattr(server, name)

        test.addCleanup(restore)
        test.addCleanup(sos.wait_idle)  # background place labels: before the real modules are put back
        sys.modules[full] = module
        if module is None:
            if hasattr(server, name):
                delattr(server, name)
        else:
            setattr(server, name, module)


def fake_live():
    mod = types.ModuleType('fake_live')
    mod.events = []

    def publish(event, data, user_id=None, device_id=None, admins=False, everyone=False):
        json.dumps(data, ensure_ascii=False)  # event data must fit into JSON
        mod.events.append({'event': event, 'data': data, 'user_id': user_id, 'device_id': device_id,
                           'admins': admins, 'everyone': everyone})
        return 1

    mod.publish = publish
    mod.of = lambda name: [e for e in mod.events if e['event'] == name]
    return mod


def haversine(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def fake_geo(name='پوهنتون کابل، ناحیه سوم', gate=None, near=None):
    """Fake geo: place_name (name or "جای lat,lon"; with gate it waits until the gate is opened), nearest_known returns near."""
    mod = types.ModuleType('fake_geo')
    mod.calls = []

    def place_name(db, lat, lon):
        if gate is not None:
            gate.wait(10)
        mod.calls.append((lat, lon))
        return name or 'جای %.4f,%.4f' % (lat, lon)

    mod.place_name = place_name
    mod.distance_km = haversine
    if near is not None:
        mod.nearest_known = lambda db, lat, lon: near
    return mod


def make_req(db, body=None, query=None, params=None, user=None, admin=None, device_id=None):
    return types.SimpleNamespace(db=db, body=body if body is not None else {}, query=query or {},
                                 params=params or {}, user=user, admin=admin, ip='127.0.0.1',
                                 device_id=device_id, app=None)


class SosTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / 'panah.db'
        self.db = store.connect(self.path)
        self.addCleanup(self.db.close)
        sos.init(self.db)
        sos.init(self.db)  # a repeated init breaks nothing
        self.live, self.geo = fake_live(), fake_geo()
        swap(self, live=self.live, geo=self.geo, auth=None)
        cur = self.db.execute(
            'INSERT INTO users (phone, name, blood_group, health, contacts) VALUES (?, ?, ?, ?, ?)',
            ('+93777123454', 'احمد یوسفی', 'O+', store.dumps(['دیابت']),
             store.dumps([{'name': 'همسر', 'phone': '+93700000001'}])))
        self.user = {'id': cur.lastrowid, 'name': 'احمد یوسفی'}
        cur = self.db.execute('INSERT INTO users (phone, name) VALUES (?, ?)', ('+93700000002', 'فاطمه امیری'))
        self.other_user = {'id': cur.lastrowid, 'name': 'فاطمه امیری'}
        self.db.commit()
        self.admin = {'id': 7, 'username': 'admin'}

    # --- helpers
    def call(self, handler, expect=200, **kw):
        status, data = handler(make_req(self.db, **kw))
        self.assertEqual(status, expect)
        json.dumps(data, ensure_ascii=False)
        return data

    def error(self, handler, **kw):
        with self.assertRaises(ApiError) as caught:
            handler(make_req(self.db, **kw))
        return caught.exception

    def sos_guest(self, device='dev-guest-1', **body):
        payload = {'kind': 'default', 'lat': KABUL_UNI[0], 'lon': KABUL_UNI[1], 'accuracy': 12,
                   'battery': 67, 'signal': 'strong'}
        payload.update(body)
        return self.call(sos.post_sos, body=payload, device_id=device)

    # --- SOS
    def test_guest_sos_default_with_auto_reply(self):
        data = self.sos_guest()
        case, messages = data['case'], data['messages']
        self.assertIsNone(case['user_id'])
        self.assertNotIn('device_id', case)  # the device id is not given out
        self.assertEqual((case['kind'], case['status']), ('default', 'open'))
        self.assertEqual((case['lat'], case['lon'], case['accuracy_m']), (KABUL_UNI[0], KABUL_UNI[1], 12))
        self.assertEqual((case['battery'], case['signal']), (67, 'strong'))
        self.assertIsNone(case['place_name'])  # the label comes in the background; the fake has no fallback (nearest_known)
        self.assertEqual([m['sender'] for m in messages], ['user', 'auto'])
        self.assertEqual(messages[0]['text'], 'من در وضعیت اضطراری قرار دارم و نیاز به کمک فوری دارم.')
        self.assertEqual(messages[0]['text_key'], 'sos_default')
        self.assertEqual(messages[1]['text_key'], 'sos_auto_received')
        self.assertIn('تیم نجات در حال حرکت است', messages[1]['text'])
        self.assertTrue(sos.wait_idle(5))
        cards = self.live.of('sos_case')
        self.assertEqual(len(cards), 2)  # right away, then with the place label
        self.assertTrue(cards[0]['admins'])
        self.assertIsNone(cards[0]['user_id'])
        self.assertTrue(cards[0]['data']['is_new'])
        self.assertIsNone(cards[0]['data']['user'])
        self.assertEqual(cards[1]['data']['place_name'], 'پوهنتون کابل، ناحیه سوم')
        self.assertEqual(self.call(sos.get_current, device_id='dev-guest-1')['case']['place_name'],
                         'پوهنتون کابل، ناحیه سوم')
        self.assertEqual(len(self.live.of('sos_location')), 1)
        track = self.call(sos.admin_get, params={'id': str(case['id'])}, admin=self.admin)['track']
        self.assertEqual(len(track), 1)

    def test_user_sos_custom_text(self):
        data = self.call(sos.post_sos, user=self.user, device_id='dev-user-1',
                         body={'kind': 'custom', 'text': '  در ساحه کارته چهار، سیلاب آمده است.  ',
                               'lat': 34.4931, 'lon': 69.1546})
        self.assertEqual(data['case']['user_id'], self.user['id'])
        self.assertEqual(data['case']['kind'], 'custom')
        self.assertEqual(data['messages'][0]['text'], 'در ساحه کارته چهار، سیلاب آمده است.')
        self.assertIsNone(data['messages'][0]['text_key'])
        card = self.live.of('sos_case')[0]['data']
        self.assertEqual(card['user']['name'], 'احمد یوسفی')
        self.assertEqual(card['user']['health'], ['دیابت'])
        self.assertEqual(card['user']['contacts'][0]['name'], 'همسر')

    def test_custom_without_text_is_still_sos_and_bad_kind_is_400(self):
        data = self.sos_guest(kind='custom', text='   ')
        self.assertEqual(data['case']['kind'], 'default')
        self.assertEqual(data['messages'][0]['text_key'], 'sos_default')
        self.assertEqual(self.error(sos.post_sos, body={'kind': 'loud'}, device_id='x').status, 400)
        self.assertEqual(self.error(sos.post_sos, body={'kind': 'custom', 'text': 'ا' * 2001},
                                    device_id='x').status, 400)

    def test_sos_without_or_with_bad_location_still_created(self):
        data = self.call(sos.post_sos, body={}, device_id='dev-no-gps')
        self.assertIsNone(data['case']['lat'])
        self.assertIsNone(data['case']['place_name'])
        dari = self.call(sos.post_sos, body={'lat': '۳۴٫۵۱۵۴', 'lon': '۶۹٫۱۳۳۸', 'battery': '۶۷'}, device_id='dev-dari')
        self.assertEqual((dari['case']['lat'], dari['case']['lon'], dari['case']['battery']), (34.5154, 69.1338, 67))
        data = self.call(sos.post_sos, body={'lat': 'abc', 'lon': 999, 'battery': 'full'}, device_id='dev-bad')
        self.assertIsNone(data['case']['lat'])
        self.assertIsNone(data['case']['battery'])
        self.assertTrue(sos.wait_idle(5))
        self.assertEqual(self.geo.calls, [(34.5154, 69.1338)])  # only for the SOS with a location

    def test_one_open_case_per_device_and_user(self):
        first = self.sos_guest()
        second = self.sos_guest(kind='custom', text='کمک کنید!', lat=34.52, lon=69.14)
        self.assertEqual(first['case']['id'], second['case']['id'])
        self.assertEqual([m['sender'] for m in second['messages']], ['user', 'auto', 'user', 'auto'])
        self.assertEqual(second['messages'][3]['text_key'], 'sos_auto_wait')
        self.assertEqual(second['case']['lat'], 34.52)
        third = self.sos_guest()
        self.assertEqual(len(third['messages']), 5)  # no second "please wait"
        self.assertFalse(self.live.of('sos_case')[-1]['data']['is_new'])
        # another guest gets a case of their own
        self.assertNotEqual(self.sos_guest(device='dev-guest-2')['case']['id'], first['case']['id'])
        # a resident: one case, even from another device
        a = self.call(sos.post_sos, user=self.user, device_id='phone-a', body={})
        b = self.call(sos.post_sos, user=self.user, device_id='phone-b', body={})
        self.assertEqual(a['case']['id'], b['case']['id'])
        count = self.db.execute("SELECT count(*) FROM sos_cases WHERE status = 'open'").fetchone()[0]
        self.assertEqual(count, 3)

    def test_guest_case_is_attached_after_login(self):
        guest = self.sos_guest(device='shared-phone')
        logged = self.call(sos.post_sos, user=self.user, device_id='shared-phone', body={})
        self.assertEqual(logged['case']['id'], guest['case']['id'])
        self.assertEqual(logged['case']['user_id'], self.user['id'])
        # another resident on the same device does not get someone else's case
        other = self.call(sos.post_sos, user=self.other_user, device_id='shared-phone', body={})
        self.assertNotEqual(other['case']['id'], guest['case']['id'])

    def test_double_tap_from_many_threads_gives_one_case(self):
        errors = []

        def tap():
            db = store.connect(self.path)
            try:
                sos.post_sos(make_req(db, body={'kind': 'default'}, device_id='double-tap'))
            except Exception as exc:  # pragma: no cover — shows up in errors
                errors.append(exc)
            finally:
                db.close()

        threads = [threading.Thread(target=tap) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(errors, [])
        rows = self.db.execute("SELECT id FROM sos_cases WHERE device_id = 'double-tap'").fetchall()
        self.assertEqual(len(rows), 1)
        senders = [r[0] for r in self.db.execute('SELECT sender FROM sos_messages WHERE case_id = ?', (rows[0][0],))]
        self.assertEqual(senders.count('user'), 6)
        self.assertEqual(senders.count('auto'), 2)  # "received" + "please wait" once

    def test_pending_write_does_not_block_others_while_waiting_for_lock(self):
        # the request has already written something (geo cache, session) and waits for the SOS lock; the database must not stay locked meanwhile
        ready, done, errors = threading.Event(), [], []

        def waiting_request():
            db = store.connect(self.path)
            try:
                db.execute("INSERT INTO settings (key, value) VALUES ('pending', '1')")  # an unfinished write
                ready.set()
                done.append(sos.post_sos(make_req(db, body={}, device_id='dev-waiting')))
            except Exception as exc:  # pragma: no cover — shows up in errors
                errors.append(exc)
            finally:
                db.close()

        with sos._lock:  # the lock is held by another SOS
            thread = threading.Thread(target=waiting_request)
            thread.start()
            self.assertTrue(ready.wait(5))
            other = store.connect(self.path)
            try:
                other.execute('PRAGMA busy_timeout = 3000')
                started = time.monotonic()
                other.execute("INSERT INTO settings (key, value) VALUES ('other', '2')")
                other.commit()
                self.assertLess(time.monotonic() - started, 2.5)
            finally:
                other.close()
        thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(done[0][0], 200)

    def test_event_comes_before_place_name(self):
        gate = threading.Event()
        self.geo = fake_geo(name='جاده میدان هوایی، ناحیه دهم، کابل', gate=gate, near='نزدیک: پوهنتون کابل')
        swap(self, geo=self.geo)
        self.addCleanup(gate.set)
        started = time.monotonic()
        data = self.sos_guest(device='dev-slow')
        self.assertLess(time.monotonic() - started, 2)  # Nominatim (here: a closed gate) does not delay the SOS
        case_id = data['case']['id']
        self.assertEqual(data['case']['place_name'], 'نزدیک: پوهنتون کابل')  # the fallback label comes right away
        first = self.live.of('sos_case')
        self.assertEqual(len(first), 1)
        self.assertTrue(first[0]['data']['is_new'])
        self.assertEqual(first[0]['data']['place_name'], 'نزدیک: پوهنتون کابل')
        self.assertEqual(self.geo.calls, [])  # the label has not arrived yet
        gate.set()
        self.assertTrue(sos.wait_idle(5))
        second = self.live.of('sos_case')[1]
        self.assertEqual(second['data']['case']['id'], case_id)
        self.assertEqual((second['data']['is_new'], second['data']['changed']), (False, 'place_name'))
        self.assertEqual(second['data']['place_name'], 'جاده میدان هوایی، ناحیه دهم، کابل')
        self.assertTrue(second['admins'])
        self.assertEqual(second['device_id'], 'dev-slow')  # the owner gets it too: screen 10 shows "نزدیک"
        stored = self.call(sos.admin_get, params={'id': str(case_id)}, admin=self.admin)['case']
        self.assertEqual(stored['place_name'], 'جاده میدان هوایی، ناحیه دهم، کابل')
        self.assertEqual(len(self.live.of('sos_case')), 2)

    def test_stale_place_name_is_dropped(self):
        gate = threading.Event()
        self.geo = fake_geo(name=None, gate=gate)
        swap(self, geo=self.geo)
        self.addCleanup(gate.set)
        case_id = str(self.sos_guest(device='dev-walk', lat=34.50, lon=69.10)['case']['id'])
        for lat in (34.52, 34.54):  # while the labels wait for the network, the resident moved twice
            self.call(sos.post_location, params={'id': case_id}, device_id='dev-walk', body={'lat': lat, 'lon': 69.10})
        gate.set()
        self.assertTrue(sos.wait_idle(5))
        self.assertEqual(len(self.geo.calls), 3)
        names = [e['data']['place_name'] for e in self.live.of('sos_case') if e['data'].get('changed')]
        self.assertEqual(names, ['جای 34.5400,69.1000'])  # stale labels did not arrive
        detail = self.call(sos.admin_get, params={'id': case_id}, admin=self.admin)['case']
        self.assertEqual(detail['place_name'], 'جای 34.5400,69.1000')

    def test_current(self):
        self.assertEqual(self.call(sos.get_current, device_id='nobody'), {'case': None, 'messages': []})
        made = self.sos_guest()
        current = self.call(sos.get_current, device_id='dev-guest-1')
        self.assertEqual(current['case']['id'], made['case']['id'])
        self.assertEqual(len(current['messages']), 2)
        self.assertIsNone(self.call(sos.get_current, device_id='dev-guest-2')['case'])
        self.assertIsNone(self.call(sos.get_current)['case'])

    # --- resident chat
    def test_user_message_gets_wait_reply_once_until_rescuer_answers(self):
        case_id = str(self.sos_guest()['case']['id'])
        first = self.call(sos.post_message, params={'id': case_id}, device_id='dev-guest-1',
                          body={'text': 'کمک کنید!'})
        self.assertEqual(first['message']['sender'], 'user')
        self.assertEqual([m['sender'] for m in first['messages']], ['user', 'auto'])
        self.assertIn('شماره تماس مستقیم: ۱۱۹', first['messages'][1]['text'])
        second = self.call(sos.post_message, params={'id': case_id}, device_id='dev-guest-1', body={'text': 'لطفاً'})
        self.assertEqual(len(second['messages']), 1)
        events = self.live.of('sos_message')
        self.assertTrue(all(e['admins'] for e in events))
        self.assertEqual(self.error(sos.post_message, params={'id': case_id}, device_id='dev-guest-1',
                                    body={'text': '  '}).status, 400)

    def test_no_wait_reply_after_rescuer_answered(self):
        case_id = str(self.sos_guest()['case']['id'])
        self.call(sos.admin_post_message, params={'id': case_id}, admin=self.admin, body={'text': 'در راه هستیم'})
        reply = self.call(sos.post_message, params={'id': case_id}, device_id='dev-guest-1', body={'text': 'ممنون'})
        self.assertEqual([m['sender'] for m in reply['messages']], ['user'])

    def test_messages_after(self):
        case_id = str(self.sos_guest()['case']['id'])
        everything = self.call(sos.get_messages, params={'id': case_id}, device_id='dev-guest-1')['messages']
        last = everything[-1]['id']
        self.assertEqual(self.call(sos.get_messages, params={'id': case_id}, device_id='dev-guest-1',
                                   query={'after': str(last)})['messages'], [])
        self.call(sos.post_message, params={'id': case_id}, device_id='dev-guest-1', body={'text': 'سلام'})
        fresh = self.call(sos.get_messages, params={'id': case_id}, device_id='dev-guest-1',
                          query={'after': str(last)})
        self.assertEqual([m['text'] for m in fresh['messages']][0], 'سلام')
        self.assertEqual(fresh['case']['status'], 'open')
        self.assertEqual(self.error(sos.get_messages, params={'id': case_id}, device_id='dev-guest-1',
                                    query={'after': 'x'}).status, 400)

    def test_foreign_access(self):
        guest_case = str(self.sos_guest()['case']['id'])
        user_case = str(self.call(sos.post_sos, user=self.user, device_id='phone-a', body={})['case']['id'])
        for handler, body in ((sos.get_messages, None), (sos.post_message, {'text': 'x'}),
                              (sos.post_location, {'lat': 34.5, 'lon': 69.1})):
            self.assertEqual(self.error(handler, params={'id': guest_case}, device_id='dev-guest-2',
                                        body=body).status, 403)
            self.assertEqual(self.error(handler, params={'id': user_case}, user=self.other_user,
                                        body=body).status, 403)
            self.assertEqual(self.error(handler, params={'id': guest_case}, body=body).status, 403)
            self.assertEqual(self.error(handler, params={'id': '999'}, device_id='dev-guest-1',
                                        body=body).status, 404)
            self.assertEqual(self.error(handler, params={'id': 'abc'}, device_id='dev-guest-1',
                                        body=body).status, 404)
        # the resident sees their own case from any device; the admin reads it but does not write on the resident's behalf
        self.call(sos.get_messages, params={'id': user_case}, user=self.user, device_id='phone-b')
        self.call(sos.get_messages, params={'id': guest_case}, admin=self.admin)
        self.assertEqual(self.error(sos.post_message, params={'id': guest_case}, admin=self.admin,
                                    body={'text': 'x'}).status, 403)

    # --- location
    def test_location_updates_case_and_track(self):
        case_id = str(self.sos_guest()['case']['id'])
        self.assertTrue(sos.wait_idle(5))
        calls_before = len(self.geo.calls)
        near = self.call(sos.post_location, params={'id': case_id}, device_id='dev-guest-1',
                         body={'lat': 34.5160, 'lon': 69.1340, 'accuracy': 8, 'battery': 60, 'signal': 3})
        self.assertTrue(sos.wait_idle(5))
        self.assertEqual(len(self.geo.calls), calls_before)  # a ~70 m move: the place is not looked up again
        self.assertEqual(near['case']['place_name'], 'پوهنتون کابل، ناحیه سوم')
        self.assertEqual((near['case']['lat'], near['case']['battery'], near['case']['signal']), (34.516, 60, '3'))
        self.assertEqual(near['point']['accuracy_m'], 8)
        far = self.call(sos.post_location, params={'id': case_id}, device_id='dev-guest-1',
                        body={'lat': 34.5331, 'lon': 69.1684})
        self.assertIsNone(far['case']['place_name'])  # moved 3.7 km: the old label is wrong, the new one comes in the background
        self.assertTrue(sos.wait_idle(5))
        self.assertEqual(len(self.geo.calls), calls_before + 1)
        self.assertEqual(far['case']['battery'], 60)  # no battery given: the previous value
        detail = self.call(sos.admin_get, params={'id': case_id}, admin=self.admin)
        self.assertEqual([p['lat'] for p in detail['track']], [KABUL_UNI[0], 34.516, 34.5331])
        events = self.live.of('sos_location')
        self.assertEqual(len(events), 3)
        self.assertTrue(events[-1]['admins'])
        self.assertEqual(events[-1]['data']['case_id'], int(case_id))
        for bad in ({'lat': 91, 'lon': 69}, {'lat': 34.5}, {}, {'lat': 'x', 'lon': 'y'}, {'lat': True, 'lon': 69}):
            self.assertEqual(self.error(sos.post_location, params={'id': case_id}, device_id='dev-guest-1',
                                        body=bad).status, 400, bad)

    # --- admin
    def test_admin_reply_goes_to_owner(self):
        user_case = self.call(sos.post_sos, user=self.user, device_id='phone-a', body={})['case']['id']
        guest_case = self.sos_guest()['case']['id']
        self.live.events.clear()
        reply = self.call(sos.admin_post_message, params={'id': str(user_case)}, admin=self.admin,
                          body={'text': 'تیم نجات در راه است.'})
        self.assertEqual((reply['message']['sender'], reply['message']['admin_id']), ('rescuer', 7))
        event = self.live.of('sos_message')[-1]
        self.assertEqual((event['user_id'], event['device_id']), (self.user['id'], None))
        self.assertEqual(event['data']['message']['text'], 'تیم نجات در راه است.')
        self.call(sos.admin_post_message, params={'id': str(guest_case)}, admin=self.admin, body={'text': 'سلام'})
        event = self.live.of('sos_message')[-1]
        self.assertEqual((event['user_id'], event['device_id']), (None, 'dev-guest-1'))
        # the resident sees the reply in their chat
        chat = self.call(sos.get_messages, params={'id': str(guest_case)}, device_id='dev-guest-1')['messages']
        self.assertEqual(chat[-1]['sender'], 'rescuer')

    def test_admin_list_and_detail(self):
        guest = self.sos_guest()['case']['id']
        user_case = self.call(sos.post_sos, user=self.user, device_id='phone-a',
                              body={'kind': 'custom', 'text': 'من در گیر مانده‌ام اما زخمی نیستم.'})['case']['id']
        self.call(sos.post_message, params={'id': str(user_case)}, user=self.user, body={'text': 'آخرین پیام'})
        self.assertTrue(sos.wait_idle(5))
        cases = self.call(sos.admin_list, admin=self.admin)['cases']
        self.assertEqual([c['case']['id'] for c in cases], [user_case, guest])  # newest on top
        top = cases[0]
        self.assertEqual(top['kind'], 'custom')
        self.assertEqual(top['user']['phone'], '+93777123454')
        self.assertEqual(top['user']['blood_group'], 'O+')
        self.assertEqual(top['last_message']['text'], 'آخرین پیام')  # the resident's last message, not the auto-reply
        self.assertIsNone(cases[1]['user'])
        self.assertEqual(cases[1]['place_name'], 'پوهنتون کابل، ناحیه سوم')
        self.call(sos.admin_close, params={'id': str(guest)}, admin=self.admin)
        self.assertEqual([c['case']['id'] for c in self.call(sos.admin_list, admin=self.admin,
                                                            query={'status': 'closed'})['cases']], [guest])
        self.assertEqual(len(self.call(sos.admin_list, admin=self.admin, query={'status': 'all'})['cases']), 2)
        self.assertEqual(len(self.call(sos.admin_list, admin=self.admin, query={'status': 'open'})['cases']), 1)
        self.assertEqual(self.error(sos.admin_list, admin=self.admin, query={'status': 'old'}).status, 400)
        detail = self.call(sos.admin_get, params={'id': str(user_case)}, admin=self.admin)
        self.assertEqual(set(detail), {'case', 'user', 'messages', 'track'})
        self.assertEqual(detail['user']['name'], 'احمد یوسفی')
        self.assertEqual(detail['track'], [])
        self.assertEqual(self.error(sos.admin_get, params={'id': '999'}, admin=self.admin).status, 404)

    def test_admin_routes_need_admin(self):
        case_id = str(self.sos_guest()['case']['id'])
        for handler, kw in ((sos.admin_list, {}), (sos.admin_get, {'params': {'id': case_id}}),
                            (sos.admin_post_message, {'params': {'id': case_id}, 'body': {'text': 'x'}}),
                            (sos.admin_close, {'params': {'id': case_id}})):
            self.assertEqual(self.error(handler, device_id='dev-guest-1', **kw).status, 403)
            self.assertEqual(self.error(handler, user=self.user, **kw).status, 403)
        self.assertEqual(self.call(sos.get_current, device_id='dev-guest-1')['case']['status'], 'open')

    def test_close(self):
        case_id = str(self.call(sos.post_sos, user=self.user, device_id='phone-a', body={})['case']['id'])
        self.live.events.clear()
        closed = self.call(sos.admin_close, params={'id': case_id}, admin=self.admin)['case']
        self.assertEqual(closed['status'], 'closed')
        self.assertTrue(closed['closed_at'])
        event = self.live.of('sos_case')[-1]
        self.assertTrue(event['admins'])
        self.assertEqual(event['user_id'], self.user['id'])
        self.assertEqual(event['data']['case']['status'], 'closed')
        again = self.call(sos.admin_close, params={'id': case_id}, admin=self.admin)['case']
        self.assertEqual(again['closed_at'], closed['closed_at'])  # closing again changes nothing
        for handler, body in ((sos.post_message, {'text': 'x'}), (sos.post_location, {'lat': 34.5, 'lon': 69.1})):
            self.assertEqual(self.error(handler, params={'id': case_id}, user=self.user, body=body).status, 409)
        self.assertEqual(self.error(sos.admin_post_message, params={'id': case_id}, admin=self.admin,
                                    body={'text': 'x'}).status, 409)
        self.assertIsNone(self.call(sos.get_current, user=self.user)['case'])
        history = self.call(sos.get_messages, params={'id': case_id}, user=self.user)
        self.assertEqual(history['case']['status'], 'closed')  # the resident sees the case is closed
        fresh = self.call(sos.post_sos, user=self.user, body={})
        self.assertNotEqual(str(fresh['case']['id']), case_id)

    def test_stats(self):
        self.assertEqual(sos.stats(self.db), {'open': 0, 'today': 0})
        self.sos_guest()
        self.sos_guest(device='dev-guest-2')
        closed = self.call(sos.post_sos, user=self.user, body={})['case']['id']
        self.call(sos.admin_close, params={'id': str(closed)}, admin=self.admin)
        old_time = (datetime.datetime.strptime(sos.kabul_day_start_utc(), '%Y-%m-%dT%H:%M:%SZ')
                    - datetime.timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        self.db.execute("INSERT INTO sos_cases (device_id, kind, status, created_at, updated_at)"
                        " VALUES ('old', 'default', 'open', ?, ?)", (old_time, old_time))
        self.assertEqual(sos.stats(self.db), {'open': 3, 'today': 3})
        empty = store.connect(':memory:')
        self.addCleanup(empty.close)
        self.assertEqual(sos.stats(empty), {'open': 0, 'today': 0})  # sos.init not called

    def test_kabul_day(self):
        utc = datetime.timezone.utc
        self.assertEqual(sos.kabul_day_start_utc(datetime.datetime(2026, 11, 6, 20, 0, tzinfo=utc)),
                         '2026-11-06T19:30:00Z')
        self.assertEqual(sos.kabul_day_start_utc(datetime.datetime(2026, 11, 6, 19, 0, tzinfo=utc)),
                         '2026-11-05T19:30:00Z')

    def test_works_without_geo_and_live(self):
        swap(self, geo=None, live=None)
        data = self.sos_guest(device='dev-alone')
        self.assertIsNone(data['case']['place_name'])
        self.assertEqual(len(data['messages']), 2)
        case_id = str(data['case']['id'])
        moved = self.call(sos.post_location, params={'id': case_id}, device_id='dev-alone',
                          body={'lat': 34.53, 'lon': 69.17})
        self.assertEqual(moved['case']['lat'], 34.53)

    def test_routes(self):
        found = {(method, path) for method, path, _ in sos.routes()}
        for item in (('POST', '/api/sos'), ('GET', '/api/sos/current'), ('GET', '/api/sos/{id}/messages'),
                     ('POST', '/api/sos/{id}/messages'), ('POST', '/api/sos/{id}/location'),
                     ('GET', '/api/admin/sos'), ('GET', '/api/admin/sos/{id}'),
                     ('POST', '/api/admin/sos/{id}/messages'), ('POST', '/api/admin/sos/{id}/close')):
            self.assertIn(item, found)


class SosHttpTest(unittest.TestCase):
    """Through the real web: a guest sends an SOS, the admin gets the event and replies, the guest gets the reply in the stream."""

    def test_sos_over_http(self):
        swap(self, geo=fake_geo(), auth=None, alerts=None)
        with support.Running(engines=[('auth', support.fake_auth()), 'sos', 'content']) as srv:
            guest, admin, stranger, citizen = srv.client(), srv.client(), srv.client(), srv.client()
            self.assertEqual(admin.post('/api/test/login', {'token': 'tok-admin'}).status, 200)
            self.assertEqual(citizen.post('/api/test/login', {'token': 'tok-user-1'}).status, 200)
            admin_stream = admin.stream()
            try:
                srv.wait_listeners(1)
                sent = guest.post('/api/sos', {'kind': 'default', 'lat': 34.5154, 'lon': 69.1338, 'battery': 67})
                self.assertEqual(sent.status, 200, sent.text)
                case = sent.json()['case']
                first = admin_stream.events_until('sos_case')[-1][1]
                self.assertEqual((first['case']['id'], first['is_new']), (case['id'], True))
                named = admin_stream.events_until('sos_case')[-1][1]  # the place label comes as the second event
                self.assertEqual((named['case']['id'], named['changed']), (case['id'], 'place_name'))
                self.assertEqual(named['place_name'], 'پوهنتون کابل، ناحیه سوم')
                guest_stream = guest.stream()
                try:
                    srv.wait_listeners(2)
                    answer = admin.post('/api/admin/sos/%d/messages' % case['id'], {'text': 'تیم نجات در راه است.'})
                    self.assertEqual(answer.status, 200, answer.text)
                    got = guest_stream.events_until('sos_message')[-1][1]
                    self.assertEqual((got['case_id'], got['message']['sender']), (case['id'], 'rescuer'))
                finally:
                    guest_stream.close()
            finally:
                admin_stream.close()
            current = guest.get('/api/sos/current').json()
            self.assertEqual(current['case']['id'], case['id'])
            self.assertEqual(current['messages'][-1]['text'], 'تیم نجات در راه است.')
            self.assertEqual(stranger.get('/api/sos/%d/messages' % case['id']).status, 403)
            self.assertEqual(guest.get('/api/admin/sos').status, 403)
            self.assertEqual(citizen.get('/api/admin/sos').status, 403)
            listed = admin.get('/api/admin/sos?status=open').json()['cases']
            self.assertEqual([c['case']['id'] for c in listed], [case['id']])
            moved = guest.post('/api/sos/%d/location' % case['id'], {'lat': 34.52, 'lon': 69.14})
            self.assertEqual(moved.status, 200, moved.text)
            self.assertEqual(admin.post('/api/admin/sos/%d/close' % case['id']).status, 200)
            self.assertEqual(guest.post('/api/sos/%d/messages' % case['id'], {'text': 'x'}).status, 409)
            numbers = guest.get('/api/numbers').json()['numbers']
            self.assertIn('119', [n['number'] for n in numbers])
            found = guest.get('/api/search?q=' + urllib.parse.quote('پولیس')).json()
            self.assertEqual(found['numbers'][0]['number'], '119')
            places = guest.get('/api/places?lat=34.5154&lon=69.1338&limit=3').json()['places']
            self.assertEqual(len(places), 3)
            self.assertLessEqual(places[0]['distance_km'], places[1]['distance_km'])
            self.assertTrue(sos.wait_idle(5))  # the background label after the move, while the server database is still there


if __name__ == '__main__':
    unittest.main()
