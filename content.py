"""content — emergency numbers, training videos, safe places and search: figma 16, 17 (admin), 07–09 (resident).

Contract:
  init(db)  — tables numbers, videos, safe_places (+ the service table content_seeded) and seed data ONCE:
              a second run duplicates nothing, and what the admin deleted does not come back.
  routes()  — resident (no login):
      GET /api/numbers                     → {numbers: [{id, number, title, title_ps, sort}]}, by sort
      GET /api/videos                      → {videos: [{id, title, title_ps, url, kind, youtube_id, source,
                                                        created_at}]}, newest first
      GET /api/places?lat&lon&limit        → {places: [{id, name, name_ps, kind, lat, lon, address, source,
                                                        distance_km}]}, nearest first (without lat/lon:
                                              distance_km null, in stored order)
      GET /api/search?q=&lat&lon           → {q, alerts, videos, numbers, places}; alerts from alerts.search(db, q)
    admin (otherwise 401/403):
      POST /api/admin/numbers {number, title, title_ps, sort} → {number}; PUT / DELETE /api/admin/numbers/{id}
      POST /api/admin/videos {title, title_ps, url, source}   → {video};  PUT / DELETE /api/admin/videos/{id}
      POST /api/admin/places {name, name_ps, kind, lat, lon, address} → {place}; PUT / DELETE /api/admin/places/{id}
      DELETE → {ok: true, id}. PUT changes only the fields that were sent.
  nearest_places(db, lat, lon, n=3) → [place + distance_km], nearest first (for alerts and geo).
  search_text(text) — normalization for search (alerts.search can call it too).

Number: digits (Dari ۰–۹ too), an optional leading '+', 2–15 digits; spaces and '-' are removed.
Video: a YouTube link (watch?v=, youtu.be/, embed/, shorts/, live/) → kind 'youtube', url is normalized to
https://www.youtube.com/watch?v=ID; a file (/uploads/… or http(s)://….mp4|webm|ogg|ogv|m4v) → kind 'file'; else 400.
Place: kind ∈ PLACE_KINDS; lat/lon are parsed as in geo.parse_point (same as alerts): Dari digits and '٫' are allowed.
Search uses the stems of the query words, all of which must be found in one object; ignores case and Latin diacritics
('farkhar' finds 'Farkhār'); Dari and Pashto: ي/ى/ې/ۍ/ئ→ی, ك→ک, ګ→گ, ة/ۀ/ە→ه, أ/إ/آ→ا, Dari digits → 0–9, without
vowel marks, ZWNJ and spaces; the stem drops the endings ها/های/ونه/ونو/ان/و and a final ه/ی
('زلزلې' finds 'زلزله', 'کمکهای' finds 'کمک‌های').
Where the seed data comes from: app/server/content_sources.md.
"""
import logging
import math
import re
import unicodedata
import urllib.parse

from server.common import ApiError, utcnow

log = logging.getLogger(__name__)

PLACE_KINDS = ('park', 'stadium', 'campus', 'open_area', 'hill', 'shelter', 'other')
VIDEO_KINDS = ('youtube', 'file')
VIDEO_EXT = ('.mp4', '.webm', '.ogg', '.ogv', '.m4v')
MAX_TITLE = 200
MAX_URL = 500
SEARCH_LIMIT = 20

# --- seed data (sources and check dates in content_sources.md) ---
SEED_NUMBERS = [
    # (number, Dari, Pashto); 119 and 102 from the group, 06.11; 100 from official sites (content_sources.md).
    # 112 (fire) is no longer seeded since v3: the group asked for officially confirmed numbers only.
    ('119', 'پولیس', 'پولیس'),
    ('102', 'آمبولانس', 'امبولانس'),
    ('100', 'پولیس (شمارهٔ دوم)', 'پولیس (دوهمه شمېره)'),
]
# The 112 row exactly as the v2 seed created it (number, title, title_ps, sort): removed once from older databases,
# but only while it is untouched; a row the admin edited stays.
V2_FIRE_112 = ('112', 'آتش‌نشانی', 'اور وژونکي', 4)

