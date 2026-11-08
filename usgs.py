"""usgs — earthquakes in Afghanistan from the free USGS feed.

poll_once(db, fetch=None, now=None) → {fetched, afghanistan, new, active, ended, feed[, error]}
    Feed 2.5_day.geojson; on the first run (and if there was no successful poll for over CATCH_UP_HOURS) —
    2.5_month.geojson: 30 days of history. Afghanistan means latitude 29.3–38.5, longitude 60.5–75.0 and
    'Afghanistan' in properties.place. No duplicates: the event id (and its other ids from properties.ids)
    is checked against alerts.source_id. M ≥ 4.0 and not older than 24 h → active alert alerts.create(source='usgs')
    (notifications, SMS, live); the rest → alerts.add_past (past ones, status ended).
    Place as given by USGS ('47 km SSW of Jurm, Afghanistan'), magnitude, depth, event time.
    Active USGS alerts older than 24 h are ended (alerts.end), so a quake does not stay "active" forever.
    fetch(url) → JSON (dict / str / bytes) replaces the network in tests; now is "now" (datetime or Unix seconds).
    No network: the error goes into the status, no exception. An active alert and its ending are committed in alerts
    before the live event; history and status are committed by the caller (the thread does it itself).
load_history(db) → {events, afghanistan, new[, skipped | error]}
    Real past earthquakes from the official USGS FDSN catalog, saved in data/usgs_history.geojson (query and date
    in data/README.md): Afghanistan as above, M ≥ HISTORY_MIN_MAGNITUDE (5.5), since 1990. Loaded once per snapshot
    as past alerts (source 'usgs', status ended, real event time); no repeats with the live feed (same ids check).
    Place names where USGS lost a letter with a diacritic ('Ashk?sham') are restored from data/usgs_place_fixes.json.
status(db) → {last_poll, ok, last_error, last_event, new_total, feed, running, history}
start(app) — loads the history (once), then a thread polls right away and every 60 s (app.config['usgs_interval']);
    app.config['usgs'] is False → not started; stop it with app.stopping or stop().
"""
import datetime
import gzip
import json
import logging
import threading
import urllib.request
from pathlib import Path

from server import alerts, store
from server.common import ApiError, parse_utc, to_utc, utcnow

log = logging.getLogger(__name__)

FEED_DAY = 'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson'
FEED_MONTH = 'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_month.geojson'
USER_AGENT = 'PANAH/1.0 (AUAF student project)'
TIMEOUT_S = 15
INTERVAL_S = 60
AF_LAT = (29.3, 38.5)
AF_LON = (60.5, 75.0)
MIN_MAGNITUDE = 4.0
ACTIVE_HOURS = 24
CATCH_UP_HOURS = 23      # no successful poll for longer → the month feed again, so that nothing is missed
STATE_KEY = 'usgs_status'
DATA_DIR = Path(__file__).resolve().parent / 'data'
HISTORY_FILE = DATA_DIR / 'usgs_history.geojson'          # FDSN snapshot: see data/README.md
PLACE_FIXES_FILE = DATA_DIR / 'usgs_place_fixes.json'
HISTORY_MIN_MAGNITUDE = 5.5                               # 114 earthquakes in Afghanistan since 1990 (see README)
HISTORY_KEY = 'usgs_history'

_thread = None
_stop = threading.Event()


def init(db):
    """No own tables: alerts go to alerts (so we create its tables), the state goes to settings."""
    alerts.init(db)


def routes():
    return []


# ---------------------------------------------------------------- time

def _now(now=None):
    if now is None:
        return datetime.datetime.now(datetime.timezone.utc)
    if isinstance(now, datetime.datetime):
        return now if now.tzinfo else now.replace(tzinfo=datetime.timezone.utc)
    if isinstance(now, str):
        return parse_utc(now)
    return datetime.datetime.fromtimestamp(float(now), datetime.timezone.utc)


# ---------------------------------------------------------------- feed

