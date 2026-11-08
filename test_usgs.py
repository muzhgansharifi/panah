"""Tests for usgs on a real feed snapshot (app/tests/data): the Afghanistan filter, repeats, history, no network, the thread."""
import datetime
import threading
import types
import unittest
from unittest import mock

from alerts_support import DATA, FakeFetch, TempDB, destination, fake_live, fake_sms, load_json, neighbours, req

from server import alerts, geo, store, usgs
from server.common import to_utc

LATEST_AF = 'us7000tw2x'                     # M4.2, 47 km SSW of Jurm, 2026-11-04 18:54:49 UTC
LATEST_AF_AT = datetime.datetime(2026, 11, 4, 18, 54, 49, 398000, tzinfo=datetime.timezone.utc)
NOW = LATEST_AF_AT + datetime.timedelta(hours=2)
AFGHAN_IDS = {'us7000tw2x', 'us7000tupb', 'us7000ttnr', 'us7000ttgz', 'us7000ttgc', 'us6000u4va', 'us6000u4h9',
              'us6000u42e', 'us6000u2fx', 'us6000u279', 'us6000u211', 'us6000u1iz', 'us6000u1h9', 'us6000u2tp',
              'us6000u15g'}
NEIGHBOURS_IN_BOX = {'us6000u3d2', 'us6000u3b0', 'us6000u20g', 'us6000u1hm'}   # Tajikistan, inside the box


def feature(event_id, lat, lon, place, mag=4.5, at=NOW, kind='earthquake', ids=None, depth=10.0):
    return {'type': 'Feature', 'id': event_id,
            'properties': {'mag': mag, 'place': place, 'time': int(at.timestamp() * 1000), 'type': kind,
                           'ids': ids if ids is not None else ',%s,' % event_id},
            'geometry': {'type': 'Point', 'coordinates': [lon, lat, depth]}}


def feed(*features):
    return {'type': 'FeatureCollection', 'metadata': {'count': len(features)}, 'features': list(features)}


class UsgsCase(unittest.TestCase):
    def setUp(self):
        geo.reset_network_state()
        self.db = TempDB(geo, alerts)
        self.addCleanup(self.db.close)
        self.conn = self.db.conn
        self.sms, self.live = fake_sms(), fake_live()
        patcher = neighbours(sms=self.sms, live=self.live, content=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.drain_sms)    # background alert SMS: before the fakes and the database are removed
        self.sample = load_json('usgs_sample.geojson')

    def drain_sms(self):
        self.conn.commit()                 # like the polling thread after poll_once
        alerts.wait_sms(self.conn, 5)

    def usgs_rows(self):
        return {row['source_id']: row for row in self.conn.execute("SELECT * FROM alerts WHERE source = 'usgs'")}


class FilterTest(UsgsCase):
    def test_real_feed_afghanistan_only(self):
        quakes = [q for q in map(usgs.parse_feature, self.sample['features']) if q and usgs.is_afghanistan(q)]
        ids = {q['id'] for q in quakes}
        self.assertEqual(len(self.sample['features']), 77)
        self.assertEqual(len(quakes), 15)
        self.assertEqual(ids, AFGHAN_IDS)
        self.assertFalse(ids & NEIGHBOURS_IN_BOX)
        for q in quakes:
            self.assertIn('Afghanistan', q['place'])
            self.assertTrue(29.3 <= q['lat'] <= 38.5 and 60.5 <= q['lon'] <= 75.0)

    def test_box_edges_and_name(self):
        cases = [
            (feature('a', 29.3, 65.0, 'X, Afghanistan'), True),
            (feature('b', 29.29, 65.0, 'X, Afghanistan'), False),
            (feature('c', 38.5, 75.0, 'X, Afghanistan'), True),
            (feature('d', 35.0, 75.01, 'X, Afghanistan'), False),
            (feature('e', 35.0, 60.49, 'X, Afghanistan'), False),
            (feature('f', 35.0, 70.0, 'X, Tajikistan'), False),
            (feature('g', 35.0, 70.0, 'Hindu Kush region, Afghanistan'), True),
            (feature('h', 35.0, 70.0, 'X, Afghanistan', kind='quarry blast'), False),
        ]
        for item, expected in cases:
            self.assertEqual(usgs.is_afghanistan(usgs.parse_feature(item)), expected, item['id'])

    def test_broken_features_are_skipped(self):
        self.assertIsNone(usgs.parse_feature({'id': 'x'}))
        self.assertIsNone(usgs.parse_feature({'id': 'x', 'properties': {'time': 'soon'},
                                              'geometry': {'coordinates': [70, 35]}}))
        no_mag = usgs.parse_feature(feature('m', 35, 70, 'X, Afghanistan', mag=None))
        self.assertIsNone(no_mag['magnitude'])


