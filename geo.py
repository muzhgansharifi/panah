"""geo — PANAH distances and place names.

distance_km(lat1, lon1, lat2, lon2) → km along the Earth's surface (haversine, R = 6371.0088 km).
place_name(db, lat, lon, lang='fa') → a short address "place، district، city", for example
    "جاده میدان هوایی، ناحیه دهم، کابل ښار":
    Nominatim reverse (OpenStreetMap) with its own User-Agent, at most 1 request per second, 3 s timeout;
    the answer is cached in the geo_cache table (coordinates rounded to 4 decimals, ≈ 11 m);
    without network: "نزدیک: <place>", the nearest place from content.nearest_places within 5 km, otherwise ''.
    lang: 'fa' (Dari, the default), 'ps' (Pashto), 'en'.
search_places(q, lang='fa') → [{name, lat, lon, full}] — Nominatim search, Afghanistan only, up to 5;
    without network → None.
parse_point(lat, lon) → (lat, lon) from query strings, or ApiError 400.

Routes:
    GET /api/geo/name?lat=&lon=[&lang=ps]  → {name, lat, lon}
    GET /api/geo/search?q=[&lang=ps]       → {results: [{name, lat, lon, full}], offline}
Nothing breaks without network: after a network error Nominatim is left alone for OFFLINE_PAUSE_S seconds.
Network before the database write: place_name writes to the cache only after Nominatim has answered.
"""
import importlib
import json
import logging
import math
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request

from server.common import ApiError, utcnow

log = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0088          # mean Earth radius (IUGG)
NOMINATIM_URL = 'https://nominatim.openstreetmap.org'
USER_AGENT = 'PANAH/1.0 (AUAF student project)'
TIMEOUT_S = 3
MIN_INTERVAL_S = 1.0                 # Nominatim rule: at most 1 request per second
MAX_QUEUE_S = 2.0                    # our turn is further away than this: do not wait, answer without network
OFFLINE_PAUSE_S = 60                 # after a network error, do not try for this many seconds
REVERSE_ZOOM = 16                    # street level
NEAR_KM = 5.0                        # "نزدیک: …" only if a known place is no farther than this
SEARCH_LIMIT = 5
SEARCH_CACHE_S = 3600
SEARCH_CACHE_SIZE = 256
MAX_QUERY = 200

LANGS = {'fa': 'fa', 'ps': 'ps,fa', 'en': 'en'}           # our language → accept-language
NEAR_WORD = {'fa': 'نزدیک', 'ps': 'نږدې', 'en': 'Near'}

MSG = {
    'location_required': 'موقعیت را مشخص کنید.',
    'bad_location': 'موقعیت درست نیست.',
    'q_required': 'چیزی برای جستجو بنویسید.',
    'q_too_long': 'متن جستجو خیلی طولانی است.',
}

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS geo_cache (
        key        TEXT PRIMARY KEY,   -- 'fa:34.5550,69.2070'
        lat        REAL NOT NULL,      -- rounded to 4 decimals
        lon        REAL NOT NULL,
        lang       TEXT NOT NULL,
        name       TEXT NOT NULL,      -- '': Nominatim answered but does not know the place
        created_at TEXT NOT NULL
    )""",
)

# Order of Nominatim address parts: what to take first, second (district) and third (city).
_FIRST = ('amenity', 'building', 'shop', 'tourism', 'leisure', 'office', 'historic', 'man_made', 'road',
          'pedestrian', 'neighbourhood', 'quarter', 'hamlet')
_DISTRICT = ('suburb', 'city_district', 'district', 'borough', 'quarter', 'neighbourhood')
_CITY = ('city', 'town', 'village', 'municipality', 'county', 'state')

_FA_DIGITS = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
_LATIN = re.compile(r'[A-Za-z]')


def init(db):
    """Address cache table (calling again changes nothing)."""
    for statement in SCHEMA:
        db.execute(statement)


def routes():
    return [
        ('GET', '/api/geo/name', handle_name),
        ('GET', '/api/geo/search', handle_search),
    ]


# ---------------------------------------------------------------- distance and points

def distance_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km (haversine)."""
    phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
    d_phi = phi2 - phi1
    d_lambda = math.radians(float(lon2) - float(lon1))
    h = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def _empty(value):
    return value is None or (isinstance(value, str) and not value.strip())


