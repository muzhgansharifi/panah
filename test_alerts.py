"""Tests for alerts: radius boundary 1.99 / 2.00 / 2.01 km, notifications without repeats, SMS, live, end, stats, search."""
import datetime
import json
import threading
import time
import types
import unittest
from unittest import mock

from alerts_support import (KABUL, FakeFetch, TempDB, destination, fake_content, fake_live, fake_sms, fake_sos,
                            load_json, neighbours, req)

from server import alerts, geo, store
from server.common import ApiError, parse_utc, to_utc, utcnow

try:                                   # the real sms engine, to check "each SMS once" in sms_outbox
    from server import sms as REAL_SMS
except ImportError:
    REAL_SMS = None

FLOOD = {'type': 'flood', 'place': 'کارته چهار، کابل', 'lat': KABUL[0], 'lon': KABUL[1],
         'safe_place': 'پارک شهر نو', 'safe_lat': 34.5340, 'safe_lon': 69.1680}
ADMIN = {'id': 7, 'username': 'admin'}


def iso_ago(hours):
    return to_utc(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours))


class AlertsCase(unittest.TestCase):
    """Temporary database, fake sms / live / content / sos; auth is "missing" (the fallback login check is used)."""

    places = ()

    def setUp(self):
        geo.reset_network_state()
        self.addCleanup(geo.reset_network_state)
        self.db = TempDB(geo, alerts)
        self.addCleanup(self.db.close)
        self.conn = self.db.conn
        self.sms, self.live, self.sos = fake_sms(), fake_live(), fake_sos()
        self.content = fake_content(list(self.places))
        patcher = neighbours(sms=self.sms, live=self.live, content=self.content, sos=self.sos, auth=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        offline = mock.patch.object(geo, 'fetch_json', FakeFetch(OSError('tests run offline')))
        offline.start()
        self.addCleanup(offline.stop)
        self.addCleanup(self.drain_sms)    # background SMS: before the fake engines and the database are removed

    def drain_sms(self):
        self.conn.commit()                 # like web at the end of a request
        alerts.wait_sms(self.conn, 5)

    def user(self, uid):
        return self.db.user(uid)

    def create(self, **changes):
        data = dict(FLOOD)
        data.update(changes)
        return alerts.create(self.conn, data, 'admin', admin_id=ADMIN['id'])


class RadiusTest(AlertsCase):
    def test_boundary_1_99_2_00_2_01_km_in_every_direction(self):
        inside, outside = set(), set()
        for bearing in (0, 45, 90, 135, 180, 225, 270, 315):
            for km in (1.99, 2.00, 2.01):
                uid = self.db.add_user(last=destination(*KABUL, km, bearing))
                (inside if km <= 2.0 else outside).add(uid)
        result = self.create()
        got = {row['user_id'] for row in self.conn.execute('SELECT user_id FROM notifications')}
        self.assertEqual(got, inside)
        self.assertEqual(result['notified'], 16)
        self.assertEqual(result['sms'], 16)
        self.assertFalse(got & outside)
        for row in self.conn.execute('SELECT distance_km FROM notifications'):
            self.assertLessEqual(row['distance_km'], 2.0)

    def test_last_location_first_then_home(self):
        home_only = self.db.add_user(home=destination(*KABUL, 1.0, 10))
        moved_away = self.db.add_user(last=destination(*KABUL, 3.0, 10), home=destination(*KABUL, 0.5, 10))
        came_in = self.db.add_user(last=destination(*KABUL, 0.3, 10), home=destination(*KABUL, 9.0, 10))
        self.db.add_user()                                          # no location at all
        self.create()
        got = {row['user_id'] for row in self.conn.execute('SELECT user_id FROM notifications')}
        self.assertEqual(got, {home_only, came_in})
        self.assertNotIn(moved_away, got)

    def test_custom_radius(self):
        near = self.db.add_user(last=destination(*KABUL, 4.9, 60))
        self.db.add_user(last=destination(*KABUL, 5.1, 60))
        result = self.create(radius_km='5')
        self.assertEqual(result['alert']['radius_km'], 5.0)
        self.assertEqual([row['user_id'] for row in self.conn.execute('SELECT user_id FROM notifications')], [near])

    def test_residents_within_sorted_nearest_first(self):
        far = self.db.add_user(last=destination(*KABUL, 1.5, 0))
        near = self.db.add_user(last=destination(*KABUL, 0.2, 180))
        found = alerts.residents_within(self.conn, KABUL[0], KABUL[1], 2.0)
        self.assertEqual([user['id'] for user, _ in found], [near, far])
        self.assertAlmostEqual(found[0][1], 0.2, places=6)


class CreateTest(AlertsCase):
    def test_result_sms_and_live_event(self):
        uid = self.db.add_user(last=destination(*KABUL, 0.5, 90), phone='+93700111222')
        result = self.create(starts_at='2026-11-06T10:00:00+04:30')
        alert = result['alert']
        self.assertEqual(set(result), {'alert', 'notified', 'sms', 'ms'})
        self.assertEqual((result['notified'], result['sms']), (1, 1))
        self.assertIsInstance(result['ms'], float)
        self.assertGreaterEqual(result['ms'], 0)
        self.assertEqual((alert['type'], alert['source'], alert['status'], alert['radius_km']),
                         ('flood', 'admin', 'active', 2.0))
        self.assertEqual(alert['starts_at'], '2026-11-06T05:30:00Z')
        self.assertEqual(alert['title_fa'], 'هشدار سیلاب')
        self.assertNotIn('created_by', alert)
        self.assertEqual(self.conn.execute('SELECT created_by FROM alerts').fetchone()[0], ADMIN['id'])
        # SMS: in the background; Dari and Pashto: type, place, where to go
        self.assertTrue(alerts.wait_sms(self.conn))
        self.assertEqual(len(self.sms.sent), 1)
        sms = self.sms.sent[0]
        self.assertEqual((sms['phone'], sms['kind']), ('+93700111222', 'alert'))
        dari, pashto = sms['text'].split('\n')
        self.assertTrue(dari.startswith('پناه — ') and pashto.startswith('پناه — '), sms['text'])   # PANAH
        for part in ('هشدار سیلاب', 'کارته چهار، کابل', 'به محل امن بروید', 'پارک شهر نو'):
            self.assertIn(part, dari)
        for part in ('د سېلاب خبرتیا', 'کارته چهار، کابل', 'خوندي ځای ته لاړ شئ', 'پارک شهر نو'):
            self.assertIn(part, pashto)
        row = self.conn.execute('SELECT * FROM notifications').fetchone()
        self.assertEqual((row['user_id'], row['alert_id'], row['sms_id'], row['sms_status']),
                         (uid, alert['id'], 1001, 'sent'))
        self.assertTrue(row['sms_sent_at'])
        self.assertAlmostEqual(row['distance_km'], 0.5, places=3)
        # the live event: to everyone, public view, fits into JSON
        self.assertEqual(len(self.live.events), 1)
        event = self.live.events[0]
        self.assertEqual((event['event'], event['everyone']), ('alert', True))
        self.assertEqual(event['data']['id'], alert['id'])
        self.assertNotIn('created_by', event['data'])
        self.assertTrue(EVENT_FIELDS <= set(event['data']), EVENT_FIELDS - set(event['data']))
        self.assertEqual((event['data']['lat'], event['data']['radius_km'], event['data']['safe_place']),
                         (KABUL[0], 2.0, 'پارک شهر نو'))
        json.dumps(result, ensure_ascii=False)

    def test_alert_is_in_database_before_events(self):
        """The live event goes out after commit: another connection already sees the alert when it is sent."""
        other = store.connect(self.db.path)
        self.addCleanup(other.close)
        seen = []

        def publish(event, data, **target):
            row = other.execute('SELECT status FROM alerts WHERE id = ?', (data['id'],)).fetchone()
            seen.append((event, row['status'] if row else None))

        self.live.publish = publish
        alert = self.create()['alert']
        alerts.end(self.conn, alert['id'])
        self.assertEqual(seen, [('alert', 'active'), ('alert_end', 'ended')])

    def test_earthquake_sms_without_safe_place_gives_advice(self):
        self.db.add_user(last=KABUL)
        alerts.create(self.conn, {'type': 'earthquake', 'lat': KABUL[0], 'lon': KABUL[1], 'magnitude': 4.6,
                                  'place': '41 km S of Farkhār, Afghanistan'}, 'usgs')
        self.assertTrue(alerts.wait_sms(self.conn))
        dari, pashto = self.sms.sent[0]['text'].split('\n')
        self.assertIn('هشدار زلزله M4.6', dari)
        self.assertIn('41 km S of Farkhār, Afghanistan', dari)
        self.assertIn('از ساختمان‌ها دور شوید', dari)
        self.assertIn('د زلزلې خبرتیا M4.6', pashto)

    def test_no_repeat_notifications(self):
        uid = self.db.add_user(last=KABUL)
        result = self.create()
        row = alerts._row(self.conn, result['alert']['id'])
        again = alerts._distribute(self.conn, row)                # the same alert again
        self.assertEqual(again, (0, 0))
        self.conn.commit()
        self.assertTrue(alerts.wait_sms(self.conn))
        self.assertEqual(len(self.sms.sent), 1)
        count = 'SELECT count(*) FROM notifications WHERE user_id = ?'
        self.assertEqual(self.conn.execute(count, (uid,)).fetchone()[0], 1)
        self.create(type='earthquake')                            # another alert: a new notification
        self.assertEqual(self.conn.execute(count, (uid,)).fetchone()[0], 2)

    def test_sms_failure_is_retried_then_marked_failed(self):
        self.sms = fake_sms(fail_for=('+93700000001',))
        with neighbours(sms=self.sms), mock.patch.object(alerts, 'SMS_RETRY_S', (0, 0)), \
                self.assertLogs('server.alerts', 'WARNING') as logs:
            first = self.db.add_user(last=KABUL)
            ok = self.db.add_user(last=destination(*KABUL, 0.1, 0))
            result = self.create()
            self.assertTrue(alerts.wait_sms(self.conn))
        self.assertEqual((result['notified'], result['sms']), (2, 2))          # both queued
        rows = {row['user_id']: row for row in self.conn.execute('SELECT * FROM notifications')}
        self.assertEqual((rows[ok]['sms_status'], rows[ok]['sms_attempts']), ('sent', 0))
        self.assertIsNotNone(rows[ok]['sms_id'])
        self.assertEqual((rows[first]['sms_status'], rows[first]['sms_attempts'], rows[first]['sms_id']),
                         ('failed', 3, None))                                  # 1 attempt + 2 retries
        self.assertIn('provider down', rows[first]['sms_error'])
        self.assertEqual([m['phone'] for m in self.sms.sent], ['+93700000002'])
        self.assertTrue(any('marked failed' in line for line in logs.output))

    def test_without_sms_and_live_engines(self):
        self.db.add_user(last=KABUL)
        with neighbours(sms=None, live=None):
            result = self.create()
        self.assertEqual((result['notified'], result['sms']), (1, 0))
        self.assertEqual(self.conn.execute('SELECT sms_status FROM notifications').fetchone()[0], 'none')

    def test_place_from_geo_when_empty(self):
        with mock.patch.object(geo, 'fetch_json', FakeFetch(load_json('nominatim_reverse_kabul.json'))):
            result = self.create(place='', lat=34.5550, lon=69.2070, safe_place='')
        self.assertEqual(result['alert']['place'], 'جاده میدان هوایی، ناحیه دهم، کابل ښار')
        self.assertTrue(result['alert']['safe_place'])     # from the safe_lat/lon point

    def test_place_offline_falls_back_to_coordinates(self):
        with neighbours(content=None):
            result = self.create(place='', lat=34.5550, lon=69.2070)
        self.assertEqual(result['alert']['place'], '34.5550, 69.2070')

    def test_type_words_and_defaults(self):
        result = self.create(type='سیلاب', starts_at='', radius_km='')
        self.assertEqual(result['alert']['type'], 'flood')
        self.assertEqual(result['alert']['radius_km'], 2.0)
        moment = parse_utc(result['alert']['starts_at'])
        self.assertLess(abs((datetime.datetime.now(datetime.timezone.utc) - moment).total_seconds()), 5)

    def test_validation(self):
        bad = [
            ({'type': 'fire'}, 'bad_type'),
            ({'type': None}, 'bad_type'),
            ({'lat': None, 'lon': None}, 'location_required'),
            ({'lat': 'abc'}, 'bad_location'),
            ({'lat': 95}, 'bad_location'),
            ({'radius_km': 0}, 'bad_radius'),
            ({'radius_km': 'abc'}, 'bad_radius'),
            ({'radius_km': 5000}, 'bad_radius'),
            ({'starts_at': 'tomorrow'}, 'bad_time'),
            ({'safe_place': '', 'safe_lat': None, 'safe_lon': None}, 'safe_place_required'),
            ({'magnitude': 'big'}, 'bad_number'),
        ]
        for changes, code in bad:
            with self.assertRaises(ApiError, msg=changes) as caught:
                self.create(**changes)
            self.assertEqual((caught.exception.status, caught.exception.code), (400, code), changes)
            self.assertTrue(caught.exception.message_fa)
        with self.assertRaises(ApiError) as caught:
            alerts.create(self.conn, 'not a dict', 'admin')
        self.assertEqual(caught.exception.status, 400)
        with self.assertRaises(ApiError):
            alerts.create(self.conn, dict(FLOOD), 'twitter')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM alerts').fetchone()[0], 0)

    def test_duplicate_source_id(self):
        data = {'type': 'earthquake', 'lat': 36.4, 'lon': 70.7, 'source_id': 'us7000tw2x', 'magnitude': 4.2}
        alerts.create(self.conn, data, 'usgs')
        with self.assertRaises(ApiError) as caught:
            alerts.create(self.conn, data, 'usgs')
        self.assertEqual(caught.exception.status, 409)
        with self.assertRaises(ApiError):
            alerts.add_past(self.conn, data, 'usgs')

    def test_usgs_needs_no_safe_place_and_gets_url(self):
        result = alerts.create(self.conn, {'type': 'earthquake', 'lat': 36.4, 'lon': 70.7, 'source_id': 'us7000tw2x',
                                           'place': '47 km SSW of Jurm, Afghanistan'}, 'usgs')
        self.assertEqual(result['alert']['url'], 'https://earthquake.usgs.gov/earthquakes/eventpage/us7000tw2x')
        self.assertIsNone(self.create()['alert']['url'])

    def test_add_past_has_no_notifications(self):
        self.db.add_user(last=KABUL)
        past = alerts.add_past(self.conn, {'type': 'earthquake', 'lat': KABUL[0], 'lon': KABUL[1]}, 'usgs')
        self.assertEqual(past['status'], 'ended')
        self.assertIsNotNone(past['ended_at'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM notifications').fetchone()[0], 0)
        self.assertEqual((self.sms.sent, self.live.events), ([], []))

    def test_init_twice(self):
        alerts.init(self.conn)
        alerts.init(self.conn)
        self.create()
        alerts.init(self.conn)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM alerts').fetchone()[0], 1)


class ListTest(AlertsCase):
    places = [{'name': 'پارک شهر نو', 'lat': 34.5340, 'lon': 69.1680},
              {'name': 'ستدیوم غازی', 'lat': 34.5300, 'lon': 69.1800},
              {'name': 'باغ بابر', 'lat': 34.5030, 'lon': 69.1580},
              {'name': 'دشت برچی', 'lat': 34.4900, 'lon': 69.0800}]

    def setUp(self):
        super().setUp()
        self.near = self.create(lat=destination(*KABUL, 1.0, 0)[0], lon=KABUL[1], starts_at=iso_ago(3))['alert']
        far = destination(*KABUL, 300, 45)
        self.far = alerts.create(self.conn, {'type': 'earthquake', 'lat': far[0], 'lon': far[1], 'magnitude': 4.4,
                                             'starts_at': iso_ago(1)}, 'usgs')['alert']
        self.mid = self.create(lat=destination(*KABUL, 12, 270)[0], lon=destination(*KABUL, 12, 270)[1],
                               starts_at=iso_ago(2))['alert']
        self.old = alerts.add_past(self.conn, {'type': 'earthquake', 'lat': 36.2, 'lon': 69.9,
                                               'starts_at': iso_ago(240)}, 'usgs')
        self.ended = self.create(starts_at=iso_ago(30))['alert']
        alerts.end(self.conn, self.ended['id'])

    def test_with_point_nearest_first(self):
        code, body = alerts.handle_list(req(self.conn, query={'lat': str(KABUL[0]), 'lon': str(KABUL[1])}))
        self.assertEqual(code, 200)
        self.assertEqual([a['id'] for a in body['active']], [self.near['id'], self.mid['id'], self.far['id']])
        first = body['active'][0]
        self.assertAlmostEqual(first['distance_km'], 1.0, places=3)
        self.assertTrue(first['inside'])
        self.assertFalse(body['active'][1]['inside'])
        self.assertAlmostEqual(body['active'][1]['distance_km'], 12.0, places=2)
        self.assertEqual({a['id'] for a in body['past']}, {self.old['id'], self.ended['id']})
        self.assertEqual(body['past'][0]['id'], self.ended['id'])         # closer
        self.assertEqual(body['point'], {'lat': KABUL[0], 'lon': KABUL[1]})
        json.dumps(body, ensure_ascii=False)

    def test_sort_by_time(self):
        _, body = alerts.handle_list(req(self.conn, query={'lat': str(KABUL[0]), 'lon': str(KABUL[1]), 'sort': 'time'}))
        self.assertEqual([a['id'] for a in body['active']], [self.far['id'], self.mid['id'], self.near['id']])

    def test_place_of_logged_in_resident(self):
        uid = self.db.add_user(last=destination(*KABUL, 12, 270), home=KABUL)
        _, body = alerts.handle_list(req(self.conn, user=self.user(uid)))
        self.assertEqual(body['active'][0]['id'], self.mid['id'])           # from last, the 12 km alert is closer
        home_only = self.db.add_user(home=KABUL)
        _, body = alerts.handle_list(req(self.conn, user={'id': home_only}))  # req.user may have no locations
        self.assertEqual(body['active'][0]['id'], self.near['id'])

    def test_guest_without_point(self):
        _, body = alerts.handle_list(req(self.conn))
        self.assertIsNone(body['point'])
        self.assertTrue(all(a['distance_km'] is None and a['inside'] is False for a in body['active']))
        self.assertEqual(len(body['active']), 3)

    def test_past_limited_to_50(self):
        for i in range(55):
            alerts.add_past(self.conn, {'type': 'flood', 'lat': 34.5, 'lon': 69.2, 'starts_at': iso_ago(100 + i)},
                            'usgs')
        _, body = alerts.handle_list(req(self.conn, query={'lat': '34.5', 'lon': '69.2'}))
        self.assertEqual(len(body['past']), 50)

    def test_bad_point(self):
        with self.assertRaises(ApiError) as caught:
            alerts.handle_list(req(self.conn, query={'lat': 'x', 'lon': '69'}))
        self.assertEqual(caught.exception.status, 400)

    def test_one_alert_with_safe_places(self):
        point = {'lat': str(KABUL[0]), 'lon': str(KABUL[1])}
        code, body = alerts.handle_get(req(self.conn, query=point, params={'id': self.near['id']}))
        self.assertEqual(code, 200)
        self.assertEqual(body['alert']['id'], self.near['id'])
        self.assertAlmostEqual(body['alert']['distance_km'], 1.0, places=3)
        self.assertTrue(body['alert']['inside'])
        self.assertEqual(len(body['safe_places']), 3)
        self.assertEqual(self.content.calls[-1], (KABUL[0], KABUL[1], alerts.SAFE_CANDIDATES))   # from the resident
        dists = [p['distance_km'] for p in body['safe_places']]
        self.assertEqual(dists, sorted(dists))

    def test_safe_places_skip_the_danger_zone(self):
        """Places within radius_km of the alert centre are never offered; the admin's own safe place stays."""
        spots = {'inside': destination(*KABUL, 0.5, 90),     # inside the 2 km circle
                 'edge': destination(*KABUL, 2.0, 180),      # exactly on the boundary: still inside
                 'outside': destination(*KABUL, 2.5, 270),
                 'far': destination(*KABUL, 9.0, 0)}
        content = fake_content([{'name': name, 'lat': lat, 'lon': lon} for name, (lat, lon) in spots.items()])
        admin_spot = destination(*KABUL, 0.3, 45)            # admin chose a point inside the circle: kept as is
        with neighbours(content=content):
            alert = self.create(safe_place='مسجد', safe_lat=admin_spot[0], safe_lon=admin_spot[1])['alert']
            resident = destination(*KABUL, 0.4, 90)          # the nearest place to him is 'inside'
            _, body = alerts.handle_get(req(self.conn, query={'lat': str(resident[0]), 'lon': str(resident[1])},
                                            params={'id': alert['id']}))
            self.assertEqual([place['name'] for place in body['safe_places']], ['outside', 'far'])
            self.assertEqual(content.calls[-1][:2], resident)                # sorted from the resident
            _, guest = alerts.handle_get(req(self.conn, params={'id': alert['id']}))
            self.assertEqual([place['name'] for place in guest['safe_places']], ['outside', 'far'])
            self.assertEqual(content.calls[-1][:2], (KABUL[0], KABUL[1]))    # no resident: from the alert centre
        self.assertEqual((body['alert']['safe_place'], body['alert']['safe_lat'], body['alert']['safe_lon']),
                         ('مسجد', admin_spot[0], admin_spot[1]))

    def test_safe_places_not_farther_than_30_km(self):
        """Herat has two places; Kandahar (≈ 440 km) must not top the list up to three."""
        # coordinates as in the content seed (app/server/content.py, SEED_PLACES_CITIES)
        content = fake_content([{'name': 'پوهنتون هرات', 'lat': 34.3675322, 'lon': 62.2111314},
                                {'name': 'ورزشگاه هرات', 'lat': 34.3473974, 'lon': 62.2176845},
                                {'name': 'پوهنتون قندهار', 'lat': 31.6374143, 'lon': 65.6971092}])
        herat_airport = (34.2100, 62.2283)                    # 15–18 km from both Herat places, outside 2 km
        with neighbours(content=content):
            alert = alerts.create(self.conn, {'type': 'earthquake', 'lat': herat_airport[0], 'lon': herat_airport[1],
                                              'place': 'Herat'}, 'usgs')['alert']
            _, guest = alerts.handle_get(req(self.conn, params={'id': alert['id']}))
            self.assertEqual([place['name'] for place in guest['safe_places']], ['ورزشگاه هرات', 'پوهنتون هرات'])
            self.assertTrue(all(place['distance_km'] <= alerts.SAFE_MAX_KM for place in guest['safe_places']))
            kandahar = {'lat': '31.61', 'lon': '65.70'}           # a resident in Kandahar: only Kandahar is near
            _, there = alerts.handle_get(req(self.conn, query=kandahar, params={'id': alert['id']}))
            self.assertEqual([place['name'] for place in there['safe_places']], ['پوهنتون قندهار'])
            nowhere = {'lat': '30.50', 'lon': '63.50'}            # Helmand desert: nothing within 30 km
            _, empty = alerts.handle_get(req(self.conn, query=nowhere, params={'id': alert['id']}))
            self.assertEqual(empty['safe_places'], [])

    def test_one_alert_guest_safe_places_near_alert(self):
        _, body = alerts.handle_get(req(self.conn, params={'id': str(self.mid['id'])}))
        self.assertIsNone(body['alert']['distance_km'])
        self.assertEqual(self.content.calls[-1][:2], (self.mid['lat'], self.mid['lon']))

    def test_one_alert_without_content_engine(self):
        with neighbours(content=None):
            _, body = alerts.handle_get(req(self.conn, params={'id': self.near['id']}))
        self.assertEqual(body['safe_places'], [])

    def test_unknown_alert(self):
        for value in (99999, 'abc', None):
            with self.assertRaises(ApiError) as caught:
                alerts.handle_get(req(self.conn, params={'id': value}))
            self.assertEqual(caught.exception.status, 404)


class NotificationTest(AlertsCase):
    def test_list_and_seen(self):
        uid = self.db.add_user(last=KABUL)
        other = self.db.add_user(last=destination(*KABUL, 0.5, 0))
        alert = self.create()['alert']
        code, body = alerts.handle_notifications(req(self.conn, user=self.user(uid)))
        self.assertEqual(code, 200)
        self.assertEqual(body['unseen'], 1)
        item = body['notifications'][0]
        self.assertEqual((item['alert_id'], item['alert']['id'], item['seen_at']), (alert['id'], alert['id'], None))
        code, seen = alerts.handle_seen(req(self.conn, user=self.user(uid), params={'id': item['id']}))
        self.assertEqual(code, 200)
        first_seen = seen['notification']['seen_at']
        self.assertTrue(first_seen)
        _, again = alerts.handle_seen(req(self.conn, user=self.user(uid), params={'id': item['id']}))
        self.assertEqual(again['notification']['seen_at'], first_seen)
        _, body = alerts.handle_notifications(req(self.conn, user=self.user(uid)))
        self.assertEqual(body['unseen'], 0)
        with self.assertRaises(ApiError) as caught:     # someone else's notification
            alerts.handle_seen(req(self.conn, user=self.user(other), params={'id': item['id']}))
        self.assertEqual(caught.exception.status, 404)

    def test_guest_gets_401(self):
        for handler, params in ((alerts.handle_notifications, {}), (alerts.handle_seen, {'id': 1})):
            with self.assertRaises(ApiError) as caught:
                handler(req(self.conn, params=params))
            self.assertEqual(caught.exception.status, 401)

    def test_uses_web_require_user_when_present(self):
        request = req(self.conn)
        request.require_user = mock.Mock(side_effect=ApiError(401, 'login_required', 'x'))
        with self.assertRaises(ApiError):
            alerts.handle_notifications(request)
        request.require_user.assert_called_once()


class AdminTest(AlertsCase):
    def test_create_through_handler(self):
        self.db.add_user(last=KABUL)
        code, body = alerts.handle_admin_create(req(self.conn, admin=ADMIN, body=dict(FLOOD)))
        self.assertEqual(code, 201)
        self.assertEqual((body['notified'], body['sms']), (1, 1))
        self.assertEqual(self.conn.execute('SELECT created_by FROM alerts').fetchone()[0], 7)

    def test_admin_only(self):
        uid = self.db.add_user(last=KABUL)
        handlers = [(alerts.handle_admin_create, {}), (alerts.handle_admin_list, {}),
                    (alerts.handle_admin_end, {'id': 1}), (alerts.handle_admin_stats, {})]
        for handler, params in handlers:
            with self.assertRaises(ApiError) as caught:
                handler(req(self.conn, body=dict(FLOOD), params=params))
            self.assertEqual(caught.exception.status, 401, handler.__name__)
            with self.assertRaises(ApiError) as caught:
                handler(req(self.conn, user=self.user(uid), body=dict(FLOOD), params=params))
            self.assertEqual(caught.exception.status, 403, handler.__name__)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM alerts').fetchone()[0], 0)

    def test_list_with_counts(self):
        a = self.db.add_user(last=KABUL)
        self.db.add_user(last=destination(*KABUL, 1, 0))
        first = self.create()['alert']
        second = self.create(type='earthquake', lat=30.0, lon=62.0)['alert']
        alerts.end(self.conn, second['id'])
        nid = self.conn.execute('SELECT id FROM notifications WHERE user_id = ?', (a,)).fetchone()[0]
        alerts.handle_seen(req(self.conn, user=self.user(a), params={'id': nid}))
        self.conn.commit()                                 # like web at the end of a request
        self.assertTrue(alerts.wait_sms(self.conn))
        code, body = alerts.handle_admin_list(req(self.conn, admin=ADMIN))
        self.assertEqual(code, 200)
        self.assertEqual(body['counts'], {'active': 1, 'ended': 1})
        top = body['alerts'][0]
        self.assertEqual((top['id'], top['notified'], top['seen'], top['sms'], top['created_by']),
                         (first['id'], 2, 1, 2, 7))
        self.assertEqual((top['sms_queued'], top['sms_failed']), (0, 0))
        _, only_ended = alerts.handle_admin_list(req(self.conn, admin=ADMIN, query={'status': 'ended'}))
        self.assertEqual([x['id'] for x in only_ended['alerts']], [second['id']])

    def test_end(self):
        alert = self.create()['alert']
        self.live.events.clear()
        code, body = alerts.handle_admin_end(req(self.conn, admin=ADMIN, params={'id': alert['id']}))
        self.assertEqual(code, 200)
        self.assertEqual(body['alert']['status'], 'ended')
        self.assertTrue(body['alert']['ended_at'])
        self.assertEqual([(e['event'], e['everyone'], e['data']['id']) for e in self.live.events],
                         [('alert_end', True, alert['id'])])
        _, listing = alerts.handle_list(req(self.conn, query={'lat': '34.5', 'lon': '69.2'}))
        self.assertEqual(listing['active'], [])
        self.assertEqual(listing['past'][0]['id'], alert['id'])
        with self.assertRaises(ApiError) as caught:
            alerts.handle_admin_end(req(self.conn, admin=ADMIN, params={'id': alert['id']}))
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(len(self.live.events), 1)           # no event the second time
        with self.assertRaises(ApiError) as caught:
            alerts.handle_admin_end(req(self.conn, admin=ADMIN, params={'id': 4242}))
        self.assertEqual(caught.exception.status, 404)

    def test_stats(self):
        self.db.add_user(last=KABUL)
        self.db.add_user()
        self.create()                                          # today
        self.create(type='earthquake', starts_at=iso_ago(24 * 3))
        done = self.create()['alert']
        alerts.end(self.conn, done['id'])
        self.assertTrue(alerts.wait_sms(self.conn))
        code, body = alerts.handle_admin_stats(req(self.conn, admin=ADMIN))
        self.assertEqual(code, 200)
        self.assertEqual(set(body), {'users', 'active_alerts', 'alerts_today', 'notifications', 'sms_queue', 'sos',
                                     'sms', 'usgs'})
        self.assertEqual(body['sms_queue'], {'queued': 0, 'sending': 0, 'sent': 3, 'failed': 0})
        self.assertEqual((body['users'], body['active_alerts'], body['alerts_today'], body['notifications']),
                         (2, 2, 2, 3))
        self.assertEqual(body['sos'], {'open': 2, 'today': 3})
        self.assertEqual(body['sms']['total'], 3)
        self.assertEqual(body['usgs']['new_total'], 0)
        with neighbours(sos=None, sms=None, usgs=None):
            stats = alerts.stats(self.conn)
        self.assertEqual((stats['sos'], stats['sms'], stats['usgs']), (None, None, None))

    def test_stats_neighbour_failure_is_null(self):
        broken = types.ModuleType('server.sos')
        broken.stats = mock.Mock(side_effect=RuntimeError('no table'))
        with neighbours(sos=broken), self.assertLogs('server.alerts', 'ERROR'):
            self.assertIsNone(alerts.stats(self.conn)['sos'])

    def test_kabul_day(self):
        start, end = alerts._kabul_day(datetime.datetime(2026, 11, 5, 20, 0, tzinfo=datetime.timezone.utc))
        self.assertEqual((start, end), ('2026-11-05T19:30:00Z', '2026-11-06T19:30:00Z'))   # 00:30 on Nov 6 in Kabul


class SearchTest(AlertsCase):
    def setUp(self):
        super().setUp()
        self.kabul_flood = self.create(place='کارته چهار، كابل')['alert']
        self.jalalabad_flood = alerts.add_past(self.conn, dict(FLOOD, place='جلال‌آباد، ننگرهار', safe_place='تپه'),
                                               'admin')
        self.jurm_quake = alerts.create(self.conn, {'type': 'earthquake', 'lat': 36.446, 'lon': 70.718,
                                                    'place': '47 km SSW of Jurm, Afghanistan', 'magnitude': 4.2},
                                        'usgs')['alert']
        self.kabul_quake = self.create(type='earthquake', place='ناحیه ۴، کابل')['alert']

    def ids(self, q):
        return [a['id'] for a in alerts.search(self.conn, q)]

    def test_by_type_in_three_languages(self):
        floods = {self.kabul_flood['id'], self.jalalabad_flood['id']}
        quakes = {self.jurm_quake['id'], self.kabul_quake['id']}
        for q in ('سیلاب', 'سیل', 'سېلاب', 'flood', 'Floods', 'سیلاب‌ها', 'هشدار سیلاب', 'د سېلاب خبرتیا'):
            self.assertEqual(set(self.ids(q)), floods, q)
        for q in ('زلزله', 'زمین‌لرزه', 'زمین لرزه', 'زلزلې', 'earthquake', 'QUAKE'):
            self.assertEqual(set(self.ids(q)), quakes, q)

    def test_by_place(self):
        self.assertEqual(set(self.ids('کابل')), {self.kabul_flood['id'], self.kabul_quake['id']})   # ك = ک
        self.assertEqual(self.ids('jurm'), [self.jurm_quake['id']])
        for q in ('جلال‌آباد', 'جلال آباد', 'جلالاباد'):          # with ZWNJ, a space or joined: the same place
            self.assertEqual(self.ids(q), [self.jalalabad_flood['id']], q)
        self.assertEqual(self.ids('ناحیه 4'), [self.kabul_quake['id']])   # Dari and Latin digits are the same

    def test_type_and_place(self):
        self.assertEqual(self.ids('سیلاب کابل'), [self.kabul_flood['id']])
        self.assertEqual(self.ids('earthquake Jurm'), [self.jurm_quake['id']])

    def test_active_first_and_nothing(self):
        found = alerts.search(self.conn, 'سیلاب')
        self.assertEqual(found[0]['status'], 'active')
        self.assertEqual(self.ids('هرات'), [])
        self.assertEqual(self.ids(''), [])
        self.assertEqual(self.ids(None), [])
        self.assertEqual(self.ids('، ؟'), [])

    def test_without_tables(self):
        bare = TempDB()
        self.addCleanup(bare.close)
        self.assertEqual(alerts.search(bare.conn, 'سیلاب'), [])


def slow_sms(seconds):
    """Fake sms where each SMS "takes" seconds to send (like a real provider)."""
    mod = fake_sms()
    quick = mod.send

    def send(db, phone, text, kind='other'):
        time.sleep(seconds)
        return quick(db, phone, text, kind)

    mod.send = send
    return mod


class SmsQueueTest(AlertsCase):
    """Alert SMS: after the event and in the background; the queue is not lost, no repeats, the rest is sent on stop."""

    def server(self):
        app = types.SimpleNamespace(db_path=self.db.path, stopping=threading.Event(), config={})
        self.addCleanup(self.stop_server, app)
        return app

    def stop_server(self, app, timeout=10):
        """Stop the "server" and wait until the SMS queue of its database finishes sending and lets go."""
        app.stopping.set()
        key = alerts._path_key(app.db_path)
        deadline = time.monotonic() + timeout
        while key in alerts.SMS_QUEUE._jobs and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertNotIn(key, alerts.SMS_QUEUE._jobs)

    def queued_rows(self, count, status='queued', claim=None, next_at=None):
        """Queue rows as a previous run left them: the alert and notifications, SMS not sent yet."""
        alert = alerts._insert(self.conn, alerts._clean(self.conn, dict(FLOOD), 'admin'), 'active')
        uids = [self.db.add_user(last=KABUL) for _ in range(count)]
        self.conn.executemany('INSERT INTO notifications (alert_id, user_id, distance_km, created_at, sms_status, '
                              'sms_claim, sms_next_at) VALUES (?, ?, 0, ?, ?, ?, ?)',
                              [(alert['id'], uid, utcnow(), status, claim, next_at) for uid in uids])
        self.conn.commit()
        return uids

    def left(self):
        return self.conn.execute("SELECT count(*) FROM notifications WHERE sms_status IN ('queued', 'sending')"
                                 ).fetchone()[0]

    def test_event_goes_before_sms_and_answer_does_not_wait(self):
        self.sms = slow_sms(0.1)
        at_event = []

        def publish(event, data, **target):
            at_event.append((event, len(self.sms.sent)))

        self.live.publish = publish
        users = [self.db.add_user(last=destination(*KABUL, 0.3 * i, 40 * i)) for i in range(5)]
        with neighbours(sms=self.sms):
            started = time.monotonic()
            result = self.create()
            elapsed = time.monotonic() - started
            self.assertEqual(at_event, [('alert', 0)])         # the event went out, but no SMS yet
            self.assertEqual((result['notified'], result['sms']), (5, 5))
            self.assertLess(elapsed, 0.3)                      # before, the response would have waited all 5 × 0.1 s
            self.assertLess(result['ms'], 300)
            self.assertTrue(alerts.wait_sms(self.conn, 10))
        self.assertEqual(len(self.sms.sent), 5)
        rows = self.conn.execute('SELECT * FROM notifications ORDER BY id').fetchall()
        self.assertEqual([row['user_id'] for row in rows], users)       # nearest first
        self.assertTrue(all(row['sms_status'] == 'sent' and row['sms_id'] and row['sms_sent_at'] for row in rows))

    def test_queue_left_by_previous_run_is_sent_once_at_start(self):
        uids = self.queued_rows(3)
        stuck = self.queued_rows(1, status='sending', claim='crashed-1', next_at=utcnow(-5))   # "claimed" by a crashed sender
        busy = self.queued_rows(1, status='sending', claim='other-1', next_at=utcnow(60))      # a live sender is sending
        with self.assertLogs('server.alerts', 'WARNING') as logs:
            alerts.start(self.server())
            deadline = time.monotonic() + 10
            while self.left() > 1 and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertTrue(any('queued again' in line for line in logs.output))
        phones = sorted(message['phone'] for message in self.sms.sent)
        self.assertEqual(phones, sorted(self.db.user(uid)['phone'] for uid in uids + stuck))
        self.assertEqual(self.conn.execute('SELECT sms_status FROM notifications WHERE user_id = ?',
                                           (busy[0],)).fetchone()[0], 'sending')   # another sender's claim is left alone
        alerts.SMS_QUEUE.kick(self.conn)
        time.sleep(0.2)
        self.assertEqual(len(self.sms.sent), 4)                                    # no repeats
        self.conn.execute("UPDATE notifications SET sms_next_at = ? WHERE user_id = ?", (utcnow(-1), busy[0]))
        self.conn.commit()                                     # the live sender "crashed": its claim expired
        with self.assertLogs('server.alerts', 'WARNING'):
            self.assertTrue(alerts.wait_sms(self.conn, 10))
        self.assertEqual(len(self.sms.sent), 5)

    def test_two_senders_send_each_sms_once(self):
        real_sms = REAL_SMS
        if real_sms is None:
            self.skipTest('sms engine not ready')
        real_sms.init(self.conn)
        self.queued_rows(120)
        done = []

        def sender():
            queue, db = alerts.SmsQueue(), store.connect(self.db.path)
            try:
                done.append(queue.process(db)[0])
            finally:
                db.close()

        with neighbours(sms=real_sms):
            threads = [threading.Thread(target=sender) for _ in range(3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
        self.assertEqual(sum(done), 120)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM sms_outbox WHERE kind = 'alert'").fetchone()[0], 120)
        self.assertEqual(self.conn.execute("SELECT count(DISTINCT sms_id) FROM notifications WHERE sms_status = 'sent'"
                                           ).fetchone()[0], 120)

    def test_stop_drains_the_queue(self):
        self.sms = slow_sms(0.02)
        app = self.server()
        for i in range(10):
            self.db.add_user(last=destination(*KABUL, 0.1 * i, 36 * i))
        with neighbours(sms=self.sms), self.assertLogs('server.alerts', 'INFO') as logs:
            alerts.start(app)
            self.assertEqual(self.create()['sms'], 10)
            self.stop_server(app)
        self.assertEqual(len(self.sms.sent), 10)
        self.assertEqual(self.left(), 0)
        self.assertTrue(any('queue is empty' in line for line in logs.output))

    def test_stop_says_what_was_not_sent_and_next_start_sends_it(self):
        self.sms = slow_sms(0.3)
        app = self.server()
        for i in range(5):
            self.db.add_user(last=destination(*KABUL, 0.2 * i, 72 * i))
        with neighbours(sms=self.sms), mock.patch.object(alerts, 'SMS_STOP_DRAIN_S', 0.1), \
                self.assertLogs('server.alerts', 'WARNING') as logs:
            alerts.start(app)
            self.create()
            time.sleep(0.05)                                   # the first SMS is already "sending"
            self.stop_server(app)
        left = self.left()
        self.assertGreater(left, 0)
        self.assertEqual(len(self.sms.sent) + left, 5)                     # nothing lost
        self.assertTrue(any('%d alert SMS not sent' % left in line and 'next start' in line for line in logs.output))
        later = fake_sms()
        with neighbours(sms=later):                            # the next server run
            alerts.start(self.server())
            self.assertTrue(alerts.wait_sms(self.conn, 10))
        phones = [message['phone'] for message in self.sms.sent + later.sent]
        self.assertEqual((len(phones), len(set(phones))), (5, 5))         # one for each

    def test_memory_database_sends_after_event(self):
        mem = store.connect(':memory:')
        self.addCleanup(mem.close)
        geo.init(mem)
        alerts.init(mem)
        mem.execute("INSERT INTO users (phone, last_lat, last_lon) VALUES ('+93700999999', ?, ?)", KABUL)
        order = []
        self.live.publish = lambda event, data, **target: order.append(event)
        quick = self.sms.send
        self.sms.send = lambda db, phone, text, kind='other': order.append('sms') or quick(db, phone, text, kind)
        result = alerts.create(mem, dict(FLOOD), 'admin')
        self.assertEqual((result['sms'], order), (1, ['alert', 'sms']))
        self.assertEqual(mem.execute('SELECT sms_status FROM notifications').fetchone()[0], 'sent')

    def test_old_database_is_upgraded_without_resending(self):
        old = TempDB()
        self.addCleanup(old.close)
        old.conn.execute(alerts.SCHEMA[0])
        old.conn.execute('CREATE TABLE notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, alert_id INTEGER NOT NULL '
                         'REFERENCES alerts (id) ON DELETE CASCADE, user_id INTEGER NOT NULL REFERENCES users (id) '
                         'ON DELETE CASCADE, distance_km REAL, sms_id INTEGER, created_at TEXT NOT NULL, '
                         'seen_at TEXT, UNIQUE (alert_id, user_id))')
        old.conn.execute("INSERT INTO alerts (type, source, lat, lon, starts_at, created_at) "
                         "VALUES ('flood', 'admin', 34.5, 69.2, ?, ?)", (utcnow(), utcnow()))
        sent, never = old.add_user(last=KABUL), old.add_user(last=KABUL)
        old.conn.executemany('INSERT INTO notifications (alert_id, user_id, sms_id, created_at) VALUES (1, ?, ?, ?)',
                             [(sent, 5, utcnow()), (never, None, utcnow())])
        alerts.init(old.conn)
        alerts.init(old.conn)
        old.conn.commit()                  # web commits after init
        statuses = dict(old.conn.execute('SELECT user_id, sms_status FROM notifications').fetchall())
        self.assertEqual(statuses, {sent: 'sent', never: 'none'})
        self.assertTrue(alerts.wait_sms(old.conn, 1))
        self.assertEqual(self.sms.sent, [])


EVENT_FIELDS = {'id', 'type', 'place', 'lat', 'lon', 'radius_km', 'magnitude', 'starts_at', 'status', 'safe_place',
                'safe_lat', 'safe_lon'}   # from these the screen itself works out "I am inside the radius"


class HttpTest(unittest.TestCase):
    """Through the real web: server on a free port, admin login (fake auth), SSE 'alert'."""

    def test_event_comes_after_commit_30_times(self):
        """Keep SSE open; create and end an alert 30 times; on every event, GET /api/alerts/{id} right away."""
        try:
            import support
        except ImportError as exc:
            self.skipTest('web/support not ready: %s' % exc)
        from server import live
        real_publish = live.publish

        def slow_publish(*args, **kwargs):     # the event is out, but the caller returns only after 40 ms:
            sent = real_publish(*args, **kwargs)   # this way the "event before the write" window shows every time
            time.sleep(0.04)
            return sent

        with neighbours(sms=fake_sms(), content=None, sos=None), \
                mock.patch.object(geo, 'fetch_json', FakeFetch(OSError('offline'))), \
                mock.patch.object(live, 'publish', slow_publish), \
                support.Running(engines=[('auth', support.fake_auth()), 'geo', 'alerts'],
                                config={'usgs': False}) as srv:
            admin, guest = srv.client(), srv.client()
            self.assertEqual(admin.post('/api/test/login', {'token': 'tok-admin'}).status, 200)
            stream = guest.stream()
            wrong, answers = [], []
            try:
                srv.wait_listeners(1)

                def step(path, body, event_name, status):
                    """Admin request in its own thread; on the event, GET right away → (alert id)."""
                    sender = support.run_in_thread(lambda: answers.append(admin.post(path, body)))
                    data = stream.events_until(event_name)[-1][1]
                    r = guest.get('/api/alerts/%d' % data['id'])            # right on the event
                    got = r.json()['alert']['status'] if r.status == 200 else r.status
                    if got != status:
                        wrong.append((event_name, data['id'], got))
                    self.assertTrue(EVENT_FIELDS <= set(data), EVENT_FIELDS - set(data))
                    self.assertEqual(data['status'], status)
                    sender.join(10)
                    return data['id']

                for _ in range(30):
                    alert_id = step('/api/admin/alerts', dict(FLOOD), 'alert', 'active')
                    step('/api/admin/alerts/%d/end' % alert_id, {}, 'alert_end', 'ended')
            finally:
                stream.close()
            self.assertEqual(wrong, [])
            self.assertEqual([r.status for r in answers], [201, 200] * 30)

    def test_admin_creates_alert_resident_gets_it(self):
        try:
            import support
        except ImportError as exc:                   # web is not ready yet
            self.skipTest('web/support not ready: %s' % exc)
        sms = fake_sms()
        with neighbours(sms=sms, content=None, sos=None), \
                mock.patch.object(geo, 'fetch_json', FakeFetch(OSError('offline'))), \
                support.Running(engines=[('auth', support.fake_auth()), 'geo', 'alerts'],
                                config={'usgs': False}) as srv:
            with srv.app.transaction() as db:
                db.execute("INSERT INTO users (id, phone, last_lat, last_lon) VALUES (1, '+93700000001', ?, ?)",
                           destination(*KABUL, 1.5, 120))
            admin, resident, guest = srv.client(), srv.client(), srv.client()
            self.assertEqual(admin.post('/api/test/login', {'token': 'tok-admin'}).status, 200)
            self.assertEqual(resident.post('/api/test/login', {'token': 'tok-user-1'}).status, 200)
            self.assertEqual(guest.post('/api/admin/alerts', dict(FLOOD)).status, 403)
            stream = guest.stream()
            try:
                srv.wait_listeners(1)
                r = admin.post('/api/admin/alerts', dict(FLOOD, starts_at=utcnow()))
                self.assertEqual(r.status, 201, r.text)
                created = r.json()
                self.assertEqual((created['notified'], created['sms']), (1, 1))   # SMS queued
                events = stream.events_until('alert')
                self.assertEqual(events[-1][1]['id'], created['alert']['id'])
            finally:
                stream.close()
            with srv.app.transaction() as db:
                self.assertTrue(alerts.wait_sms(db))
            self.assertEqual(len(sms.sent), 1)
            r = guest.get('/api/alerts?lat=%s&lon=%s' % KABUL)
            self.assertEqual(r.status, 200)
            body = r.json()
            self.assertEqual(body['active'][0]['place'], 'کارته چهار، کابل')
            self.assertTrue(body['active'][0]['inside'])
            r = resident.get('/api/notifications')
            self.assertEqual((r.status, r.json()['unseen']), (200, 1))
            nid = r.json()['notifications'][0]['id']
            self.assertEqual(resident.post('/api/notifications/%d/seen' % nid).status, 200)
            self.assertEqual(guest.get('/api/notifications').status, 401)
            self.assertEqual(admin.get('/api/admin/stats').json()['active_alerts'], 1)
            r = admin.post('/api/admin/alerts/%d/end' % created['alert']['id'])
            self.assertEqual((r.status, r.json()['alert']['status']), (200, 'ended'))
            self.assertEqual(guest.get('/api/alerts/abc').status, 404)
            r = guest.get('/api/geo/name?lat=34.555&lon=69.207')
            self.assertEqual((r.status, r.json()['name']), (200, ''))
            self.assertEqual(guest.get('/api/geo/search?q=').status, 400)


if __name__ == '__main__':
    unittest.main()