def fetch_feed(url, timeout=TIMEOUT_S):
    """GET the USGS feed → dict (GeoJSON). No network / error → exception (OSError, ValueError)."""
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept-Encoding': 'gzip',
                                                   'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        body = resp.read()
        if (resp.headers.get('Content-Encoding') or '').lower() == 'gzip':
            body = gzip.decompress(body)
    return json.loads(body.decode('utf-8'))


def _as_json(data):
    if isinstance(data, (bytes, bytearray)):
        data = data.decode('utf-8')
    if isinstance(data, str):
        data = json.loads(data)
    return data


def parse_feature(feature):
    """GeoJSON event → dict {id, ids, place, lat, lon, depth_km, magnitude, time, kind} or None (broken)."""
    try:
        props = feature.get('properties') or {}
        coords = (feature.get('geometry') or {}).get('coordinates') or []
        lon, lat = float(coords[0]), float(coords[1])
        depth = float(coords[2]) if len(coords) > 2 and coords[2] is not None else None
        event_id = str(feature.get('id') or props.get('code') or '').strip()
        moment = datetime.datetime.fromtimestamp(float(props['time']) / 1000, datetime.timezone.utc)
    except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError, OSError):
        return None
    if not event_id:
        return None
    mag = props.get('mag')
    try:
        mag = float(mag) if mag is not None else None
    except (TypeError, ValueError):
        mag = None
    ids = {event_id} | {part.strip() for part in str(props.get('ids') or '').split(',') if part.strip()}
    return {'id': event_id, 'ids': ids, 'place': str(props.get('place') or '').strip(), 'lat': lat, 'lon': lon,
            'depth_km': depth, 'magnitude': mag, 'time': moment,
            'kind': str(props.get('type') or 'earthquake').strip().lower()}


def is_afghanistan(quake):
    """Afghanistan's box and 'Afghanistan' in the USGS place (not neighbouring Tajikistan, Pakistan, Iran)."""
    return (quake['kind'] == 'earthquake'
            and AF_LAT[0] <= quake['lat'] <= AF_LAT[1] and AF_LON[0] <= quake['lon'] <= AF_LON[1]
            and 'afghanistan' in quake['place'].lower())


def _alert_data(quake):
    return {'type': 'earthquake', 'source_id': quake['id'], 'place': quake['place'], 'lat': quake['lat'],
            'lon': quake['lon'], 'magnitude': quake['magnitude'], 'depth_km': quake['depth_km'],
            'starts_at': to_utc(quake['time'])}


def _known_ids(db):
    return {row[0] for row in db.execute("SELECT source_id FROM alerts WHERE source_id IS NOT NULL")}


def _need_history(state, now):
    if not state.get('history_done') or not state.get('last_success'):
        return True
    try:
        return now - parse_utc(state['last_success']) > datetime.timedelta(hours=CATCH_UP_HOURS)
    except (TypeError, ValueError):
        return True


def _end_stale(db, now):
    """Active USGS alerts older than ACTIVE_HOURS → ended (alerts.end: live event 'alert_end')."""
    edge = to_utc(now - datetime.timedelta(hours=ACTIVE_HOURS))
    count = 0
    for row in db.execute("SELECT id FROM alerts WHERE source = 'usgs' AND status = 'active' AND starts_at < ?",
                          (edge,)).fetchall():
        try:
            alerts.end(db, row['id'])
            count += 1
        except ApiError:
            pass
    return count