def parse_point(lat, lon, required=True):
    """Latitude and longitude (numbers or strings, Dari digits allowed) → (lat, lon).

    Both empty → ApiError 400 (or None if required=False); invalid or out of range → ApiError 400.
    """
    if _empty(lat) and _empty(lon):
        if required:
            raise ApiError(400, 'location_required', MSG['location_required'])
        return None
    try:
        if isinstance(lat, bool) or isinstance(lon, bool):
            raise ValueError('bool')
        values = [float(str(v).strip().replace('٫', '.')) if isinstance(v, str) else float(v) for v in (lat, lon)]
    except (TypeError, ValueError):
        raise ApiError(400, 'bad_location', MSG['bad_location']) from None
    la, lo = values
    if not (math.isfinite(la) and math.isfinite(lo) and -90 <= la <= 90 and -180 <= lo <= 180):
        raise ApiError(400, 'bad_location', MSG['bad_location'])
    return la, lo


def _round4(value):
    value = round(float(value), 4)
    return 0.0 if value == 0 else value   # no '-0.0000'


def _cache_key(lat, lon, lang):
    return '%s:%.4f,%.4f' % (lang, lat, lon)


def _lang(value):
    value = str(value or 'fa').strip().lower()
    return value if value in LANGS else 'fa'


# ---------------------------------------------------------------- network: Nominatim

_lock = threading.Lock()
_next_slot = 0.0        # time.monotonic() when the next request is allowed
_offline_until = 0.0    # do not try the network until this time
_search_cache = {}      # (lang, query) → (when, results)


def _take_slot():
    """Queue for Nominatim: requests at most once per MIN_INTERVAL_S.

    → how many seconds to wait before the request, or None: no network (it failed recently or the queue is long).
    """
    global _next_slot
    with _lock:
        now = time.monotonic()
        if now < _offline_until:
            return None
        start = max(now, _next_slot)
        if start - now > MAX_QUEUE_S:
            return None
        _next_slot = start + MIN_INTERVAL_S
        return start - now


def _mark_offline(reason):
    global _offline_until
    with _lock:
        _offline_until = time.monotonic() + OFFLINE_PAUSE_S
    log.warning('geo: Nominatim unreachable (%s); next try in %d s', reason, OFFLINE_PAUSE_S)


def reset_network_state():
    """Forget the queue, the offline pause and the search cache (for tests and after a network change)."""
    global _next_slot, _offline_until
    with _lock:
        _next_slot = 0.0
        _offline_until = 0.0
        _search_cache.clear()


def fetch_json(url, timeout=TIMEOUT_S):
    """GET to Nominatim → parsed JSON. Per Nominatim rules: own User-Agent, at most 1 request per second.

    No network, an error response, the pause after an error or a long queue → OSError.
    """
    wait = _take_slot()
    if wait is None:
        raise OSError('Nominatim paused: offline recently or too many requests')
    if wait > 0:
        time.sleep(wait)
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = resp.read(2_000_000)
        return json.loads(body.decode('utf-8'))
    except Exception as exc:   # URLError, HTTPError (429, 403…), timeout, dropped connection, not JSON
        _mark_offline('%s: %s' % (type(exc).__name__, exc))
        raise OSError('Nominatim request failed: %s' % (exc,)) from exc


def _as_json(data):
    if isinstance(data, (bytes, bytearray)):
        data = data.decode('utf-8')
    if isinstance(data, str):
        data = json.loads(data)
    return data


# ---------------------------------------------------------------- address from a Nominatim answer

def clean_text(value, lang='fa'):
    """OpenStreetMap string → for the screen: ك → ک; for Dari ي/ى → ی; Dari digits if the part has no Latin letters."""
    text = ' '.join(str(value or '').split())
    if not text:
        return ''
    text = text.replace('ك', 'ک')
    if lang == 'fa':
        text = text.replace('ي', 'ی').replace('ى', 'ی')
    if lang in ('fa', 'ps') and not _LATIN.search(text):
        text = text.translate(_FA_DIGITS)
    return text


