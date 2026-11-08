"""Tests for live: who receives which events, event ids, replay by Last-Event-ID, the SSE stream through the server."""
import http.client
import time
import types
import unittest
from unittest import mock

from support import Running, fake_auth  # support adds app/ to sys.path

from server import live  # noqa: E402

DEVICE_A = 'deviceAAAAAAAAAAAAAAAA'
DEVICE_B = 'deviceBBBBBBBBBBBBBBBB'


def drain(sub):
    """Everything in the subscriber's queue → [(event, text)]."""
    found = []
    while True:
        item = sub.get(timeout=0)
        if item is None:
            return found
        found.append((item.name, item.text))


class HubTest(unittest.TestCase):
    def setUp(self):
        self.hub = live.Hub()
        self.guest = self.hub.subscribe(device_id=DEVICE_A)
        self.user1 = self.hub.subscribe(user_id=1, device_id=DEVICE_B)
        self.user2 = self.hub.subscribe(user_id=2)
        self.admin = self.hub.subscribe(admin=True)

    def names(self, sub):
        return [name for name, _ in drain(sub)]

    def test_routing(self):
        self.assertEqual(self.hub.publish('alert', {'n': 1}, everyone=True), 4)
        self.assertEqual(self.hub.publish('sos_message', {'n': 2}, user_id=1), 1)
        self.assertEqual(self.hub.publish('sos_case', {'n': 3}, admins=True), 1)
        self.assertEqual(self.hub.publish('sos_message', {'n': 4}, device_id=DEVICE_A), 1)
        self.assertEqual(self.hub.publish('sos_message', {'n': 5}, user_id=[2, 3], admins=True), 2)
        self.assertEqual(self.hub.publish('sos_location', {'n': 6}, user_id='1'), 1, "id 1 and '1' are the same")
        self.assertEqual(self.names(self.guest), ['alert', 'sos_message'])
        self.assertEqual(self.names(self.user1), ['alert', 'sos_message', 'sos_location'])
        self.assertEqual(self.names(self.user2), ['alert', 'sos_message'])
        self.assertEqual(self.names(self.admin), ['alert', 'sos_case', 'sos_message'])

    def test_guest_gets_only_everyone_and_own_device(self):
        plain_guest = self.hub.subscribe()
        self.hub.publish('sos_message', {}, user_id=1)
        self.hub.publish('sos_case', {}, admins=True)
        self.hub.publish('sos_message', {}, device_id=DEVICE_B)
        self.hub.publish('alert_end', {}, everyone=True)
        self.assertEqual(self.names(plain_guest), ['alert_end'])
        self.assertEqual(self.names(self.guest), ['alert_end'])

    def test_payload_and_ids(self):
        self.hub.publish('alert', {'title': 'هشدار سیلاب', 'km': 1.2}, everyone=True)
        self.hub.publish('alert_end', {'id': 7}, everyone=True)
        first, second = self.user2.get(0), self.user2.get(0)
        self.assertGreater(second.id, first.id)
        self.assertIn('هشدار سیلاب', first.text, 'Dari in JSON as is')
        self.assertEqual(live.format_event(first),
                         'id: %d\nevent: alert\ndata: {"title":"هشدار سیلاب","km":1.2}\n\n' % first.id)

    def test_no_recipients_and_bad_name(self):
        with self.assertLogs('server.live', 'WARNING'):
            self.assertEqual(self.hub.publish('alert', {}), 0)
        for bad in ('', 'two words', 'line\nbreak', None):
            with self.assertRaises(ValueError):
                self.hub.publish(bad, {}, everyone=True)

    def test_replay_after_reconnect(self):
        self.hub.publish('alert', {'n': 1}, everyone=True)
        seen = self.user1.get(0).id
        self.hub.unsubscribe(self.user1)
        self.hub.publish('sos_message', {'n': 2}, user_id=1)
        self.hub.publish('sos_message', {'n': 3}, user_id=2)
        self.hub.publish('alert_end', {'n': 4}, everyone=True)
        back = self.hub.subscribe(user_id=1, last_event_id=str(seen))
        self.assertEqual(drain(back), [('sos_message', '{"n":2}'), ('alert_end', '{"n":4}')])
        fresh = self.hub.subscribe(user_id=1)
        self.assertEqual(drain(fresh), [], 'without Last-Event-ID: only new events')
        junk = self.hub.subscribe(user_id=1, last_event_id='junk')
        self.assertEqual(drain(junk), [])

    def test_slow_client_is_closed_not_blocking(self):
        slow = self.hub.subscribe(user_id=9)
        with mock.patch.object(slow.queue, 'put_nowait', side_effect=[__import__('queue').Full(), None]):
            self.hub.publish('alert', {}, user_id=9)
        self.assertEqual(self.hub.count(), 4, 'slow subscriber dropped, the others stayed')

    def test_close_all(self):
        self.hub.close_all()
        self.assertEqual(self.hub.count(), 0)
        self.assertIs(self.guest.get(0), live._CLOSE)