def poll_once(db, fetch=None, now=None):
    """One feed poll (see the top of the file) → {fetched, afghanistan, new, active, ended, feed[, error]}."""
    moment = _now(now)
    alerts.init(db)
    state = store.get_setting(db, STATE_KEY, {}) or {}
    history = _need_history(state, moment)
    url = FEED_MONTH if history else FEED_DAY
    result = {'fetched': 0, 'afghanistan': 0, 'new': 0, 'active': 0, 'ended': 0,
              'feed': 'month' if history else 'day'}
    state.update(last_poll=to_utc(moment), feed=result['feed'])
    try:
        feed = _as_json((fetch or fetch_feed)(url))   # network before the first database write
        features = feed['features']
        if not isinstance(features, list):
            raise ValueError('features is not a list')
    except Exception as exc:
        state.update(ok=False, last_error='%s: %s' % (type(exc).__name__, str(exc)[:300]))
        store.set_setting(db, STATE_KEY, state)
        log.warning('usgs: feed %s unavailable (%s); will retry', result['feed'], state['last_error'])
        result['error'] = state['last_error']
        return result
    result['fetched'] = len(features)
    quakes = [q for q in (parse_feature(f) for f in features if isinstance(f, dict)) if q and is_afghanistan(q)]
    result['afghanistan'] = len(quakes)
    known = _known_ids(db)
    fresh_edge = moment - datetime.timedelta(hours=ACTIVE_HOURS)
    for quake in sorted(quakes, key=lambda q: q['time']):
        if quake['ids'] & known:
            continue                                      # already known: a duplicate
        active = (quake['magnitude'] is not None and quake['magnitude'] >= MIN_MAGNITUDE
                  and quake['time'] >= fresh_edge)
        try:
            if active:
                alerts.create(db, _alert_data(quake), 'usgs')
                result['active'] += 1
            else:
                alerts.add_past(db, _alert_data(quake), 'usgs')
        except ApiError as exc:
            log.warning('usgs: event %s skipped (%s)', quake['id'], exc.code)
            continue
        known |= quake['ids']
        result['new'] += 1
    result['ended'] = _end_stale(db, moment)
    state.update(ok=True, last_error=None, last_success=to_utc(moment),
                 last_counts={k: result[k] for k in ('fetched', 'afghanistan', 'new', 'active', 'ended')})
    if history:
        state['history_done'] = True
    store.set_setting(db, STATE_KEY, state)
    if result['new'] or result['ended']:
        log.info('usgs: %s feed — %d events, %d in Afghanistan, %d new (%d active), %d ended', result['feed'],
                 result['fetched'], result['afghanistan'], result['new'], result['active'], result['ended'])
    return result


# ---------------------------------------------------------------- history from the FDSN catalog

def place_fixes(path=PLACE_FIXES_FILE):
    """{'Ashk?sham': 'Ashkāsham', ...} from the fixes file; no file or a broken one → {}."""
    try:
        names = json.loads(Path(path).read_text(encoding='utf-8')).get('names') or {}
        return {broken: item['name'] for broken, item in names.items() if item.get('name')}
    except (OSError, ValueError, AttributeError, TypeError, KeyError):
        log.warning('usgs: place fixes %s not loaded', path)
        return {}


def fix_place(place, fixes):
    """Restore town names where USGS shows '?' for a letter with a diacritic."""
    for broken, right in fixes.items():
        if broken in place:
            place = place.replace(broken, right)
    return place