def _same(a, b):
    return clean_text(a, 'fa').replace('\u200c', '') == clean_text(b, 'fa').replace('\u200c', '')


def format_address(item, lang='fa'):
    """Nominatim answer (jsonv2) → "place، district، city" (up to 3 parts, no repeats); nothing → ''."""
    if not isinstance(item, dict):
        return ''
    address = item.get('address') if isinstance(item.get('address'), dict) else {}
    parts = []

    def add(value):
        value = clean_text(value, lang)
        if value and not any(_same(value, part) for part in parts):
            parts.append(value)
            return True
        return False

    def add_first(keys):
        for key in keys:
            if add(address.get(key)):
                return

    if not add(item.get('name')):
        add_first(_FIRST)
    add_first(_DISTRICT)
    add_first(_CITY)
    if not parts and item.get('display_name'):
        skip = {str(address.get('country') or ''), str(address.get('postcode') or '')}
        for piece in str(item['display_name']).split(','):
            piece = piece.strip()
            if piece and piece not in skip and not piece.isdigit():
                add(piece)
            if len(parts) == 3:
                break
    return '، '.join(parts[:3])


def _full_address(item, name, lang):
    """Nominatim display_name → full address joined with '، ', without the postcode."""
    pieces = [clean_text(piece, lang) for piece in str(item.get('display_name') or '').split(',')]
    pieces = [piece for piece in pieces if piece and not piece.isdigit()]
    return '، '.join(pieces) or name


# ---------------------------------------------------------------- place name

def _cache_get(db, key):
    try:
        row = db.execute('SELECT name FROM geo_cache WHERE key = ?', (key,)).fetchone()
    except sqlite3.OperationalError:   # init not called yet (e.g. in other engines' tests)
        try:
            init(db)
        except sqlite3.Error:
            log.exception('geo: cannot create geo_cache')
        return None
    return None if row is None else row[0]