class PollTest(UsgsCase):
    def test_first_poll_takes_month_history(self):
        resident = self.db.add_user(last=destination(36.446, 70.718, 1.0, 200))   # 1 km from the epicentre
        fetch = FakeFetch(self.sample)
        result = usgs.poll_once(self.conn, fetch=fetch, now=NOW)
        self.assertEqual(fetch.urls, [usgs.FEED_MONTH])
        self.assertEqual((result['fetched'], result['afghanistan'], result['new'], result['active'], result['feed']),
                         (77, 15, 15, 1, 'month'))
        rows = self.usgs_rows()
        self.assertEqual(len(rows), 15)
        latest = rows[LATEST_AF]
        source = next(f for f in self.sample['features'] if f['id'] == LATEST_AF)
        lon, lat, depth = source['geometry']['coordinates']
        self.assertEqual((latest['status'], latest['type'], latest['place'], latest['magnitude'], latest['depth_km']),
                         ('active', 'earthquake', '47 km SSW of Jurm, Afghanistan', 4.2, depth))
        self.assertEqual(latest['starts_at'], '2026-11-04T18:54:49Z')
        self.assertEqual((latest['lat'], latest['lon'], latest['radius_km']), (lat, lon, 2.0))
        self.assertTrue(all(row['status'] == 'ended' for key, row in rows.items() if key != LATEST_AF))
        strong_old = rows['us6000u4va']                                        # M5.2, Oct 25: already history
        self.assertEqual((strong_old['status'], strong_old['magnitude']), ('ended', 5.2))
        # the active alert went out: notification, SMS, live; history goes without them
        notes = self.conn.execute('SELECT user_id, alert_id FROM notifications').fetchall()
        self.assertEqual([(n['user_id'], n['alert_id']) for n in notes], [(resident, latest['id'])])
        self.assertEqual(result['active'], 1)
        self.conn.commit()                                                  # like the polling thread
        self.assertTrue(alerts.wait_sms(self.conn))                         # SMS in the background, after the event
        self.assertEqual(len(self.sms.sent), 1)
        self.assertIn('47 km SSW of Jurm, Afghanistan', self.sms.sent[0]['text'])
        self.assertEqual([e['event'] for e in self.live.events], ['alert'])

    def test_second_poll_takes_day_feed_without_repeats(self):
        usgs.poll_once(self.conn, fetch=FakeFetch(self.sample), now=NOW)
        fetch = FakeFetch(self.sample)
        result = usgs.poll_once(self.conn, fetch=fetch, now=NOW + datetime.timedelta(minutes=1))
        self.assertEqual(fetch.urls, [usgs.FEED_DAY])
        self.assertEqual((result['afghanistan'], result['new'], result['active']), (15, 0, 0))
        self.assertEqual(len(self.usgs_rows()), 15)
        self.assertEqual(len(self.live.events), 1)

    def test_real_day_feed_has_nothing_for_afghanistan(self):
        usgs.poll_once(self.conn, fetch=FakeFetch(self.sample), now=NOW)
        result = usgs.poll_once(self.conn, fetch=FakeFetch(load_json('usgs_day_sample.geojson')),
                                now=NOW + datetime.timedelta(minutes=1))
        self.assertEqual((result['fetched'], result['afghanistan'], result['new']), (31, 0, 0))

    def test_repeat_by_other_id(self):
        usgs.poll_once(self.conn, fetch=FakeFetch(feed(feature('us1', 35, 70, 'X, Afghanistan'))), now=NOW)
        renamed = feature('at99', 35, 70, 'X, Afghanistan', ids=',at99,us1,')      # USGS changed the main id
        result = usgs.poll_once(self.conn, fetch=FakeFetch(feed(renamed)), now=NOW)
        self.assertEqual(result['new'], 0)
        self.assertEqual(list(self.usgs_rows()), ['us1'])

    def test_weak_or_old_goes_to_past(self):
        data = feed(feature('weak', 35, 70, 'X, Afghanistan', mag=3.9, at=NOW - datetime.timedelta(hours=1)),
                    feature('edge', 35.1, 70, 'X, Afghanistan', mag=4.0, at=NOW - datetime.timedelta(hours=24)),
                    feature('old', 35.2, 70, 'X, Afghanistan', mag=6.0,
                            at=NOW - datetime.timedelta(hours=24, seconds=1)))
        result = usgs.poll_once(self.conn, fetch=FakeFetch(data), now=NOW)
        rows = self.usgs_rows()
        self.assertEqual((result['new'], result['active']), (3, 1))
        self.assertEqual({k: r['status'] for k, r in rows.items()}, {'weak': 'ended', 'edge': 'active', 'old': 'ended'})

    def test_active_usgs_alert_ends_after_24_hours(self):
        usgs.poll_once(self.conn, fetch=FakeFetch(self.sample), now=NOW)
        later = LATEST_AF_AT + datetime.timedelta(hours=24, minutes=1)
        fetch = FakeFetch(load_json('usgs_day_sample.geojson'))
        result = usgs.poll_once(self.conn, fetch=fetch, now=later)
        self.assertEqual(fetch.urls, [usgs.FEED_DAY])
        self.assertEqual(result['ended'], 1)
        self.assertEqual(self.usgs_rows()[LATEST_AF]['status'], 'ended')
        self.assertEqual([e['event'] for e in self.live.events], ['alert', 'alert_end'])

    def test_usgs_alert_is_in_database_before_events(self):
        """On the USGS path too: the alert commit comes before the 'alert' event, the 24 h expiry before 'alert_end'."""
        other = store.connect(self.db.path)
        self.addCleanup(other.close)
        seen = []

        def publish(event, data, **target):
            row = other.execute('SELECT status FROM alerts WHERE id = ?', (data['id'],)).fetchone()
            seen.append((event, data['source_id'], row['status'] if row else None))

        self.live.publish = publish
        usgs.poll_once(self.conn, fetch=FakeFetch(self.sample), now=NOW)
        usgs.poll_once(self.conn, fetch=FakeFetch(load_json('usgs_day_sample.geojson')),
                       now=LATEST_AF_AT + datetime.timedelta(hours=24, minutes=1))
        self.assertEqual(seen, [('alert', LATEST_AF, 'active'), ('alert_end', LATEST_AF, 'ended')])

    def test_catch_up_with_month_after_long_pause(self):
        usgs.poll_once(self.conn, fetch=FakeFetch(self.sample), now=NOW)
        fetch = FakeFetch(self.sample)
        usgs.poll_once(self.conn, fetch=fetch, now=NOW + datetime.timedelta(hours=usgs.CATCH_UP_HOURS, minutes=1))
        self.assertEqual(fetch.urls, [usgs.FEED_MONTH])

    def test_admin_alert_with_same_text_is_not_touched(self):
        alerts.create(self.conn, {'type': 'earthquake', 'lat': 36.446, 'lon': 70.718, 'safe_place': 'میدان'}, 'admin')
        usgs.poll_once(self.conn, fetch=FakeFetch(self.sample), now=NOW + datetime.timedelta(days=3))
        self.assertEqual(self.conn.execute("SELECT status FROM alerts WHERE source = 'admin'").fetchone()[0], 'active')


