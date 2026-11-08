"""live — PANAH live updates (SSE): the server itself "pushes" events to the browser.

publish(event, data, user_id=None, device_id=None, admins=False, everyone=False) → how many connections got it.
  event — event name: alert, alert_end, sos_case, sos_message, sos_location;
  data  — dict, goes to the browser as JSON (Dari as is);
  recipients (several at once are fine):
    user_id   — resident: id or list of ids;
    device_id — device (cookie panah_device): a guest without login, e.g. their SOS chat; id or list;
    admins    — all admins;
    everyone  — everybody, guests included.
Who gets what: a guest — events for their device and everyone; a resident — their own, their device's and everyone;
an admin — admins, their device's and everyone.

GET /api/stream — SSE stream (Content-Type: text/event-stream). Each event:
    id: 1762390000123
    event: alert
    data: {"id": 5, ...}
Every 15 s a heartbeat comment ': ping'. The browser (EventSource) reconnects by itself and sends Last-Event-ID:
missed events among the last 200 are sent again. Who is connected (guest, resident, admin) is decided on connect:
after login or logout the stream is opened again.

Short streams — for proxies that hold a response until it ends (Cloudflare quick tunnels, *.trycloudflare.com, do
not support SSE): the response ends right after the first event(s) or after 20 s, with 'retry: 1000'. EventSource
reconnects by itself and Last-Event-ID brings whatever was published in between, so nothing is lost; the client
also re-reads its data on every reconnect. On when app.config['short_streams'] is true or the Host header is a
*.trycloudflare.com name.
"""
import collections
import json
import logging
import queue
import re
import threading
import time

HEARTBEAT_SECONDS = 15
REPLAY_SIZE = 200
QUEUE_SIZE = 500
RETRY_MS = 3000
SHORT_STREAM_SECONDS = 20   # short streams: the longest response without events
SHORT_RETRY_MS = 1000       # short streams: the browser reconnects after 1 s
SHORT_HOSTS = ('.trycloudflare.com',)

log = logging.getLogger(__name__)

_CLOSE = object()
_EVENT_NAME = re.compile(r'^[A-Za-z0-9_.:-]{1,64}$')

Event = collections.namedtuple('Event', 'id name text users devices admins everyone')


def _id_set(value):
    """id or list of ids → set of strings (1 and '1' are the same)."""
    if value is None:
        return frozenset()
    if isinstance(value, (list, tuple, set, frozenset)):
        return frozenset(str(item) for item in value if item is not None and item != '')
    if value == '':
        return frozenset()
    return frozenset([str(value)])


class Subscriber:
    """One connection to /api/stream: who it is and its own event queue."""

    def __init__(self, user_id=None, device_id=None, admin=False):
        self.user_id = None if user_id is None or user_id == '' else str(user_id)
        self.device_id = device_id or None
        self.admin = bool(admin)
        self.queue = queue.Queue(QUEUE_SIZE)

    def wants(self, event):
        return bool(
            event.everyone
            or (event.admins and self.admin)
            or (self.user_id is not None and self.user_id in event.users)
            or (self.device_id is not None and self.device_id in event.devices)
        )

    def get(self, timeout=None):
        """Next event; None if nothing came within timeout seconds; _CLOSE if the stream is closed."""
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None