def _cache_put(db, key, lat, lon, lang, name):
    try:
        db.execute('INSERT OR REPLACE INTO geo_cache (key, lat, lon, lang, name, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                   (key, lat, lon, lang, name, utcnow()))
    except sqlite3.Error:
        log.exception('geo: cannot write geo_cache')


def _reverse(lat, lon, lang, fetch):
    """Nominatim reverse → name; '' if Nominatim does not know the place; None without network."""
    params = {'lat': '%.4f' % lat, 'lon': '%.4f' % lon, 'format': 'jsonv2', 'accept-language': LANGS[lang],
              'zoom': REVERSE_ZOOM, 'addressdetails': 1}
    url = NOMINATIM_URL + '/reverse?' + urllib.parse.urlencode(params)
    try:
        data = _as_json((fetch or fetch_json)(url))
    except Exception as exc:
        log.info('geo: reverse without network (%s)', exc)
        return None
    if not isinstance(data, dict):
        return None
    if data.get('error'):
        return ''
    return format_address(data, lang)


def _engine(name):
    """Neighbouring engine server.<name> (imported lazily) or None if it does not exist yet."""
    full = 'server.' + name
    try:
        return importlib.import_module(full)
    except ModuleNotFoundError as exc:
        if exc.name == full:
            log.debug('geo: engine %s is not built yet', name)
            return None
        raise


def _field(item, key):
    try:
        return item[key]
    except (KeyError, IndexError, TypeError):
        return getattr(item, key, None)


def place_title(item, lang='fa'):
    """Place name from content (dict or sqlite3.Row): name_<lang> → name → title."""
    for key in ('name_' + lang, 'name', 'name_fa', 'title_' + lang, 'title', 'title_fa'):
        value = _field(item, key)
        if value:
            return clean_text(value, lang)
    return ''


def nearest_known(db, lat, lon, lang='fa', within_km=NEAR_KM):
    """Nearest known place (content.nearest_places) within within_km → "نزدیک: …" or ''."""
    content = _engine('content')
    finder = getattr(content, 'nearest_places', None) if content is not None else None
    if finder is None:
        return ''
    try:
        places = finder(db, lat, lon, 3) or []
    except Exception:
        log.exception('geo: content.nearest_places failed')
        return ''
    best = None
    for place in places:
        dist = _field(place, 'distance_km')
        if dist is None:
            p_lat, p_lon = _field(place, 'lat'), _field(place, 'lon')
            if p_lat is None or p_lon is None:
                continue
            dist = distance_km(lat, lon, p_lat, p_lon)
        title = place_title(place, lang)
        if title and float(dist) <= within_km and (best is None or float(dist) < best[0]):
            best = (float(dist), title)
    return '%s: %s' % (NEAR_WORD[lang], best[1]) if best else ''


def place_name(db, lat, lon, lang='fa', fetch=None):
    """Short place name for coordinates (see the top of the file). Nothing found → ''.

    fetch(url) → JSON replaces the network in tests; the default is fetch_json (Nominatim with the queue).
    """
    lat, lon = parse_point(lat, lon)
    lang = _lang(lang)
    r_lat, r_lon = _round4(lat), _round4(lon)
    key = _cache_key(r_lat, r_lon, lang)
    cached = _cache_get(db, key)
    if cached:
        return cached
    if cached is None:                   # not in the cache: ask Nominatim
        name = _reverse(r_lat, r_lon, lang, fetch)
        if name is not None:             # got an answer: remember it (even empty: Nominatim does not know the place)
            _cache_put(db, key, r_lat, r_lon, lang, name)
            if name:
                return name
    return nearest_known(db, lat, lon, lang)


# ---------------------------------------------------------------- address search

def search_places(q, lang='fa', fetch=None, limit=SEARCH_LIMIT):
    """Address → up to limit points in Afghanistan [{name, lat, lon, full}]; without network → None."""
    text = ' '.join(str(q or '').split())
    lang = _lang(lang)
    if not text:
        return []
    cache_key = (lang, text.lower(), int(limit))
    hit = _search_cache.get(cache_key)
    if fetch is None and hit and time.monotonic() - hit[0] < SEARCH_CACHE_S:
        return [dict(item) for item in hit[1]]
    params = {'q': text, 'format': 'jsonv2', 'countrycodes': 'af', 'limit': int(limit),
              'accept-language': LANGS[lang], 'addressdetails': 1}
    url = NOMINATIM_URL + '/search?' + urllib.parse.urlencode(params)
    try:
        data = _as_json((fetch or fetch_json)(url))
    except Exception as exc:
        log.info('geo: search without network (%s)', exc)
        return None
    if not isinstance(data, list):
        return None
    results = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            point = parse_point(item.get('lat'), item.get('lon'))
        except ApiError:
            continue
        name = format_address(item, lang)
        if not name:
            continue
        results.append({'name': name, 'lat': point[0], 'lon': point[1], 'full': _full_address(item, name, lang)})
        if len(results) >= limit:
            break
    if fetch is None:
        if len(_search_cache) >= SEARCH_CACHE_SIZE:
            _search_cache.clear()
        _search_cache[cache_key] = (time.monotonic(), [dict(item) for item in results])
    return results


# ---------------------------------------------------------------- handlers

def _query(req, key):
    value = (getattr(req, 'query', None) or {}).get(key)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    return value


def handle_name(req):
    """GET /api/geo/name?lat=&lon=[&lang=] → {name, lat, lon}."""
    lat, lon = parse_point(_query(req, 'lat'), _query(req, 'lon'))
    return 200, {'name': place_name(req.db, lat, lon, _query(req, 'lang')), 'lat': lat, 'lon': lon}


def handle_search(req):
    """GET /api/geo/search?q=[&lang=] → {results: [{name, lat, lon, full}], offline}."""
    text = ' '.join(str(_query(req, 'q') or '').split())
    if not text:
        raise ApiError(400, 'q_required', MSG['q_required'])
    if len(text) > MAX_QUERY:
        raise ApiError(400, 'q_too_long', MSG['q_too_long'])
    results = search_places(text, _query(req, 'lang'))
    return 200, {'results': results or [], 'offline': results is None}