class OfflineTest(UsgsCase):
    def test_no_network_is_recorded_and_quiet(self):
        with self.assertLogs('server.usgs', 'WARNING'):
            result = usgs.poll_once(self.conn, fetch=FakeFetch(OSError('Network is unreachable')), now=NOW)
        self.assertEqual((result['fetched'], result['afghanistan'], result['new']), (0, 0, 0))
        self.assertIn('Network is unreachable', result['error'])
        status = usgs.status(self.conn)
        self.assertEqual((status['ok'], status['last_poll'], status['new_total']), (False, to_utc(NOW), 0))
        self.assertIn('OSError', status['last_error'])
        fetch = FakeFetch(self.sample)                       # history not taken yet: the monthly feed again
        result = usgs.poll_once(self.conn, fetch=fetch, now=NOW)
        self.assertEqual(fetch.urls, [usgs.FEED_MONTH])
        status = usgs.status(self.conn)
        self.assertEqual((status['ok'], status['last_error'], status['new_total']), (True, None, 15))
        self.assertEqual(status['last_event']['source_id'], LATEST_AF)
        self.assertEqual(set(status), {'last_poll', 'ok', 'last_error', 'last_event', 'new_total', 'feed', 'running',
                                       'history'})

    def test_garbage_answer(self):
        for answer in ('{"oops": 1}', b'not json', {'features': 'x'}, None):
            with self.assertLogs('server.usgs', 'WARNING'):
                result = usgs.poll_once(self.conn, fetch=FakeFetch(answer), now=NOW)
            self.assertIn('error', result)
        self.assertFalse(usgs.status(self.conn)['ok'])

    def test_status_before_any_poll(self):
        fresh = TempDB()
        self.addCleanup(fresh.close)
        self.assertEqual(usgs.status(fresh.conn),
                         {'last_poll': None, 'ok': None, 'last_error': None, 'last_event': None, 'new_total': 0,
                          'feed': None, 'running': False, 'history': None})

    def test_real_fetch_offline(self):
        with mock.patch.object(usgs.urllib.request, 'urlopen', side_effect=OSError('offline')), \
                self.assertLogs('server.usgs', 'WARNING'):
            result = usgs.poll_once(self.conn, now=NOW)
        self.assertIn('offline', result['error'])