SEED_VIDEOS = [
    # (Dari title from figma 17, Pashto, YouTube link (oEmbed 200, 06.11.2026), organization and language)
    ('چگونه در هنگام سیلاب پناه بگیریم', 'د سیلاب پر مهال څنګه پناه واخلو',
     'https://www.youtube.com/watch?v=Glc-1f4Ez00', 'American Red Cross, English'),
    ('چگونه زلزله را تشخیص دهیم', 'زلزله څنګه وپېژنو',
     'https://www.youtube.com/watch?v=MKILThtPxQs', 'FEMA, English'),
    ('کمک‌های اولیه در حوادث', 'په پېښو کې لومړنۍ مرستې',
     'https://www.youtube.com/watch?v=jrc0QEscOco', 'Afghan Red Crescent Society (ARCS), Dari'),
    ('آمادگی در برابر آتش‌سوزی', 'د اور لګېدو پر وړاندې چمتووالی',
     'https://www.youtube.com/watch?v=tNYcJaeaRQg', 'American Red Cross, English'),
]

SEED_PLACES = [
    # (Dari, Pashto, kind, lat, lon, address); coordinates from OpenStreetMap (Nominatim), source='example'
    ('پارک شهر نو', 'د نوي ښار پارک', 'park', 34.5331433, 69.1684384, 'ناحیه چهارم، کابل'),
    ('چمن حضوری', 'حضوري چمن', 'open_area', 34.5152174, 69.1951985, 'ناحیه هشتم، کابل'),
    ('پارک زرنگار', 'د زرنګار پارک', 'park', 34.5212367, 69.1752644, 'ناحیه دوم، کابل'),
    ('باغ بابر', 'بابر بڼ', 'park', 34.5026029, 69.1583798, 'ناحیه هفتم، کابل'),
    ('باغ چهلستون', 'د چهلستون بڼ', 'park', 34.4681673, 69.1523306, 'ناحیه هفتم، کابل'),
    ('پارک حصه اول خیرخانه', 'د خیرخانې د لومړۍ برخې پارک', 'park', 34.5743975, 69.1233809, 'ناحیه یازدهم، کابل'),
    ('ورزشگاه غازی', 'غازي لوبغالی', 'stadium', 34.5186050, 69.1939630, 'ناحیه نهم، کابل'),
    ('پوهنتون کابل', 'کابل پوهنتون', 'campus', 34.5154398, 69.1338400, 'جاده علی‌آباد، ناحیه سوم، کابل'),
    ('پوهنتون پولی‌تخنیک کابل', 'د کابل پولي تخنیک پوهنتون', 'campus', 34.5356621, 69.1199368, 'ناحیه سوم، کابل'),
    ('پوهنتون تعلیم و تربیه', 'د ښوونې او روزنې پوهنتون', 'campus', 34.5327049, 69.1122522, 'ناحیه پنجم، کابل'),
    ('محوطهٔ قصر دارالامان', 'د دارالامان ماڼۍ انګړ', 'open_area', 34.4649317, 69.1199532, 'ناحیه ششم، کابل'),
    ('تپهٔ بی‌بی مهرو', 'د بي بي مهرو غونډۍ', 'hill', 34.5440046, 69.1830777, 'ناحیه دهم، کابل'),
]

