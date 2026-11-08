"""Tests for the content engine: seed data without duplicates, admin CRUD and refusal without admin, distances and
sorting of places, search (Dari, Pashto, case, ZWNJ, Dari digits), video links."""
import json
import math
import sys
import tempfile
import types
import unittest
from pathlib import Path

import support  # noqa: F401 — adds app/ to sys.path

import server
from server import content, store
from server.common import ApiError

FIGMA_17 = ['چگونه در هنگام سیلاب پناه بگیریم', 'چگونه زلزله را تشخیص دهیم', 'کمک‌های اولیه در حوادث',
            'آمادگی در برابر آتش‌سوزی']
KABUL_UNI = (34.5154398, 69.1338400)

try:  # the real geo, to check number parsing against alerts; in the other tests geo is faked
    from server import geo as REAL_GEO
except ImportError:  # pragma: no cover
    REAL_GEO = None


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
        sys.modules[full] = module
        if module is None:
            if hasattr(server, name):
                delattr(server, name)
        else:
            setattr(server, name, module)


def haversine(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def fake_alerts(result=None, fail=False):
    mod = types.ModuleType('fake_alerts')
    mod.calls = []

    def search(db, q):
        mod.calls.append(q)
        if fail:
            raise RuntimeError('boom')
        return result or []

    mod.search = search
    return mod


def make_req(db, body=None, query=None, params=None, user=None, admin=None):
    return types.SimpleNamespace(db=db, body=body if body is not None else {}, query=query or {},
                                 params=params or {}, user=user, admin=admin, ip='127.0.0.1',
                                 device_id='dev-test-0000000001', app=None)


class ContentTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.db = store.connect(Path(self._tmp.name) / 'panah.db')
        self.addCleanup(self.db.close)
        content.init(self.db)
        swap(self, auth=None, geo=None, alerts=None)
        self.admin = {'id': 1, 'username': 'admin'}

    def call(self, handler, expect=200, **kw):
        status, data = handler(make_req(self.db, **kw))
        self.assertEqual(status, expect)
        json.dumps(data, ensure_ascii=False)
        return data

    def error(self, handler, **kw):
        with self.assertRaises(ApiError) as caught:
            handler(make_req(self.db, **kw))
        return caught.exception

    def count(self, table):
        return self.db.execute('SELECT count(*) FROM %s' % table).fetchone()[0]

    # --- seed data
    def test_seed_once(self):
        before = (self.count('numbers'), self.count('videos'), self.count('safe_places'))
        content.init(self.db)
        content.init(self.db)
        self.assertEqual((self.count('numbers'), self.count('videos'), self.count('safe_places')), before)
        numbers = self.call(content.get_numbers)['numbers']
        self.assertEqual([(n['number'], n['title']) for n in numbers][:2], [('119', 'پولیس'), ('102', 'آمبولانس')])
        self.assertTrue(all(n['title_ps'] for n in numbers))
        videos = self.call(content.get_videos)['videos']
        self.assertEqual([v['title'] for v in videos], FIGMA_17)  # order as in figma 17
        self.assertEqual(before[2], len(content.SEED_PLACES) + len(content.SEED_PLACES_CITIES))
        self.assertEqual([n['number'] for n in numbers], ['119', '102', '100'])  # no 112 since v3

    def test_seed_data_is_sane(self):
        for video in self.call(content.get_videos)['videos']:
            self.assertTrue(video['title_ps'])
            self.assertEqual(video['kind'], 'youtube')
            if video['url']:
                self.assertRegex(video['url'], r'^https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}$')
                self.assertEqual(video['url'][-11:], video['youtube_id'])
                self.assertTrue(video['source'])
        places = self.call(content.get_places)['places']
        self.assertEqual(len({p['name'] for p in places}), len(places))
        kabul = {row[0] for row in content.SEED_PLACES}
        for place in places:
            self.assertTrue(29.3 < place['lat'] < 38.5 and 60.5 < place['lon'] < 75.0, place)  # Afghanistan
            if place['name'] in kabul:
                self.assertTrue(34.40 < place['lat'] < 34.65 and 69.0 < place['lon'] < 69.35, place)  # Kabul
            self.assertIn(place['kind'], content.PLACE_KINDS)
            self.assertEqual(place['source'], 'example')
            self.assertTrue(place['name_ps'] and place['address'])
            self.assertIsNone(place['distance_km'])

    def test_places_outside_kabul(self):
        cities = ['هرات', 'مزار شریف', 'جلال‌آباد', 'قندهار', 'قندوز', 'فیض‌آباد', 'بامیان', 'خوست']
        for city in cities:
            found = [row for row in content.SEED_PLACES_CITIES if city in row[5]]
            self.assertTrue(1 <= len(found) <= 2, city)
        herat = content.nearest_places(self.db, 34.3482, 62.1997, 2)  # Herat city centre
        self.assertEqual({p['name'] for p in herat}, {'پوهنتون هرات', 'ورزشگاه هرات'})
        self.assertLess(herat[-1]['distance_km'], 5)
        self.assertEqual([p['name'] for p in self.search('خوست')['places']], ['پوهنتون خوست'])

    def test_older_database_upgrade(self):
        """A database made by v2: the untouched 112 goes away once, places outside Kabul arrive once."""
        for edited in (False, True):
            db = store.connect(Path(self._tmp.name) / ('v2-%s.db' % edited))
            self.addCleanup(db.close)
            db.executescript(content.SCHEMA)
            v2_numbers = [('119', 'پولیس', 'پولیس', 1), ('102', 'آمبولانس', 'امبولانس', 2),
                          ('100', 'پولیس (شمارهٔ دوم)', 'پولیس (دوهمه شمېره)', 3), content.V2_FIRE_112]
            if edited:  # the admin renamed it: it is his row now and stays
                v2_numbers[3] = ('112', 'آتش‌نشانی کابل', 'اور وژونکي', 4)
            db.executemany('INSERT INTO numbers (number, title, title_ps, sort) VALUES (?, ?, ?, ?)', v2_numbers)
            db.executemany('INSERT INTO safe_places (name, name_ps, kind, lat, lon, address, source)'
                           ' VALUES (?, ?, ?, ?, ?, ?, ?)', [row + ('example',) for row in content.SEED_PLACES])
            db.executemany('INSERT INTO content_seeded (name, at) VALUES (?, ?)',
                           [(name, '2026-11-06T01:00:00Z') for name in ('numbers', 'videos', 'places')])
            db.commit()
            for _ in range(2):  # the second start changes nothing
                content.init(db)
                numbers = [r['number'] for r in db.execute('SELECT number FROM numbers ORDER BY sort')]
                self.assertEqual(numbers, ['119', '102', '100', '112'] if edited else ['119', '102', '100'])
                places = db.execute('SELECT count(*) FROM safe_places').fetchone()[0]
                self.assertEqual(places, len(content.SEED_PLACES) + len(content.SEED_PLACES_CITIES))
            self.assertEqual(db.execute('SELECT count(*) FROM videos').fetchone()[0], 0)  # v2 marks are respected

    def test_new_112_from_admin_is_not_touched(self):
        made = self.call(content.add_number, admin=self.admin, body={'number': '112', 'title': 'آتش‌نشانی'})['number']
        content.init(self.db)
        self.assertIn(made['id'], [n['id'] for n in self.call(content.get_numbers)['numbers']])

    def test_deleted_seed_does_not_come_back(self):
        first = self.call(content.get_numbers)['numbers'][0]
        self.call(content.delete_number, params={'id': str(first['id'])}, admin=self.admin)
        content.init(self.db)
        self.assertNotIn(first['id'], [n['id'] for n in self.call(content.get_numbers)['numbers']])

    # --- numbers
    def test_numbers_crud(self):
        made = self.call(content.add_number, admin=self.admin,
                         body={'number': '۰۲۰ ۲۱۰-۱۲۳۴', 'title': 'صلیب سرخ', 'title_ps': 'سره صلیب'})['number']
        self.assertEqual(made['number'], '0202101234')
        self.assertEqual(made['sort'], self.count('numbers'))  # at the end of the list
        self.assertEqual(self.call(content.get_numbers)['numbers'][-1]['id'], made['id'])
        first = self.call(content.add_number, admin=self.admin,
                          body={'number': '+93 799 000 000', 'title': 'اول', 'sort': 0})['number']
        self.assertEqual(self.call(content.get_numbers)['numbers'][0]['id'], first['id'])
        edited = self.call(content.edit_number, admin=self.admin, params={'id': str(made['id'])},
                           body={'title': 'هلال احمر'})['number']
        self.assertEqual((edited['title'], edited['number'], edited['title_ps']), ('هلال احمر', '0202101234', 'سره صلیب'))
        for bad in ({'number': 'abc', 'title': 'x'}, {'number': '1', 'title': 'x'}, {'number': '119', 'title': ' '},
                    {'number': '119'}, {'number': '119', 'title': 'x', 'sort': 'z'}):
            self.assertEqual(self.error(content.add_number, admin=self.admin, body=bad).status, 400, bad)
        self.assertEqual(self.error(content.edit_number, admin=self.admin, params={'id': str(made['id'])},
                                    body={'number': '12a'}).status, 400)
        self.assertEqual(self.call(content.delete_number, admin=self.admin, params={'id': str(made['id'])}),
                         {'ok': True, 'id': made['id']})
        for handler in (content.edit_number, content.delete_number):
            self.assertEqual(self.error(handler, admin=self.admin, params={'id': str(made['id'])},
                                        body={'title': 'x'}).status, 404)
            self.assertEqual(self.error(handler, admin=self.admin, params={'id': 'x'}, body={}).status, 404)

    def test_admin_only(self):
        handlers = [(content.add_number, {}), (content.edit_number, {'id': '1'}), (content.delete_number, {'id': '1'}),
                    (content.add_video, {}), (content.edit_video, {'id': '1'}), (content.delete_video, {'id': '1'}),
                    (content.add_place, {}), (content.edit_place, {'id': '1'}), (content.delete_place, {'id': '1'})]
        for handler, params in handlers:
            self.assertEqual(self.error(handler, params=params, body={'title': 'x'}).status, 403, handler)
            self.assertEqual(self.error(handler, params=params, user={'id': 5}, body={'title': 'x'}).status, 403)
        self.assertEqual(self.count('numbers'), len(content.SEED_NUMBERS))
        # reading: for everyone, no login
        for handler in (content.get_numbers, content.get_videos, content.get_places):
            self.call(handler)

    # --- videos
    def test_video_links(self):
        yid = 'dQw4w9WgXcQ'
        for url in ('https://youtu.be/%s?t=10' % yid, 'https://www.youtube.com/watch?v=%s&list=PL1' % yid,
                    'https://m.youtube.com/watch?v=%s' % yid, 'https://www.youtube.com/embed/%s' % yid,
                    'https://youtube.com/shorts/%s' % yid, 'https://www.youtube-nocookie.com/embed/%s' % yid,
                    'https://www.youtube.com/live/%s' % yid, 'youtube.com/watch?v=%s' % yid,
                    'youtu.be/%s' % yid):
            self.assertEqual(content.video_link(url), ('https://www.youtube.com/watch?v=' + yid, 'youtube'), url)
        self.assertEqual(content.video_link('/uploads/flood.mp4'), ('/uploads/flood.mp4', 'file'))
        self.assertEqual(content.video_link('https://example.org/v/fire.WEBM'), ('https://example.org/v/fire.WEBM',
                                                                                 'file'))
        for bad in ('', '   ', 'https://www.youtube.com/watch?v=short', 'https://www.youtube.com/channel/UC1',
                    'https://example.org/page', 'javascript:alert(1)', '//evil.example/x.mp4', 'ftp://x/y.mp4',
                    '/uploads/../secret.mp4', 'https://youtu.be/', 'x' * 600):
            with self.assertRaises(ApiError, msg=bad) as caught:
                content.video_link(bad)
            self.assertEqual(caught.exception.status, 400)

    def test_videos_crud(self):
        made = self.call(content.add_video, admin=self.admin,
                         body={'title': 'First Aid Basics', 'title_ps': 'لومړنۍ مرسته',
                               'url': 'https://youtu.be/dQw4w9WgXcQ'})['video']
        self.assertEqual((made['kind'], made['youtube_id'], made['source']), ('youtube', 'dQw4w9WgXcQ', 'admin'))
        self.assertEqual(self.call(content.get_videos)['videos'][0]['id'], made['id'])  # newest on top
        edited = self.call(content.edit_video, admin=self.admin, params={'id': str(made['id'])},
                           body={'url': '/uploads/aid.mp4'})['video']
        self.assertEqual((edited['kind'], edited['youtube_id'], edited['title']), ('file', None, 'First Aid Basics'))
        self.assertEqual(self.error(content.add_video, admin=self.admin, body={'title': 'x'}).status, 400)
        self.assertEqual(self.error(content.add_video, admin=self.admin,
                                    body={'url': 'https://youtu.be/dQw4w9WgXcQ'}).status, 400)
        self.assertEqual(self.error(content.edit_video, admin=self.admin, params={'id': str(made['id'])},
                                    body={'url': 'https://example.org/'}).status, 400)
        self.call(content.delete_video, admin=self.admin, params={'id': str(made['id'])})
        self.assertEqual(self.count('videos'), len(content.SEED_VIDEOS))

    # --- places
    def test_places_crud(self):
        made = self.call(content.add_place, admin=self.admin,
                         body={'name': 'مسجد جامع', 'kind': 'shelter', 'lat': '34.52', 'lon': 69.17,
                               'address': 'کابل'})['place']
        self.assertEqual((made['source'], made['kind'], made['lat'], made['name_ps']), ('admin', 'shelter', 34.52, ''))
        moved = self.call(content.edit_place, admin=self.admin, params={'id': str(made['id'])},
                          body={'lat': 34.53})['place']
        self.assertEqual((moved['lat'], moved['lon']), (34.53, 69.17))
        for bad in ({'name': 'x', 'lat': 34.5}, {'name': 'x', 'lat': 95, 'lon': 69}, {'lat': 34.5, 'lon': 69.1},
                    {'name': 'x', 'lat': 34.5, 'lon': 69.1, 'kind': 'mall'}, {'name': 'x', 'lat': 'a', 'lon': 'b'}):
            self.assertEqual(self.error(content.add_place, admin=self.admin, body=bad).status, 400, bad)
        self.call(content.delete_place, admin=self.admin, params={'id': str(made['id'])})
        self.assertEqual(self.error(content.delete_place, admin=self.admin, params={'id': str(made['id'])}).status, 404)

    def test_places_by_distance(self):
        places = self.call(content.get_places, query={'lat': str(KABUL_UNI[0]), 'lon': str(KABUL_UNI[1])})['places']
        self.assertEqual(places[0]['name'], 'پوهنتون کابل')
        self.assertEqual(places[0]['distance_km'], 0)
        distances = [p['distance_km'] for p in places]
        self.assertEqual(distances, sorted(distances))
        for place in places:
            self.assertAlmostEqual(place['distance_km'], haversine(KABUL_UNI[0], KABUL_UNI[1], place['lat'],
                                                                   place['lon']), places=2)
        self.assertEqual(len(self.call(content.get_places, query={'lat': '34.5', 'lon': '69.2', 'limit': '3'})
                             ['places']), 3)
        for bad in ({'lat': '34.5'}, {'lat': 'x', 'lon': '69'}, {'lat': '34.5', 'lon': '200'},
                    {'lat': '34.5', 'lon': '69', 'limit': 'many'}):
            self.assertEqual(self.error(content.get_places, query=bad).status, 400, bad)

    def test_dari_digits_and_decimal_sign(self):
        dari = {'lat': '۳۴٫۵۱۵۴۳۹۸', 'lon': '۶۹٫۱۳۳۸۴'}  # as typed on a Dari keyboard
        self.assertEqual(self.call(content.get_places, query=dari)['places'][0]['name'], 'پوهنتون کابل')
        made = self.call(content.add_place, admin=self.admin,
                         body={'name': 'مکتب', 'lat': '۳۴٫۵۲', 'lon': '۶۹٫۱۷'})['place']
        self.assertEqual((made['lat'], made['lon']), (34.52, 69.17))
        edited = self.call(content.edit_place, admin=self.admin, params={'id': str(made['id'])},
                           body={'lon': '٦٩٫١٨'})['place']  # Arabic digits
        self.assertEqual(edited['lon'], 69.18)
        self.assertEqual(content.nearest_places(self.db, '۳۴٫۵۳۳۱۴۳۳', '۶۹٫۱۶۸۴۳۸۴', 1)[0]['name'], 'پارک شهر نو')
        self.assertTrue(self.search('پارک', **dari)['places'][0]['distance_km'] is not None)

    def test_points_parsed_like_alerts(self):
        if REAL_GEO is None or not hasattr(REAL_GEO, 'parse_point'):
            self.skipTest('geo not available yet')
        swap(self, geo=REAL_GEO)
        for lat, lon in (('۳۴٫۵', '۶۹٫۲'), (' 34.5 ', 69.2), ('٣٤٫٥', '٦٩٫٢')):
            self.assertEqual(content._point(lat, lon), REAL_GEO.parse_point(lat, lon))
        for bad in (('x', '69'), ('34.5', ''), ('95', '69')):
            with self.assertRaises(ApiError):
                REAL_GEO.parse_point(*bad)
            self.assertEqual(self.error(content.get_places, query={'lat': bad[0], 'lon': bad[1]}).status, 400, bad)
        self.assertEqual(content._point('', None), (None, None))
        self.assertEqual(self.error(content.add_place, admin=self.admin, body={'name': 'x'}).status, 400)

    def test_nearest_places(self):
        found = content.nearest_places(self.db, 34.5331433, 69.1684384, 2)
        self.assertEqual([p['name'] for p in found][0], 'پارک شهر نو')
        self.assertEqual(len(found), 2)
        self.assertLessEqual(found[0]['distance_km'], found[1]['distance_km'])
        self.assertEqual(len(content.nearest_places(self.db, '34.5', '69.2')), 3)  # n defaults to 3
        self.assertEqual(content.nearest_places(self.db, 34.5, 69.2, 0), [])
        empty = store.connect(':memory:')
        self.addCleanup(empty.close)
        self.assertEqual(content.nearest_places(empty, 34.5, 69.2, 3), [])  # content.init not called

    def test_same_distance_with_real_geo_formula(self):
        geo = types.ModuleType('fake_geo')
        geo.distance_km = lambda *a: 1.5
        swap(self, geo=geo)
        self.assertEqual({p['distance_km'] for p in content.nearest_places(self.db, 34.5, 69.2, 5)}, {1.5})

    # --- search
    def search(self, q, **query):
        query['q'] = q
        return self.call(content.search, query=query)

    def titles(self, q):
        return [v['title'] for v in self.search(q)['videos']]

    def test_search_dari_pashto_and_spelling(self):
        self.assertEqual(self.titles('سیلاب'), [FIGMA_17[0]])
        self.assertEqual(self.titles('سيلاب'), [FIGMA_17[0]])  # Arabic ي
        self.assertEqual(self.titles('زلزله څنګه'), [FIGMA_17[1]])  # Pashto
        self.assertEqual(self.titles('کمکهای'), [FIGMA_17[2]])  # without ZWNJ
        self.assertEqual(self.titles('کمک های اولیه'), [FIGMA_17[2]])  # space instead of ZWNJ
        self.assertEqual(self.titles('آتش سوزی'), [FIGMA_17[3]])
        self.assertEqual([n['number'] for n in self.search('۱۱۹')['numbers']], ['119'])
        self.assertEqual({n['number'] for n in self.search('پولیس')['numbers']}, {'119', '100'})
        self.assertIn('پارک شهر نو', [p['name'] for p in self.search('پارك')['places']])  # Arabic ك
        self.assertIn('پوهنتون کابل', [p['name'] for p in self.search('کابل پوهنتون')['places']])  # Pashto
        self.assertEqual(self.search('nothing-like-this'), {'q': 'nothing-like-this', 'alerts': [], 'videos': [],
                                                            'numbers': [], 'places': []})

    def test_search_by_word_stem(self):
        for q in ('زلزلې', 'زلزلو', 'زلزله'):  # Pashto: a different ending, the same stem
            self.assertEqual(self.titles(q), [FIGMA_17[1]], q)
        for q in ('سیلابونه', 'سیلاب‌ها', 'سیلاب ها', 'سېلاب'):
            self.assertEqual(self.titles(q), [FIGMA_17[0]], q)
        self.assertEqual(self.titles('کمک‌های اولیه'), [FIGMA_17[2]])
        # all words in one item; function words ("در") do not get in the way
        self.assertEqual([p['name'] for p in self.search('پارک در ناحیه چهارم')['places']], ['پارک شهر نو'])
        self.assertEqual(self.search('زلزله کابل')['videos'], [])
        self.assertEqual(content.search_terms('سیلاب در کابل'), ['سیلاب', 'کابل'])
        self.assertEqual(content.word_stem(content.search_text('زلزلې')), 'زلزل')

    def test_search_ignores_latin_diacritics(self):
        self.call(content.add_place, admin=self.admin, body={'name': 'Farkhār', 'lat': 36.57, 'lon': 69.86,
                                                             'address': 'Takhār'})
        for q in ('farkhar', 'FARKHĀR', 'Farkhar takhar'):
            self.assertEqual([p['name'] for p in self.search(q)['places']], ['Farkhār'], q)
        self.assertEqual(content.search_text('Farkhār Çelik'), 'farkharcelik')

    def test_search_ignores_case(self):
        self.call(content.add_video, admin=self.admin, body={'title': 'First Aid Basics', 'url': '/uploads/a.mp4'})
        self.assertEqual(self.titles('first AID'), ['First Aid Basics'])

    def test_search_places_with_distance(self):
        places = self.search('پارک', lat=str(KABUL_UNI[0]), lon=str(KABUL_UNI[1]))['places']
        self.assertGreaterEqual(len(places), 3)
        self.assertEqual([p['distance_km'] for p in places], sorted(p['distance_km'] for p in places))

    def test_search_alerts(self):
        alerts = fake_alerts([{'id': 1, 'type': 'flood'}])
        swap(self, alerts=alerts)
        self.assertEqual(self.search(' سیلاب ')['alerts'], [{'id': 1, 'type': 'flood'}])
        self.assertEqual(alerts.calls, ['سیلاب'])
        self.assertEqual(self.search('   ')['alerts'], [])
        self.assertEqual(alerts.calls, ['سیلاب'])  # empty query: alerts not called
        swap(self, alerts=fake_alerts(fail=True))
        with self.assertLogs('server.content', level='ERROR'):  # an alerts failure goes to the log, search does not fail
            self.assertEqual(self.search('سیلاب')['alerts'], [])
        swap(self, alerts=None)
        self.assertEqual(self.search('سیلاب')['alerts'], [])

    def test_search_text(self):
        self.assertEqual(content.search_text('کمک‌های  اولیه'), content.search_text('كمكهاي اوليه'))
        self.assertEqual(content.search_text('۱۲۳ ٤٥'), '12345')
        self.assertEqual(content.search_text('FLOOD'), 'flood')

    def test_routes(self):
        found = {(method, path) for method, path, _ in content.routes()}
        for item in (('GET', '/api/numbers'), ('GET', '/api/videos'), ('GET', '/api/places'), ('GET', '/api/search'),
                     ('POST', '/api/admin/numbers'), ('PUT', '/api/admin/numbers/{id}'),
                     ('DELETE', '/api/admin/numbers/{id}'), ('POST', '/api/admin/videos'),
                     ('PUT', '/api/admin/videos/{id}'), ('DELETE', '/api/admin/videos/{id}'),
                     ('POST', '/api/admin/places'), ('PUT', '/api/admin/places/{id}'),
                     ('DELETE', '/api/admin/places/{id}')):
            self.assertIn(item, found)


if __name__ == '__main__':
    unittest.main()
