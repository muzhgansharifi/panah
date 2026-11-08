"""Test helpers for geo, alerts, usgs: a temporary database, residents at a given distance, fake neighbour engines.

    from alerts_support import TempDB, neighbours, fake_sms, fake_live, fake_content, destination
    with neighbours(sms=fake_sms(), live=fake_live(), content=None):   # None: the engine "does not exist yet"
        ...

Importing it adds app/ to sys.path; after that `from server import ...` works.
"""
import json
import math
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from server import geo, store  # noqa: E402

DATA = Path(__file__).resolve().parent / 'data'
KABUL = (34.5553, 69.2075)


def load_json(name):
    return json.loads((DATA / name).read_text(encoding='utf-8'))


def destination(lat, lon, km, bearing_deg):
    """Point km away from (lat, lon) along a great circle at bearing bearing_deg (0 = north, 90 = east)."""
    delta = km / geo.EARTH_RADIUS_KM
    theta = math.radians(bearing_deg)
    phi1, lam1 = math.radians(lat), math.radians(lon)
    phi2 = math.asin(math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(theta))
    lam2 = lam1 + math.atan2(math.sin(theta) * math.sin(delta) * math.cos(phi1),
                             math.cos(delta) - math.sin(phi1) * math.sin(phi2))
    return math.degrees(phi2), math.degrees(lam2)


class TempDB:
    """Temporary folder with a database: db = TempDB(); db.conn …; db.close() (close before deleting, Windows needs it)."""

    def __init__(self, *engines):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self._tmp.name) / 'panah.db'
        self.conn = store.connect(self.path)
        for engine in engines:
            engine.init(self.conn)
        self.conn.commit()
        self._phones = 0

    def add_user(self, last=None, home=None, name='', phone=None):
        self._phones += 1
        phone = phone or '+93700%06d' % self._phones
        last = last or (None, None)
        home = home or (None, None)
        cur = self.conn.execute('INSERT INTO users (phone, name, home_lat, home_lon, last_lat, last_lon) '
                                'VALUES (?, ?, ?, ?, ?, ?)', (phone, name, home[0], home[1], last[0], last[1]))
        return cur.lastrowid

    def user(self, user_id):
        return store.get_user(self.conn, user_id)

    def close(self):
        try:
            self.conn.close()
        finally:
            self._tmp.cleanup()


def req(db, user=None, admin=None, body=None, query=None, params=None):
    """A simple request for a handler, the way web passes it."""
    return types.SimpleNamespace(db=db, user=user, admin=admin, body=body or {}, query=query or {},
                                 params=params or {}, ip='127.0.0.1', device_id='test-device', app=None)


def neighbours(**modules):
    """Swap neighbour engines server.<name> for the duration of the with block; None = the engine is missing (ModuleNotFoundError)."""
    return mock.patch.dict(sys.modules, {'server.' + name: module for name, module in modules.items()})


def fake_sms(fail_for=()):
    mod = types.ModuleType('server.sms')
    mod.sent = []

    def send(db, phone, text, kind='other'):
        if phone in fail_for:
            raise RuntimeError('provider down')
        db.execute('SELECT 1').fetchone()
        mod.sent.append({'phone': phone, 'text': text, 'kind': kind})
        return 1000 + len(mod.sent)

    mod.send = send
    mod.stats = lambda db: {'total': len(mod.sent), 'test_mode': True}
    return mod


def fake_live():
    mod = types.ModuleType('server.live')
    mod.events = []

    def publish(event, data, user_id=None, device_id=None, admins=False, everyone=False):
        json.dumps(data, ensure_ascii=False)   # must go to the browser as JSON
        mod.events.append({'event': event, 'data': data, 'user_id': user_id, 'device_id': device_id,
                           'admins': admins, 'everyone': everyone})
        return 1

    mod.publish = publish
    return mod


def fake_content(places):
    """content.nearest_places over a list of places [{name, lat, lon}]: with distance_km, nearest first."""
    mod = types.ModuleType('server.content')
    mod.calls = []

    def nearest_places(db, lat, lon, n=3):
        mod.calls.append((lat, lon, n))
        found = [dict(place, distance_km=round(geo.distance_km(lat, lon, place['lat'], place['lon']), 3))
                 for place in places]
        found.sort(key=lambda place: place['distance_km'])
        return found[:n]

    mod.nearest_places = nearest_places
    return mod


def fake_sos():
    mod = types.ModuleType('server.sos')
    mod.stats = lambda db: {'open': 2, 'today': 3}
    return mod


class FakeFetch:
    """Fake network: fetch(url) → the answers in turn (or one for all); an exception among the answers is raised."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        answer = self.answers[0] if len(self.answers) == 1 else self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer
