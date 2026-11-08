"""Tests for geo: distances against known values, place name, cache, the Nominatim queue, no network."""
import io
import json
import math
import types
import unittest
import urllib.error
import urllib.parse
from unittest import mock

from alerts_support import FakeFetch, TempDB, destination, fake_content, load_json, neighbours, req

from server import geo
from server.common import ApiError

R = geo.EARTH_RADIUS_KM
FIGMA_POINT = (34.5550, 69.2070)          # the point from Figma frame 10
KABUL_NAME = 'جاده میدان هوایی، ناحیه دهم، کابل ښار'


def query_of(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


class DistanceTest(unittest.TestCase):
    def test_same_point_is_zero(self):
        self.assertEqual(geo.distance_km(34.5553, 69.2075, 34.5553, 69.2075), 0.0)

    def test_one_degree_of_latitude(self):
        # along a meridian: R · π / 180 = 111.19508… km
        self.assertAlmostEqual(geo.distance_km(0, 0, 1, 0), R * math.pi / 180, places=9)
        self.assertAlmostEqual(geo.distance_km(34, 69, 35, 69), 111.195080, places=5)

    def test_quarter_and_half_of_equator(self):
        self.assertAlmostEqual(geo.distance_km(0, 0, 0, 90), R * math.pi / 2, places=6)    # 10007.557 km
        self.assertAlmostEqual(geo.distance_km(0, 0, 0, 180), R * math.pi, places=6)       # 20015.115 km
        self.assertAlmostEqual(geo.distance_km(90, 0, -90, 0), R * math.pi, places=6)      # pole to pole

    def test_one_degree_of_longitude_on_parallel(self):
        # at latitude φ, 1° of longitude along the parallel: 2R·asin(cos φ · sin 0.5°)
        phi = math.radians(34.5)
        expected = 2 * R * math.asin(math.cos(phi) * math.sin(math.radians(0.5)))
        self.assertAlmostEqual(geo.distance_km(34.5, 69, 34.5, 70), expected, places=9)
        self.assertAlmostEqual(expected, 91.66, places=1)

    def test_symmetric_and_strings(self):
        a = geo.distance_km(34.5553, 69.2075, 31.6289, 65.7372)    # Kabul to Kandahar
        self.assertAlmostEqual(a, geo.distance_km(31.6289, 65.7372, 34.5553, 69.2075), places=9)
        self.assertAlmostEqual(a, geo.distance_km('34.5553', '69.2075', '31.6289', '65.7372'), places=9)
        self.assertTrue(440 < a < 480, a)

    def test_two_km_in_every_direction(self):
        for bearing in (0, 45, 90, 135, 180, 225, 270, 315):
            lat, lon = destination(34.5553, 69.2075, 2.0, bearing)
            self.assertAlmostEqual(geo.distance_km(34.5553, 69.2075, lat, lon), 2.0, places=9)


class ParsePointTest(unittest.TestCase):
    def test_strings_and_dari_digits(self):
        self.assertEqual(geo.parse_point('34.5', ' 69.2 '), (34.5, 69.2))
        self.assertEqual(geo.parse_point('۳۴٫۵', '۶۹٫۲'), (34.5, 69.2))

    def test_bad_values(self):
        for lat, lon in (('abc', '69'), ('91', '69'), ('34', '181'), ('nan', '69'), ('34', ''), (True, 69)):
            with self.assertRaises(ApiError) as caught:
                geo.parse_point(lat, lon)
            self.assertEqual(caught.exception.status, 400, (lat, lon))
            self.assertEqual(caught.exception.code, 'bad_location')

    def test_missing(self):
        with self.assertRaises(ApiError) as caught:
            geo.parse_point(None, '')
        self.assertEqual(caught.exception.code, 'location_required')
        self.assertIsNone(geo.parse_point(None, None, required=False))


class PlaceNameTest(unittest.TestCase):
    def setUp(self):
        geo.reset_network_state()
        self.db = TempDB(geo)
        self.addCleanup(self.db.close)
        self.conn = self.db.conn
        self.reverse = load_json('nominatim_reverse_kabul.json')

    def test_real_kabul_answer_is_short_dari_address(self):
        fetch = FakeFetch(self.reverse)
        self.assertEqual(geo.place_name(self.conn, *FIGMA_POINT, fetch=fetch), KABUL_NAME)
        self.assertEqual(len(fetch.urls), 1)
        url = fetch.urls[0]
        self.assertTrue(url.startswith('https://nominatim.openstreetmap.org/reverse?'), url)
        q = query_of(url)
        self.assertEqual((q['lat'], q['lon'], q['format'], q['accept-language'], q['zoom']),
                         ('34.5550', '69.2070', 'jsonv2', 'fa', '16'))

    def test_cache_hit_does_not_touch_network(self):
        fetch = FakeFetch(self.reverse)
        first = geo.place_name(self.conn, *FIGMA_POINT, fetch=fetch)
        second = geo.place_name(self.conn, *FIGMA_POINT, fetch=fetch)
        self.assertEqual(first, second)
        self.assertEqual(len(fetch.urls), 1)
        row = self.conn.execute('SELECT * FROM geo_cache').fetchone()
        self.assertEqual((row['key'], row['lat'], row['lon'], row['lang'], row['name']),
                         ('fa:34.5550,69.2070', 34.555, 69.207, 'fa', KABUL_NAME))

    def test_rounding_to_four_digits_shares_cache(self):
        fetch = FakeFetch(self.reverse)
        geo.place_name(self.conn, 34.55504, 69.20704, fetch=fetch)
        geo.place_name(self.conn, 34.55496, 69.20696, fetch=fetch)       # same 4 decimals: 34.5550, 69.2070
        self.assertEqual(len(fetch.urls), 1)
        geo.place_name(self.conn, 34.5556, 69.2070, fetch=fetch)         # another cell ≈ 11 m away
        self.assertEqual(len(fetch.urls), 2)

    def test_pashto_has_own_cache_and_language(self):
        fetch = FakeFetch(self.reverse)
        geo.place_name(self.conn, *FIGMA_POINT, fetch=fetch)
        geo.place_name(self.conn, *FIGMA_POINT, lang='ps', fetch=fetch)
        self.assertEqual(len(fetch.urls), 2)
        self.assertEqual(query_of(fetch.urls[1])['accept-language'], 'ps,fa')

    def test_desert_answer(self):
        fetch = FakeFetch(load_json('nominatim_reverse_helmand.json'))
        self.assertEqual(geo.place_name(self.conn, 30.9, 64.5, fetch=fetch), 'ولسوالی گرمسی، ولایت هلمند')

    def test_unknown_place_is_cached_as_empty(self):
        fetch = FakeFetch({'error': 'Unable to geocode'})
        with neighbours(content=None):
            self.assertEqual(geo.place_name(self.conn, 0.0, -30.0, fetch=fetch), '')
            self.assertEqual(geo.place_name(self.conn, 0.0, -30.0, fetch=fetch), '')
        self.assertEqual(len(fetch.urls), 1)

    def test_offline_nearest_known_place_within_5_km(self):
        near = destination(*FIGMA_POINT, 1.2, 30)
        far = destination(*FIGMA_POINT, 7.0, 200)
        content = fake_content([{'name': 'پارک شهر نو', 'lat': near[0], 'lon': near[1]},
                                {'name': 'دور', 'lat': far[0], 'lon': far[1]}])
        fetch = FakeFetch(OSError('no network'))
        with neighbours(content=content):
            self.assertEqual(geo.place_name(self.conn, *FIGMA_POINT, fetch=fetch), 'نزدیک: پارک شهر نو')
            self.assertEqual(geo.place_name(self.conn, *FIGMA_POINT, lang='ps', fetch=fetch), 'نږدې: پارک شهر نو')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM geo_cache').fetchone()[0], 0)   # offline: not remembered
        fetch_online = FakeFetch(self.reverse)
        self.assertEqual(geo.place_name(self.conn, *FIGMA_POINT, fetch=fetch_online), KABUL_NAME)  # network is back

    def test_offline_known_place_too_far(self):
        far = destination(*FIGMA_POINT, 5.2, 90)
        content = fake_content([{'name': 'دور', 'lat': far[0], 'lon': far[1]}])
        with neighbours(content=content):
            self.assertEqual(geo.place_name(self.conn, *FIGMA_POINT, fetch=FakeFetch(OSError('x'))), '')

    def test_offline_without_content_engine(self):
        with neighbours(content=None):
            self.assertEqual(geo.place_name(self.conn, *FIGMA_POINT, fetch=FakeFetch(OSError('x'))), '')

    def test_content_rows_without_distance(self):
        content = types.ModuleType('server.content')
        content.nearest_places = lambda db, lat, lon, n: [{'name_fa': 'مسجد', 'lat': lat + 0.001, 'lon': lon}]
        with neighbours(content=content):
            self.assertEqual(geo.place_name(self.conn, *FIGMA_POINT, fetch=FakeFetch(OSError('x'))), 'نزدیک: مسجد')

    def test_works_without_init(self):
        bare = TempDB()        # geo.init not called: the table appears by itself
        self.addCleanup(bare.close)
        self.assertEqual(geo.place_name(bare.conn, *FIGMA_POINT, fetch=FakeFetch(self.reverse)), KABUL_NAME)
        self.assertEqual(geo.place_name(bare.conn, *FIGMA_POINT, fetch=FakeFetch(OSError('x'))), KABUL_NAME)

    def test_init_twice(self):
        geo.init(self.conn)
        geo.init(self.conn)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM sqlite_master WHERE name = 'geo_cache'").fetchone()[0],
                         1)

    def test_bad_coordinates(self):
        with self.assertRaises(ApiError):
            geo.place_name(self.conn, 'x', 69, fetch=FakeFetch(self.reverse))

    def test_clean_text(self):
        self.assertEqual(geo.clean_text('ناحیه 4، كابل'), 'ناحیه ۴، کابل')
        self.assertEqual(geo.clean_text('AH7'), 'AH7')            # digits next to Latin letters are left alone
        self.assertEqual(geo.clean_text('کابل ولايت', 'ps'), 'کابل ولايت')   # in Pashto ي is a letter of its own


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class NominatimNetworkTest(unittest.TestCase):
    """fetch_json: User-Agent, timeout, at most 1 request per second, a pause after a network error."""

    def setUp(self):
        geo.reset_network_state()
        self.addCleanup(geo.reset_network_state)
        self.clock = FakeClock()
        patcher = mock.patch.object(geo, 'time', self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.requests = []

    def urlopen(self, request, timeout=None):
        self.requests.append((request, timeout, self.clock.now))
        return FakeResponse(json.dumps({'ok': True}).encode('utf-8'))

    def test_user_agent_and_timeout(self):
        with mock.patch.object(geo.urllib.request, 'urlopen', self.urlopen):
            self.assertEqual(geo.fetch_json('https://nominatim.openstreetmap.org/reverse?x=1'), {'ok': True})
        request, timeout, _ = self.requests[0]
        self.assertEqual(request.get_header('User-agent'), 'PANAH/1.0 (AUAF student project)')
        self.assertEqual(timeout, 3)

    def test_not_more_than_one_request_per_second(self):
        with mock.patch.object(geo.urllib.request, 'urlopen', self.urlopen):
            for _ in range(3):
                geo.fetch_json('https://nominatim.openstreetmap.org/reverse?x=1')
        times = [moment for _, _, moment in self.requests]
        self.assertEqual(len(times), 3)
        self.assertGreaterEqual(times[1] - times[0], 1.0)
        self.assertGreaterEqual(times[2] - times[1], 1.0)

    def test_long_queue_answers_without_network(self):
        geo._next_slot = self.clock.now + 5      # a 5 s queue ahead
        with mock.patch.object(geo.urllib.request, 'urlopen', self.urlopen):
            with self.assertRaises(OSError):
                geo.fetch_json('https://nominatim.openstreetmap.org/reverse?x=1')
        self.assertEqual(self.requests, [])

    def test_pause_after_network_error(self):
        def down(request, timeout=None):
            self.requests.append(request)
            raise urllib.error.URLError('no route')

        with mock.patch.object(geo.urllib.request, 'urlopen', down), self.assertLogs('server.geo', 'WARNING'):
            with self.assertRaises(OSError):
                geo.fetch_json('https://nominatim.openstreetmap.org/reverse?x=1')
            with self.assertRaises(OSError):
                geo.fetch_json('https://nominatim.openstreetmap.org/reverse?x=2')   # pause: network not touched
        self.assertEqual(len(self.requests), 1)
        self.clock.now += geo.OFFLINE_PAUSE_S + 1
        with mock.patch.object(geo.urllib.request, 'urlopen', self.urlopen):
            self.assertEqual(geo.fetch_json('https://nominatim.openstreetmap.org/reverse?x=3'), {'ok': True})

    def test_place_name_offline_does_not_raise(self):
        db = TempDB(geo)
        self.addCleanup(db.close)

        def down(request, timeout=None):
            raise OSError('network is unreachable')

        with mock.patch.object(geo.urllib.request, 'urlopen', down), neighbours(content=None), \
                self.assertLogs('server.geo', 'WARNING'):
            self.assertEqual(geo.place_name(db.conn, *FIGMA_POINT), '')
            self.assertEqual(geo.search_places('کابل'), None)


class HandlerTest(unittest.TestCase):
    def setUp(self):
        geo.reset_network_state()
        self.addCleanup(geo.reset_network_state)
        self.db = TempDB(geo)
        self.addCleanup(self.db.close)

    def test_routes(self):
        self.assertEqual({(m, p) for m, p, _ in geo.routes()},
                         {('GET', '/api/geo/name'), ('GET', '/api/geo/search')})

    def test_name(self):
        fetch = FakeFetch(load_json('nominatim_reverse_kabul.json'))
        with mock.patch.object(geo, 'fetch_json', fetch):
            code, body = geo.handle_name(req(self.db.conn, query={'lat': '34.5550', 'lon': '69.2070'}))
        self.assertEqual(code, 200)
        self.assertEqual(body, {'name': KABUL_NAME, 'lat': 34.555, 'lon': 69.207})

    def test_name_needs_point(self):
        for query in ({}, {'lat': '34.5'}, {'lat': 'x', 'lon': '69'}):
            with self.assertRaises(ApiError) as caught:
                geo.handle_name(req(self.db.conn, query=query))
            self.assertEqual(caught.exception.status, 400)

    def test_search_real_answer(self):
        fetch = FakeFetch(load_json('nominatim_search_kabul_university.json'))
        with mock.patch.object(geo, 'fetch_json', fetch):
            code, body = geo.handle_search(req(self.db.conn, query={'q': ' پوهنتون   کابل '}))
        self.assertEqual(code, 200)
        self.assertFalse(body['offline'])
        self.assertEqual(len(body['results']), 3)          # the saved Nominatim response has 3 places
        first = body['results'][0]
        self.assertEqual(set(first), {'name', 'lat', 'lon', 'full'})
        names = [item['name'] for item in body['results']]
        self.assertIn('دانشگاه کابل، ناحیه سوم، کابل ښار', names)
        for item in body['results']:
            self.assertTrue(29 < item['lat'] < 39 and 60 < item['lon'] < 75, item)
            self.assertNotIn(',', item['full'])                       # Dari comma, no postcode
            self.assertTrue(item['full'].endswith('افغانستان'), item['full'])
        self.assertNotIn('۱۰۱۰', body['results'][0]['full'])
        q = query_of(fetch.urls[0])
        self.assertEqual((q['q'], q['countrycodes'], q['limit'], q['accept-language'], q['format']),
                         ('پوهنتون کابل', 'af', '5', 'fa', 'jsonv2'))

    def test_search_needs_text(self):
        for query in ({}, {'q': '   '}, {'q': 'x' * 201}):
            with self.assertRaises(ApiError) as caught:
                geo.handle_search(req(self.db.conn, query=query))
            self.assertEqual(caught.exception.status, 400)

    def test_search_offline(self):
        with mock.patch.object(geo, 'fetch_json', FakeFetch(OSError('offline'))):
            self.assertEqual(geo.handle_search(req(self.db.conn, query={'q': 'کابل'})),
                             (200, {'results': [], 'offline': True}))


if __name__ == '__main__':
    unittest.main()