HISTORY_AFGHAN = 114          # Afghanistan, M 5.5+, since 1990 in app/server/data/usgs_history.geojson
PAKTIKA_2022 = 'us7000hj3u'


class HistoryTest(UsgsCase):
    """Real past earthquakes from the saved USGS FDSN snapshot: loaded once, as past alerts, no repeats."""

    def test_catalog_snapshot_loads_once_as_past_alerts(self):
        self.db.add_user(last=(33.0203, 69.4639))                    # a resident at the 2022 Paktika epicentre
        result = usgs.load_history(self.conn)
        self.assertEqual((result['events'], result['afghanistan'], result['new']),
                         (180, HISTORY_AFGHAN, HISTORY_AFGHAN))
        rows = self.usgs_rows()
        self.assertEqual(len(rows), HISTORY_AFGHAN)
        self.assertTrue(all(row['status'] == 'ended' and row['type'] == 'earthquake' for row in rows.values()))
        self.assertTrue(all(row['magnitude'] >= 5.5 and 'Afghanistan' in row['place'] for row in rows.values()))
        paktika = rows[PAKTIKA_2022]
        self.assertEqual((paktika['magnitude'], paktika['starts_at'][:10]), (6.0, '2022-06-21'))
        years = {row['starts_at'][:4] for row in rows.values()}
        self.assertTrue({'1990', '1998', '2002', '2015', '2022', '2023', '2025'} <= years)
        self.assertEqual(rows['usp0008nze']['place'], '24 km E of Rustāq, Afghanistan')   # '?' restored
        broken = {row['place'] for row in rows.values() if '?' in row['place']}
        self.assertTrue(all('Kh?st' in place or 'Qar?wul' in place for place in broken), broken)
        # history: nobody is notified, no SMS, no live event
        self.assertEqual(self.conn.execute('SELECT count(*) FROM notifications').fetchone()[0], 0)
        self.assertEqual((self.sms.sent, self.live.events), ([], []))
        self.assertTrue(usgs.load_history(self.conn).get('skipped'))   # once per snapshot
        self.assertEqual(len(self.usgs_rows()), HISTORY_AFGHAN)
        self.assertEqual(usgs.status(self.conn)['history']['new'], HISTORY_AFGHAN)

    def test_home_gets_real_dates(self):
        usgs.load_history(self.conn)
        _, body = alerts.handle_list(req(self.conn, query={'lat': '34.5553', 'lon': '69.2075', 'sort': 'time'}))
        self.assertEqual(len(body['past']), 50)
        self.assertEqual(body['past'][0]['starts_at'][:10], '2026-07-01')       # the newest in the snapshot
        self.assertGreater(len({alert['starts_at'][:7] for alert in body['past']}), 30)   # not one month

    def test_no_repeats_with_the_live_feed(self):
        # the live feed brought a history event first → the history load skips it
        live_copy = feature('us6000t9gg', 36.31, 70.61, '50 km SW of Jurm, Afghanistan', mag=5.5,
                            at=NOW - datetime.timedelta(days=100))
        usgs.poll_once(self.conn, fetch=FakeFetch(feed(live_copy)), now=NOW)
        self.assertEqual(usgs.load_history(self.conn)['new'], HISTORY_AFGHAN - 1)
        self.assertEqual(len(self.usgs_rows()), HISTORY_AFGHAN)
        # history first, then the live feed brings one of its events as fresh → still no repeat, no active alert
        fresh = feature('us7000qsvj', 34.9, 70.8, '35 km WSW of Asadābād, Afghanistan', mag=6.0, at=NOW)
        self.assertEqual(usgs.poll_once(self.conn, fetch=FakeFetch(feed(fresh)), now=NOW)['new'], 0)
        self.assertEqual(self.usgs_rows()['us7000qsvj']['status'], 'ended')

    def test_missing_files_do_not_break_anything(self):
        with self.assertLogs('server.usgs', 'WARNING'):
            result = usgs.load_history(self.conn, path=DATA / 'no-such-history.geojson')
        self.assertEqual(result['new'], 0)
        self.assertIn('error', result)
        with self.assertLogs('server.usgs', 'WARNING'):
            self.assertEqual(usgs.place_fixes(DATA / 'no-such-fixes.json'), {})
        self.assertEqual(usgs.fix_place('45 km SW of Ashk?sham, Afghanistan', usgs.place_fixes()),
                         '45 km SW of Ashkāsham, Afghanistan')