class StreamOverHttpTest(unittest.TestCase):
    """The /api/stream stream through a real server; the session comes from a fake auth.who."""

    def test_headers_and_greeting(self):
        with Running() as srv:
            sse = srv.client().stream()
            try:
                self.assertEqual(sse.status, 200)
                self.assertEqual(sse.headers['Content-Type'], 'text/event-stream')
                self.assertEqual(sse.headers['Cache-Control'], 'no-cache, no-transform')
                self.assertEqual(sse.headers['X-Accel-Buffering'], 'no', 'proxies must not buffer')
                self.assertIn('panah_device=', sse.headers.get('Set-Cookie', ''))
            finally:
                sse.close()

    def test_events_by_recipient(self):
        with Running(engines=[('auth', fake_auth())]) as srv:
            clients = {name: srv.client() for name in ('guest', 'guest_b', 'user1', 'user2', 'admin')}
            clients['guest'].cookies['panah_device'] = DEVICE_A
            clients['guest_b'].cookies['panah_device'] = DEVICE_B
            clients['user1'].cookies['panah_session'] = 'tok-user-1'
            clients['user2'].cookies['panah_session'] = 'tok-user-2'
            clients['admin'].cookies['panah_session'] = 'tok-admin'
            streams = {name: client.stream() for name, client in clients.items()}
            try:
                srv.wait_listeners(5)
                live.publish('alert', {'title': 'هشدار سیلاب'}, everyone=True)
                live.publish('sos_message', {'to': 'user1'}, user_id=1)
                live.publish('sos_case', {'to': 'admins'}, admins=True)
                live.publish('sos_message', {'to': 'device A'}, device_id=DEVICE_A)
                live.publish('sos_location', {'to': 'nobody'}, user_id=99)
                live.publish('alert_end', {'id': 1}, everyone=True)
                got = {name: sse.events_until('alert_end') for name, sse in streams.items()}
            finally:
                for sse in streams.values():
                    sse.close()
        everyone = [('alert', {'title': 'هشدار سیلاب'})]
        end = [('alert_end', {'id': 1})]
        self.assertEqual(got['guest'], everyone + [('sos_message', {'to': 'device A'})] + end)
        self.assertEqual(got['guest_b'], everyone + end)
        self.assertEqual(got['user1'], everyone + [('sos_message', {'to': 'user1'})] + end)
        self.assertEqual(got['user2'], everyone + end)
        self.assertEqual(got['admin'], everyone + [('sos_case', {'to': 'admins'})] + end)

    def test_heartbeat_comment(self):
        with mock.patch.object(live, 'HEARTBEAT_SECONDS', 0.2):
            with Running() as srv:
                sse = srv.client().stream()
                try:
                    block = sse.next()
                finally:
                    sse.close()
        self.assertEqual(block, {'comments': ['ping']})

    def test_event_ids_and_last_event_id(self):
        with Running() as srv:
            client = srv.client()
            sse = client.stream()
            try:
                srv.wait_listeners(1)
                live.publish('alert', {'n': 1}, everyone=True)
                first = sse.next()
            finally:
                sse.close()
            live.publish('alert', {'n': 2}, everyone=True)
            live.publish('alert_end', {'n': 3}, everyone=True)
            again = client.stream(headers={'Last-Event-ID': first['id']})
            try:
                missed = again.events_until('alert_end')
            finally:
                again.close()
        self.assertTrue(first['id'].isdigit())
        self.assertEqual(missed, [('alert', {'n': 2}), ('alert_end', {'n': 3})])

    def test_listener_released_after_disconnect_and_stop(self):
        with mock.patch.object(live, 'HEARTBEAT_SECONDS', 0.1):
            with Running() as srv:
                sse = srv.client().stream()
                srv.wait_listeners(1)
                sse.close()
                from support import wait_until
                self.assertTrue(wait_until(lambda: srv.app.hub.count() == 0), 'client left: subscription removed')
                kept = srv.client().stream()
                srv.wait_listeners(1)
                srv.app.stop()
                with self.assertRaises(EOFError):
                    while True:
                        kept.next()
                kept.close()