class Hub:
    """Event dispatcher: subscribers and recent events for re-sending."""

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers = set()
        self._recent = collections.deque(maxlen=REPLAY_SIZE)
        # ids keep growing across server restarts: a Last-Event-ID from the previous run does no harm
        self._next_id = int(time.time() * 1000)

    def subscribe(self, user_id=None, device_id=None, admin=False, last_event_id=None):
        """New subscriber; with last_event_id it gets the missed events addressed to it right away."""
        sub = Subscriber(user_id, device_id, admin)
        try:
            last = int(str(last_event_id).strip()) if last_event_id not in (None, '') else None
        except ValueError:
            last = None
        with self._lock:
            self._subscribers.add(sub)
            if last is not None:
                for event in self._recent:
                    if event.id > last and sub.wants(event):
                        sub.queue.put_nowait(event)
        return sub

    def unsubscribe(self, sub):
        with self._lock:
            self._subscribers.discard(sub)

    def publish(self, event, data, user_id=None, device_id=None, admins=False, everyone=False):
        """Send the event to its recipients → how many connections got it."""
        if not isinstance(event, str) or not _EVENT_NAME.match(event):
            raise ValueError('live.publish: bad event name %r' % (event,))
        text = json.dumps(data, ensure_ascii=False, separators=(',', ':'), default=_json_default)
        users, devices = _id_set(user_id), _id_set(device_id)
        if not (users or devices or admins or everyone):
            log.warning('live.publish(%s): no recipients (user_id, device_id, admins, everyone)', event)
            return 0
        delivered = 0
        with self._lock:
            self._next_id += 1
            item = Event(self._next_id, event, text, users, devices, bool(admins), bool(everyone))
            self._recent.append(item)
            for sub in list(self._subscribers):
                if not sub.wants(item):
                    continue
                try:
                    sub.queue.put_nowait(item)
                    delivered += 1
                except queue.Full:  # client is not reading: close the stream, the browser reconnects and catches up
                    self._drop(sub)
        return delivered

    def _drop(self, sub):
        self._subscribers.discard(sub)
        while True:
            try:
                sub.queue.get_nowait()
            except queue.Empty:
                break
        sub.queue.put_nowait(_CLOSE)

    def count(self):
        """How many connections are listening now."""
        with self._lock:
            return len(self._subscribers)

    def close_all(self):
        """Close all streams (server shutdown)."""
        with self._lock:
            for sub in list(self._subscribers):
                self._drop(sub)


def _json_default(value):
    if hasattr(value, 'keys'):  # sqlite3.Row
        return {key: value[key] for key in value.keys()}
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


def format_event(event):
    """Event → a chunk of the SSE stream."""
    return 'id: %d\nevent: %s\ndata: %s\n\n' % (event.id, event.name, event.text)


HUB = Hub()


def publish(event, data, user_id=None, device_id=None, admins=False, everyone=False):
    """Send an event (see the module docstring) → how many connections got it."""
    return HUB.publish(event, data, user_id=user_id, device_id=device_id, admins=admins, everyone=everyone)


def stream(req):
    """GET /api/stream — keeps the connection open and writes this subscriber's events."""
    hub = getattr(req.app, 'hub', None) or HUB
    user_id = None
    if req.user:
        try:
            user_id = req.user['id']
        except (KeyError, IndexError, TypeError):
            user_id = None
    last_event_id = req.headers.get('Last-Event-ID') or req.query.get('last_event_id')
    short = is_short(req)
    sub = hub.subscribe(user_id=user_id, device_id=req.device_id, admin=bool(req.admin),
                        last_event_id=last_event_id)
    try:
        out = req.stream('text/event-stream')
        out.write('retry: %d\n: connected\n\n' % (SHORT_RETRY_MS if short else RETRY_MS))
        ends = time.monotonic() + SHORT_STREAM_SECONDS if short else None
        while True:
            wait = HEARTBEAT_SECONDS if ends is None else max(0.0, min(HEARTBEAT_SECONDS, ends - time.monotonic()))
            item = sub.get(timeout=wait)
            if item is _CLOSE:
                break
            if item is None:
                if ends is not None and time.monotonic() >= ends:
                    break  # short stream: end the response, the browser reconnects
                out.write(': ping\n\n')
                continue
            out.write(format_event(item))
            if ends is not None:  # short stream: send what is already queued, then end the response
                while True:
                    more = sub.get(timeout=0)
                    if more is None or more is _CLOSE:
                        break
                    out.write(format_event(more))
                break
    except (OSError, ValueError):  # client went away
        pass
    finally:
        hub.unsubscribe(sub)
    return None


def is_short(req):
    """Should this stream end after each event (proxy that holds responses)? See the module docstring."""
    config = getattr(req.app, 'config', None) or {}
    if config.get('short_streams'):
        return True
    host = (req.headers.get('Host') or '').strip().lower()
    host = host.rsplit(':', 1)[0] if host.count(':') == 1 else host  # drop the port
    return host.endswith(SHORT_HOSTS)


def routes():
    return [('GET', '/api/stream', stream)]