SEED_PLACES_CITIES = [
    # v3, the group's request: examples outside Kabul, 1–2 per city; same format and source as SEED_PLACES
    ('پوهنتون هرات', 'د هرات پوهنتون', 'campus', 34.3675322, 62.2111314, 'هرات'),
    ('ورزشگاه هرات', 'د هرات لوبغالی', 'stadium', 34.3473974, 62.2176845, 'هرات'),
    ('پوهنتون بلخ', 'د بلخ پوهنتون', 'campus', 36.7108014, 67.1275958, 'مزار شریف، بلخ'),
    ('بوستان آریانا', 'د آریانا پارک', 'park', 36.7118437, 67.1413598, 'مزار شریف، بلخ'),
    ('پوهنتون ننگرهار', 'د ننګرهار پوهنتون', 'campus', 34.4747094, 70.3681243, 'درونته، جلال‌آباد، ننگرهار'),
    ('پارک جوی هفت', 'د جوی هفت پارک', 'park', 34.4263639, 70.4305681, 'جلال‌آباد، ننگرهار'),
    ('پوهنتون قندهار', 'د کندهار پوهنتون', 'campus', 31.6374143, 65.6971092, 'ارغنداب، قندهار'),
    ('ورزشگاه احمدشاهی', 'احمد شاهي لوبغالی', 'stadium', 31.6100534, 65.6883916, 'قندهار'),
    ('پوهنتون قندوز', 'د کندوز پوهنتون', 'campus', 36.7112863, 68.8631860, 'قندوز'),
    ('پوهنتون فیض‌آباد', 'د فیض اباد پوهنتون', 'campus', 37.1038960, 70.5264848, 'فیض‌آباد، بدخشان'),
    ('پوهنتون بامیان', 'د بامیان پوهنتون', 'campus', 34.8019149, 67.8338409, 'بامیان'),
    ('پوهنتون خوست', 'د خوست پوهنتون', 'campus', 33.3491318, 69.8687197, 'خوست'),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS numbers (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    number    TEXT NOT NULL,
    title     TEXT NOT NULL,
    title_ps  TEXT NOT NULL DEFAULT '',
    sort      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS videos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    title_ps   TEXT NOT NULL DEFAULT '',
    url        TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT 'youtube' CHECK (kind IN ('youtube', 'file')),
    source     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS safe_places (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    name_ps  TEXT NOT NULL DEFAULT '',
    kind     TEXT NOT NULL DEFAULT 'open_area',
    lat      REAL NOT NULL,
    lon      REAL NOT NULL,
    address  TEXT NOT NULL DEFAULT '',
    source   TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS content_seeded (
    name TEXT PRIMARY KEY,
    at   TEXT NOT NULL
);
"""

NUMBER_FIELDS = ('id', 'number', 'title', 'title_ps', 'sort')
VIDEO_FIELDS = ('id', 'title', 'title_ps', 'url', 'kind', 'source', 'created_at')
PLACE_FIELDS = ('id', 'name', 'name_ps', 'kind', 'lat', 'lon', 'address', 'source')


def init(db):
    """Tables and seed data, each set once (a mark in content_seeded); v3 changes for older databases, also once."""
    db.executescript(SCHEMA)
    now = utcnow()
    _seed(db, 'numbers', now, lambda: db.executemany(
        'INSERT INTO numbers (number, title, title_ps, sort) VALUES (?, ?, ?, ?)',
        [(number, title, title_ps, i + 1) for i, (number, title, title_ps) in enumerate(SEED_NUMBERS)]))
    _seed(db, 'videos', now, lambda: db.executemany(  # descending time: the first one from figma 17 on top
        'INSERT INTO videos (title, title_ps, url, kind, source, created_at) VALUES (?, ?, ?, ?, ?, ?)',
        [(title, title_ps, url, 'youtube', source, utcnow(-i))
         for i, (title, title_ps, url, source) in enumerate(SEED_VIDEOS)]))
    _seed(db, 'places', now, lambda: db.executemany(
        'INSERT INTO safe_places (name, name_ps, kind, lat, lon, address, source) VALUES (?, ?, ?, ?, ?, ?, ?)',
        [row + ('example',) for row in SEED_PLACES]))
    # v3: places outside Kabul, added once to new databases and to databases made by an older version
    _seed(db, 'places_v3_cities', now, lambda: db.executemany(
        'INSERT INTO safe_places (name, name_ps, kind, lat, lon, address, source) VALUES (?, ?, ?, ?, ?, ?, ?)',
        [row + ('example',) for row in SEED_PLACES_CITIES]))
    # v3: 112 is not seeded any more; an older database loses the untouched seed row once (an edited row stays)
    _seed(db, 'numbers_v3_drop_112', now, lambda: db.execute(
        'DELETE FROM numbers WHERE number = ? AND title = ? AND title_ps = ? AND sort = ?', V2_FIRE_112))
    db.commit()


def _seed(db, name, now, fill):
    if db.execute('SELECT 1 FROM content_seeded WHERE name = ?', (name,)).fetchone():
        return
    fill()
    db.execute('INSERT INTO content_seeded (name, at) VALUES (?, ?)', (name, now))


def routes():
    return [
        ('GET', '/api/numbers', get_numbers),
        ('GET', '/api/videos', get_videos),
        ('GET', '/api/places', get_places),
        ('GET', '/api/search', search),
        ('POST', '/api/admin/numbers', add_number),
        ('PUT', '/api/admin/numbers/{id}', edit_number),
        ('DELETE', '/api/admin/numbers/{id}', delete_number),
        ('POST', '/api/admin/videos', add_video),
        ('PUT', '/api/admin/videos/{id}', edit_video),
        ('DELETE', '/api/admin/videos/{id}', delete_video),
        ('POST', '/api/admin/places', add_place),
        ('PUT', '/api/admin/places/{id}', edit_place),
        ('DELETE', '/api/admin/places/{id}', delete_place),
    ]


# ---------- reading ----------

def list_numbers(db):
    return [_number_out(row) for row in db.execute('SELECT * FROM numbers ORDER BY sort, id')]


def list_videos(db):
    return [_video_out(row) for row in db.execute('SELECT * FROM videos ORDER BY created_at DESC, id DESC')]


def list_places(db, lat=None, lon=None, limit=None):
    """Places; with lat/lon they get distance_km and the nearest come first."""
    places = [_place_out(row) for row in db.execute('SELECT * FROM safe_places ORDER BY id')]
    if lat is not None and lon is not None:
        for place in places:
            place['distance_km'] = round(_distance_km(lat, lon, place['lat'], place['lon']), 3)
        places.sort(key=lambda place: (place['distance_km'], place['id']))
    if limit is not None:
        places = places[:limit]
    return places


def nearest_places(db, lat, lon, n=3):
    """n nearest safe places to a point → [{id, name, name_ps, kind, lat, lon, address, source, distance_km}]."""
    try:
        lat, lon = _point(lat, lon, required=True)
        return list_places(db, lat, lon, max(0, int(n)))
    except Exception as exc:  # sqlite3.OperationalError: no such table — content.init not called yet
        if 'no such table' in str(exc):
            return []
        raise


def get_numbers(req):
    return 200, {'numbers': list_numbers(req.db)}


def get_videos(req):
    return 200, {'videos': list_videos(req.db)}


def get_places(req):
    lat, lon = _query_point(req)
    limit = _query(req, 'limit')
    try:
        limit = max(0, int(limit)) if limit not in (None, '') else None
    except ValueError:
        raise ApiError(400, 'bad_limit', 'تعداد نادرست است.')
    return 200, {'places': list_places(req.db, lat, lon, limit)}


def search(req):
    db = req.db
    q = str(_query(req, 'q') or '').strip()[:100]
    terms = search_terms(q)
    result = {'q': q, 'alerts': [], 'videos': [], 'numbers': [], 'places': []}
    if not terms:
        return 200, result
    lat, lon = _query_point(req)
    result['alerts'] = _alerts_search(db, q)
    result['videos'] = [v for v in list_videos(db)
                        if _hit(terms, v['title'], v['title_ps'], v['source'])][:SEARCH_LIMIT]
    result['numbers'] = [n for n in list_numbers(db)
                         if _hit(terms, n['number'], n['title'], n['title_ps'])][:SEARCH_LIMIT]
    result['places'] = [p for p in list_places(db, lat, lon)
                        if _hit(terms, p['name'], p['name_ps'], p['address'])][:SEARCH_LIMIT]
    return 200, result


# ---------- admin: numbers ----------

def add_number(req):
    _require_admin(req)
    db, body = req.db, _body(req)
    number = _clean_number(body.get('number'))
    title = _title(body.get('title'), required=True)
    title_ps = _title(body.get('title_ps'))
    sort = _int(body.get('sort'), 'bad_sort')
    if sort is None:
        sort = db.execute('SELECT coalesce(max(sort), 0) + 1 FROM numbers').fetchone()[0]
    cur = db.execute('INSERT INTO numbers (number, title, title_ps, sort) VALUES (?, ?, ?, ?)',
                     (number, title, title_ps, sort))
    db.commit()
    return 200, {'number': _number_out(_get(db, 'numbers', cur.lastrowid))}


def edit_number(req):
    _require_admin(req)
    db, body = req.db, _body(req)
    row = _get_or_404(req, 'numbers')
    changes = {}
    if 'number' in body:
        changes['number'] = _clean_number(body.get('number'))
    if 'title' in body:
        changes['title'] = _title(body.get('title'), required=True)
    if 'title_ps' in body:
        changes['title_ps'] = _title(body.get('title_ps'))
    if 'sort' in body:
        changes['sort'] = _int(body.get('sort'), 'bad_sort') or 0
    _update(db, 'numbers', row['id'], changes)
    return 200, {'number': _number_out(_get(db, 'numbers', row['id']))}


def delete_number(req):
    return _delete(req, 'numbers')


# ---------- admin: videos ----------

def add_video(req):
    _require_admin(req)
    db, body = req.db, _body(req)
    title = _title(body.get('title'), required=True)
    title_ps = _title(body.get('title_ps'))
    url, kind = video_link(body.get('url'))
    source = _title(body.get('source')) or 'admin'
    cur = db.execute('INSERT INTO videos (title, title_ps, url, kind, source, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                     (title, title_ps, url, kind, source, utcnow()))
    db.commit()
    return 200, {'video': _video_out(_get(db, 'videos', cur.lastrowid))}


def edit_video(req):
    _require_admin(req)
    db, body = req.db, _body(req)
    row = _get_or_404(req, 'videos')
    changes = {}
    if 'title' in body:
        changes['title'] = _title(body.get('title'), required=True)
    if 'title_ps' in body:
        changes['title_ps'] = _title(body.get('title_ps'))
    if 'url' in body:
        changes['url'], changes['kind'] = video_link(body.get('url'))
    if 'source' in body:
        changes['source'] = _title(body.get('source'))
    _update(db, 'videos', row['id'], changes)
    return 200, {'video': _video_out(_get(db, 'videos', row['id']))}


def delete_video(req):
    return _delete(req, 'videos')


# ---------- admin: places ----------

def add_place(req):
    _require_admin(req)
    db, body = req.db, _body(req)
    name = _title(body.get('name'), required=True, code='name_required', message='نام مکان را بنویسید.')
    name_ps = _title(body.get('name_ps'))
    kind = _place_kind(body.get('kind'))
    lat, lon = _point(body.get('lat'), body.get('lon'), required=True)
    address = _title(body.get('address'))
    cur = db.execute('INSERT INTO safe_places (name, name_ps, kind, lat, lon, address, source)'
                     ' VALUES (?, ?, ?, ?, ?, ?, ?)', (name, name_ps, kind, lat, lon, address, 'admin'))
    db.commit()
    return 200, {'place': _place_out(_get(db, 'safe_places', cur.lastrowid))}


def edit_place(req):
    _require_admin(req)
    db, body = req.db, _body(req)
    row = _get_or_404(req, 'safe_places')
    changes = {}
    if 'name' in body:
        changes['name'] = _title(body.get('name'), required=True, code='name_required',
                                 message='نام مکان را بنویسید.')
    if 'name_ps' in body:
        changes['name_ps'] = _title(body.get('name_ps'))
    if 'kind' in body:
        changes['kind'] = _place_kind(body.get('kind'))
    if 'lat' in body or 'lon' in body:
        changes['lat'], changes['lon'] = _point(body.get('lat', row['lat']), body.get('lon', row['lon']),
                                                required=True)
    if 'address' in body:
        changes['address'] = _title(body.get('address'))
    _update(db, 'safe_places', row['id'], changes)
    return 200, {'place': _place_out(_get(db, 'safe_places', row['id']))}


def delete_place(req):
    return _delete(req, 'safe_places')


# ---------- videos: links ----------

_YOUTUBE_HOSTS = ('youtube.com', 'youtube-nocookie.com')
_YOUTUBE_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')


def youtube_id(url):
    """Video ID from a YouTube link; not YouTube → None; YouTube without a valid ID → ''."""
    try:
        parts = urllib.parse.urlsplit(str(url or '').strip())
    except ValueError:
        return None
    host = (parts.hostname or '').lower()
    for prefix in ('www.', 'm.', 'music.'):
        if host.startswith(prefix):
            host = host[len(prefix):]
    if host == 'youtu.be':
        candidate = parts.path.strip('/').split('/')[0]
    elif host in _YOUTUBE_HOSTS:
        path = parts.path.rstrip('/')
        segments = path.strip('/').split('/')
        if path == '/watch':
            candidate = urllib.parse.parse_qs(parts.query).get('v', [''])[0]
        elif len(segments) >= 2 and segments[0] in ('embed', 'shorts', 'live', 'v'):
            candidate = segments[1]
        else:
            candidate = ''
    else:
        return None
    return candidate if _YOUTUBE_ID.match(candidate) else ''


def video_link(raw):
    """Link from the admin → (url, kind). YouTube → ('https://www.youtube.com/watch?v=ID', 'youtube'); file → 'file'."""
    url = str(raw or '').strip()
    bad = ApiError(400, 'bad_video_url', 'لینک ویدیو نادرست است. لینک یوتیوب یا فایل ویدیو (mp4) را بدهید.')
    if not url or len(url) > MAX_URL:
        raise bad
    found = youtube_id(url)
    if found is None and '://' not in url and not url.startswith('/'):
        found = youtube_id('https://' + url)  # 'youtube.com/watch?v=…' without https://
    if found:
        return 'https://www.youtube.com/watch?v=' + found, 'youtube'
    if found == '':
        raise bad
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        raise bad
    path = parts.path.lower()
    local = url.startswith('/') and not url.startswith('//') and not parts.scheme
    remote = parts.scheme in ('http', 'https') and bool(parts.netloc)
    if (local or remote) and path.endswith(VIDEO_EXT) and '..' not in parts.path:
        return url, 'file'
    raise bad


# ---------- search ----------

_FOLD = {
    'ي': 'ی', 'ى': 'ی', 'ې': 'ی', 'ۍ': 'ی', 'ئ': 'ی', 'ك': 'ک', 'ګ': 'گ', 'ة': 'ه', 'ۀ': 'ه', 'ہ': 'ه', 'ە': 'ه',
    'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ٱ': 'ا', 'ؤ': 'و',
}
for _i in range(10):
    _FOLD[chr(0x06F0 + _i)] = str(_i)  # ۰–۹ (Dari)
    _FOLD[chr(0x0660 + _i)] = str(_i)  # ٠–٩ (Arabic)
_FOLD = str.maketrans(_FOLD)
_DROP = re.compile('[\u064B-\u065F\u0670\u0640\u200B-\u200F\u202A-\u202E\u2066-\u2069\\s]+')


_WORD_SPLIT = re.compile('[\\s،,؛;:.!?؟()«»"\'/\\-]+')  # ZWNJ does not split: 'سیلاب‌ها' is one word
_SUFFIXES = ('هایی', 'های', 'ونو', 'ونه', 'ها', 'ان', 'و')  # already in search_text form, longest first
_STOP_WORDS = ('در', 'د', 'و', 'به', 'از', 'با', 'را', 'کې', 'ته', 'برای', 'ها', 'های', 'هایی',  # «سیلاب ها»
               'the', 'of', 'in', 'a', 'and')


def search_text(text):
    """String for search comparison: no case, diacritics (Farkhār → farkhar), vowel marks, ZWNJ or spaces;
    Dari/Pashto/Arabic letters and digits are folded to one form."""
    value = unicodedata.normalize('NFKD', str(text or ''))
    value = ''.join(ch for ch in value if unicodedata.category(ch) != 'Mn')  # vowel marks, diacritics, madda, hamza
    value = value.translate(_FOLD).casefold()
    return _DROP.sub('', value)


def word_stem(word):
    """Word stem (input already in search_text form): no plural ending and no final ه/ی —
    'زلزلې'/'زلزله'/'زلزلو' → 'زلزل', 'سیلابونه'/'سیلاب‌ها' → 'سیلاب'. Short words are left as is."""
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[:-len(suffix)]
            break
    if len(word) >= 4 and word[-1] in 'هی':
        word = word[:-1]
    return word


_STOP = {search_text(word) for word in _STOP_WORDS}


def search_terms(q):
    """Query → word stems; function words ('در', 'د', 'of') are dropped if there are other words."""
    words = [search_text(part) for part in _WORD_SPLIT.split(str(q or ''))]
    words = [word for word in words if word]
    useful = [word for word in words if word not in _STOP] or words
    return [word_stem(word) for word in useful]


def _hit(terms, *fields):
    """Every query stem is found in at least one field of the object."""
    hay = [search_text(field) for field in fields if field]
    return all(any(term in text for text in hay) for term in terms)


def _alerts_search(db, q):
    try:
        from server import alerts
        found = alerts.search(db, q)
    except (ImportError, AttributeError):
        return []
    except Exception:
        log.exception('alerts.search failed; no alerts in the search results')
        return []
    return list(found or [])


# ---------- helpers ----------

def _number_out(row):
    return {name: row[name] for name in NUMBER_FIELDS}


def _video_out(row):
    data = {name: row[name] for name in VIDEO_FIELDS}
    data['youtube_id'] = (youtube_id(data['url']) or None) if data['kind'] == 'youtube' else None
    return data


def _place_out(row):
    data = {name: row[name] for name in PLACE_FIELDS}
    data['distance_km'] = None
    return data


def _get(db, table, row_id):
    return db.execute('SELECT * FROM %s WHERE id = ?' % table, (row_id,)).fetchone()


_NOT_FOUND = {
    'numbers': ('number_not_found', 'شماره پیدا نشد.'),
    'videos': ('video_not_found', 'ویدیو پیدا نشد.'),
    'safe_places': ('place_not_found', 'مکان پیدا نشد.'),
}


def _get_or_404(req, table):
    params = getattr(req, 'params', None) or {}
    try:
        row = _get(req.db, table, int(params.get('id')))
    except (TypeError, ValueError):
        row = None
    if row is None:
        raise ApiError(404, *_NOT_FOUND[table])
    return row


def _update(db, table, row_id, changes):
    if not changes:
        return
    names = list(changes)
    db.execute('UPDATE %s SET %s WHERE id = ?' % (table, ', '.join('%s = ?' % name for name in names)),
               [changes[name] for name in names] + [row_id])
    db.commit()


def _delete(req, table):
    _require_admin(req)
    row = _get_or_404(req, table)
    req.db.execute('DELETE FROM %s WHERE id = ?' % table, (row['id'],))
    req.db.commit()
    return 200, {'ok': True, 'id': row['id']}


def _body(req):
    body = getattr(req, 'body', None)
    return body if isinstance(body, dict) else {}


def _query(req, key):
    value = (getattr(req, 'query', None) or {}).get(key)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    return value


def _title(value, required=False, code='title_required', message='عنوان را بنویسید.'):
    text = str(value).strip() if value is not None else ''
    if required and not text:
        raise ApiError(400, code, message)
    if len(text) > MAX_TITLE:
        raise ApiError(400, 'too_long', 'متن بسیار طولانی است.')
    return text


_DIGITS = str.maketrans({chr(0x06F0 + i): str(i) for i in range(10)} | {chr(0x0660 + i): str(i) for i in range(10)})


def _clean_number(value):
    text = re.sub(r'[\s\-()]', '', str(value if value is not None else '').translate(_DIGITS))
    if not re.fullmatch(r'\+?\d{2,15}', text):
        raise ApiError(400, 'bad_number', 'شمارهٔ تماس نادرست است.')
    return text


def _int(value, code):
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ApiError(400, code, 'عدد نادرست است.')
    try:
        return int(str(value).translate(_DIGITS))
    except ValueError:
        raise ApiError(400, code, 'عدد نادرست است.')


def _place_kind(value):
    kind = str(value or 'open_area').strip().lower()
    if kind not in PLACE_KINDS:
        raise ApiError(400, 'bad_kind', 'نوع مکان نادرست است.')
    return kind


def _point(lat, lon, required=False):
    """(lat, lon) → numbers within range; both empty and not required → (None, None); otherwise 400.
    Parsing is geo.parse_point (same as alerts): Dari digits and the decimal sign '٫' are allowed."""
    try:
        from server import geo
        parse = geo.parse_point
    except (ImportError, AttributeError):
        parse = None
    if parse is not None:
        found = parse(lat, lon, required=required)
        return tuple(found) if found else (None, None)
    bad = ApiError(400, 'bad_location', 'موقعیت نادرست است.')
    empty = (lat is None or str(lat).strip() == '') and (lon is None or str(lon).strip() == '')
    if empty:
        if required:
            raise ApiError(400, 'location_required', 'موقعیت را مشخص کنید.')
        return None, None
    try:
        if isinstance(lat, bool) or isinstance(lon, bool):
            raise ValueError
        lat, lon = [float(str(v).strip().replace('٫', '.')) if isinstance(v, str) else float(v) for v in (lat, lon)]
    except (TypeError, ValueError):
        raise bad
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise bad
    return lat, lon


def _query_point(req):
    return _point(_query(req, 'lat'), _query(req, 'lon'))


def _require_admin(req):
    try:
        from server import auth
        check = auth.require_admin
    except (ImportError, AttributeError):
        check = None
    if check is not None:
        return check(req)
    admin = getattr(req, 'admin', None)
    if not admin:  # like auth.require_admin: 403 for both a guest and a resident
        raise ApiError(403, 'admin_required', 'این بخش تنها برای مدیر است.')
    return admin


def _distance_km(lat1, lon1, lat2, lon2):
    try:
        from server import geo
        return float(geo.distance_km(lat1, lon1, lat2, lon2))
    except Exception:
        pass
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(h)))