class ShortStreamTest(unittest.TestCase):
    """Short streams for proxies that hold a response until it ends (Cloudflare quick tunnels)."""

    def read_all(self, sse):
        """Every block until the server ends the response."""
        blocks = []
        try:
            while True:
                blocks.append(sse.next())
        except EOFError:
            return blocks

    def test_ends_after_event_and_resumes_by_last_event_id(self):
        with Running(config={'short_streams': True}) as srv:
            client = srv.client()
            sse = client.stream()
            try:
                srv.wait_listeners(1)
                live.publish('alert', {'n': 1}, everyone=True)
                first = self.read_all(sse)
            finally:
                sse.close()
            live.publish('alert', {'n': 2}, everyone=True)  # while the browser waits 1 s to reconnect
            again = client.stream(headers={'Last-Event-ID': first[-1]['id']})
            try:
                srv.wait_listeners(1)
                second = self.read_all(again)
            finally:
                again.close()
        self.assertEqual([(b.get('event'), b.get('data')) for b in first], [('alert', {'n': 1})],
                         'the response ends right after the event')
        self.assertEqual([(b.get('event'), b.get('data')) for b in second], [('alert', {'n': 2})],
                         'the event published in between arrives after reconnecting')

    def test_trycloudflare_host_turns_it_on(self):
        with mock.patch.object(live, 'SHORT_STREAM_SECONDS', 0.3):
            with Running() as srv:
                started = time.monotonic()
                sse = srv.client().stream(headers={'Host': 'gentle-fox-1234.trycloudflare.com'})
                try:
                    rest = self.read_all(sse)
                finally:
                    sse.close()
                took = time.monotonic() - started
        self.assertEqual(rest, [], 'no events: the response just ends')
        self.assertLess(took, 5, 'ended after the short time, not after a heartbeat')

    def test_retry_hint_and_normal_mode(self):
        with Running(config={'short_streams': True}) as srv:
            conn = http.client.HTTPConnection('127.0.0.1', srv.port, timeout=5)
            conn.request('GET', '/api/stream')
            first_line = conn.getresponse().readline()
            conn.close()
        self.assertEqual(first_line, b'retry: 1000\n')
        app = types.SimpleNamespace(config={})
        for host, short in (('abc.trycloudflare.com', True), ('ABC.TryCloudflare.com:443', True),
                            ('localhost:8000', False), ('eviltrycloudflare.com', False), ('', False)):
            req = types.SimpleNamespace(app=app, headers={'Host': host})
            self.assertIs(live.is_short(req), short, host)


if __name__ == '__main__':
    unittest.main()