class ThreadTest(UsgsCase):
    def app(self, **config):
        return types.SimpleNamespace(config=config, db_path=self.db.path, stopping=threading.Event())

    def test_off_in_config(self):
        self.assertIsNone(usgs.start(self.app(usgs=False)))
        self.assertFalse(usgs.status(self.conn)['running'])
        self.assertEqual(self.usgs_rows(), {})                        # off: no history either

    def test_thread_polls_and_stops(self):
        fetch = FakeFetch(self.sample)
        app = self.app(usgs=True, usgs_interval=0.2)
        with mock.patch.object(usgs, 'fetch_feed', fetch):
            thread = usgs.start(app)
            self.addCleanup(usgs.stop)
            self.assertEqual(len(self.usgs_rows()), HISTORY_AFGHAN)    # history is in as soon as start returns
            self.assertTrue(thread.is_alive())
            self.assertIs(usgs.start(app), thread)                    # second time: the same thread
            deadline = datetime.datetime.now() + datetime.timedelta(seconds=10)
            while len(fetch.urls) < 2 and datetime.datetime.now() < deadline:
                threading.Event().wait(0.05)
            app.stopping.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(len(fetch.urls), 2)
        self.assertEqual(fetch.urls[:2], [usgs.FEED_MONTH, usgs.FEED_DAY])
        check = store.connect(self.db.path)                           # the thread committed by itself
        try:
            self.assertEqual(check.execute("SELECT count(*) FROM alerts WHERE source = 'usgs'").fetchone()[0],
                             HISTORY_AFGHAN + 15)                    # the catalog history + the month feed
            self.assertTrue(usgs.status(check)['ok'])
        finally:
            check.close()


if __name__ == '__main__':
    unittest.main()