def load_history(db, path=HISTORY_FILE, fixes_path=PLACE_FIXES_FILE):
    """Saved FDSN snapshot → past alerts, once per snapshot (see the top of the file). No commit (the caller)."""
    alerts.init(db)
    name = Path(path).name
    try:
        feed = json.loads(Path(path).read_text(encoding='utf-8'))
        features = feed['features']
        if not isinstance(features, list):
            raise ValueError('features is not a list')
        generated = (feed.get('metadata') or {}).get('generated')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning('usgs: history %s not loaded (%s: %s)', name, type(exc).__name__, exc)
        return {'events': 0, 'afghanistan': 0, 'new': 0, 'error': '%s: %s' % (type(exc).__name__, exc)}
    state = store.get_setting(db, HISTORY_KEY, {}) or {}
    if state.get('file') == name and state.get('generated') == generated:
        return {'events': len(features), 'afghanistan': state.get('afghanistan', 0), 'new': 0, 'skipped': True}
    quakes = [q for q in (parse_feature(f) for f in features if isinstance(f, dict))
              if q and is_afghanistan(q) and q['magnitude'] is not None and q['magnitude'] >= HISTORY_MIN_MAGNITUDE]
    fixes = place_fixes(fixes_path)
    known = _known_ids(db)
    new = 0
    for quake in sorted(quakes, key=lambda q: q['time']):
        if quake['ids'] & known:
            continue                                      # already there (live feed or an earlier load)
        quake['place'] = fix_place(quake['place'], fixes)
        try:
            alerts.add_past(db, _alert_data(quake), 'usgs')
        except ApiError as exc:
            log.warning('usgs: history event %s skipped (%s)', quake['id'], exc.code)
            continue
        known |= quake['ids']
        new += 1
    store.set_setting(db, HISTORY_KEY, {'file': name, 'generated': generated, 'loaded_at': utcnow(),
                                        'afghanistan': len(quakes), 'new': new})
    log.info('usgs: history %s — %d earthquakes M%.1f+ in Afghanistan, %d new', name, len(quakes),
             HISTORY_MIN_MAGNITUDE, new)
    return {'events': len(features), 'afghanistan': len(quakes), 'new': new}


# ---------------------------------------------------------------- status and background

def status(db):
    """{last_poll, ok, last_error, last_event, new_total, feed, running, history} — for the admin panel."""
    state = store.get_setting(db, STATE_KEY, {}) or {}
    history = store.get_setting(db, HISTORY_KEY, None)
    last_event, total = None, 0
    try:
        row = db.execute("SELECT id, source_id, place, magnitude, depth_km, starts_at, status FROM alerts "
                         "WHERE source = 'usgs' ORDER BY starts_at DESC, id DESC LIMIT 1").fetchone()
        last_event = dict(row) if row is not None else None
        total = db.execute("SELECT count(*) FROM alerts WHERE source = 'usgs'").fetchone()[0]
    except Exception:   # no alerts tables yet
        log.debug('usgs: no alerts table yet')
    return {'last_poll': state.get('last_poll'), 'ok': state.get('ok'), 'last_error': state.get('last_error'),
            'last_event': last_event, 'new_total': total, 'feed': state.get('feed'),
            'running': bool(_thread and _thread.is_alive()), 'history': history}


def _is_off(value):
    return value is False or str(value).strip().lower() in ('0', 'false', 'no', 'off')


def start(app):
    """Background feed polling (daemon thread) → the thread, or None (turned off in app.config)."""
    global _thread
    config = getattr(app, 'config', None) or {}
    if _is_off(config.get('usgs', True)):
        log.info('usgs: polling is off')
        return None
    if _thread is not None and _thread.is_alive():
        return _thread
    try:                                                  # local file, no network: fast, before the first poll
        with store.transaction(app.db_path) as db:
            load_history(db)
    except Exception:
        log.exception('usgs: history load failed')
    interval = float(config.get('usgs_interval', INTERVAL_S) or INTERVAL_S)
    stopping = getattr(app, 'stopping', None)
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(app.db_path, interval, stopping), name='panah-usgs',
                               daemon=True)
    _thread.start()
    return _thread


def stop(timeout=5):
    """Stop background polling (for tests and shutdown)."""
    _stop.set()
    if _thread is not None:
        _thread.join(timeout)


def _stopped(stopping):
    return _stop.is_set() or (stopping is not None and stopping.is_set())


def _loop(db_path, interval, stopping):
    log.info('usgs: polling every %g s', interval)
    while not _stopped(stopping):
        try:
            with store.transaction(db_path) as db:
                poll_once(db)
        except Exception:
            log.exception('usgs: poll failed')
        waited = 0.0
        while waited < interval and not _stopped(stopping):   # wake up often so that stopping is quick
            step = min(0.5, interval - waited)
            _stop.wait(step)
            waited += step
