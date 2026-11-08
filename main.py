"""PANAH launcher: server, database, browser and, if asked, a secure https link for phones.

    python app/main.py [--port 8000] [--host 0.0.0.0] [--db data/panah.db] [--no-browser] [--no-usgs]
                       [--tunnel path/to/cloudflared.exe]

Port: --port, else the PORT environment variable (cloud hosting such as Render sets it), else 8000. A busy --port or
8000 is skipped for the next free one; a PORT from the environment is used exactly (the hosting sends requests only
there) and no browser is opened. Data lives in the database folder (default: data/ in the project root): panah.db,
server.log, uploads/; a database from before the rename is taken over at the first start. ADMIN_PASSWORD in the
environment sets the demo admin's password at every start - do this on any public address. --tunnel starts a
Cloudflare quick tunnel to this server and prints its https address for phones (share-online.bat does this).
Stop with Ctrl+C.
"""
import sys

if sys.version_info < (3, 10):
    sys.stderr.write('PANAH needs Python 3.10 or newer. Download: https://www.python.org/downloads/\n')
    sys.exit(1)

import argparse  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import signal  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import webbrowser  # noqa: E402
from pathlib import Path  # noqa: E402

APP_DIR = Path(__file__).resolve().parent
BASE_DIR = APP_DIR.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from server import web  # noqa: E402

DEFAULT_PORT = 8000
TUNNEL_URL = re.compile(r'https://[-a-z0-9]+\.trycloudflare\.com', re.IGNORECASE)
TUNNEL_WAIT_SECONDS = 30

log = logging.getLogger('panah')

# The project's former working name: a database left from then is taken over once (adopt_old_database).
OLD_DB_NAME = 'deas.db'
OLD_DEMO_PASSWORD = 'deas1234'


def utf8_console():
    """The Windows console may be cp1252: write UTF-8 and replace what it cannot show instead of crashing."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError, OSError):
            pass


def setup_logging(data_dir):
    """Logging: to the console and to data/server.log (UTF-8)."""
    form = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s', '%Y-%m-%d %H:%M:%S')
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handlers = [logging.FileHandler(str(Path(data_dir) / 'server.log'), encoding='utf-8')]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.setFormatter(form)
        root.addHandler(handler)


def lan_address():
    """This computer's address in the local network (for the phone) or None. Sends nothing out."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('10.255.255.255', 1))
            address = probe.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith('127.') or address == '0.0.0.0' else address


def open_browser(url):
    try:
        webbrowser.open(url)
    except Exception:
        log.warning('could not open a browser, open %s yourself', url)


def stop_on_sigterm():
    """Hosting (Render) and process managers stop the server with SIGTERM: stop cleanly, as with Ctrl+C
    (the tunnel and the database are closed). Not available on Windows - there Ctrl+C or closing the window stops it."""
    def handler(signum, frame):
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, handler)
    except (AttributeError, ValueError, OSError):  # no SIGTERM, or not the main thread
        pass


def resolve_port(cli_port, environ):
    """→ (port, exact): --port first, then PORT from the environment (used exactly), then 8000."""
    if cli_port is not None:
        return cli_port, False
    value = (environ.get('PORT') or '').strip()
    if not value:
        return DEFAULT_PORT, False
    try:
        port = int(value)
    except ValueError:
        port = -1
    if not 0 < port < 65536:
        raise SystemExit('PORT must be a port number (1-65535), got %r' % value)
    return port, True


def adopt_old_database(db_path):
    """panah.db is not there yet, but a database of the former name (OLD_DB_NAME) is, in the same folder: rename it,
    with its -wal and -shm, so the earlier data stays → True if renamed. The main file goes last: if the move stops
    halfway, the next start finishes it. Another file name (--db other.db) is left alone."""
    db_path = Path(db_path)
    old = db_path.with_name(OLD_DB_NAME)
    if db_path.name != web.DEFAULT_DB.name or db_path.exists() or not old.is_file():
        return False
    for suffix in ('-wal', '-shm', ''):
        source = old.with_name(old.name + suffix)
        if source.exists():
            os.replace(source, db_path.with_name(db_path.name + suffix))
    return True


def renew_old_demo_password(app):
    """After adopt_old_database: the demo admin still has the former demo password → the current one, as written in
    the README (a password the owner chose stays) → True if changed."""
    auth = app.engines.get('auth')
    if auth is None:
        return False
    with app.transaction() as db:
        return auth.renew_demo_password(db, OLD_DEMO_PASSWORD)


def apply_admin_password(app, password):
    """ADMIN_PASSWORD: set the demo admin's password (create the admin if there is none) → True if done."""
    auth = app.engines.get('auth')
    if auth is None or not password:
        return False
    username = getattr(auth, 'DEMO_ADMIN_USERNAME', 'admin')
    with app.transaction() as db:
        if not auth.set_admin_password(db, username, password):
            auth.add_admin(db, username, password, getattr(auth, 'DEMO_ADMIN_PHONE', None),
                           getattr(auth, 'DEMO_ADMIN_NAME', ''))
    return True


class Tunnel:
    """Cloudflare quick tunnel to this server: `cloudflared tunnel --url http://127.0.0.1:<port>`.

    127.0.0.1, not localhost: the server may listen on IPv4 only, and localhost can mean IPv6 (::1) first.
    cloudflared's output goes to data/tunnel.log; on_url(url) is called once it prints the https address.
    """

    def __init__(self, command, port, log_path, on_url, on_exit=None):
        self.command = list(command) + ['tunnel', '--url', 'http://127.0.0.1:%d' % port]
        self.log_path, self.on_url, self.on_exit = Path(log_path), on_url, on_exit
        self.url, self.proc, self._stopping = None, None, False
        self.found = threading.Event()

    def start(self):
        env = dict(os.environ, NO_AUTOUPDATE='true')  # do not let cloudflared replace itself while it runs
        self.proc = subprocess.Popen(self.command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, env=env, text=True, encoding='utf-8',
                                     errors='replace')
        thread = threading.Thread(target=self._read, name='tunnel-output', daemon=True)
        thread.start()
        return self

    def _read(self):
        try:
            with open(self.log_path, 'a', encoding='utf-8') as out:
                for line in self.proc.stdout:
                    out.write(line)
                    out.flush()
                    if self.url is None:
                        found = TUNNEL_URL.search(line)
                        if found:
                            self.url = found.group(0).lower()
                            self.found.set()
                            self.on_url(self.url)
        finally:
            self.proc.stdout.close()
        code = self.proc.wait()
        if not self._stopping and self.on_exit is not None:
            self.on_exit(code)

    def stop(self):
        self._stopping = True
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def print_link(url):
    line = '=' * 64
    print('')
    print('  ' + line)
    print('  Secure link for phones (GPS works). Share it while this window is open:')
    print('')
    print('      %s' % url)
    print('')
    print('  The address is new at every start of share-online.bat.')
    print('  ' + line)
    print('')
    sys.stdout.flush()


def tunnel_failed(code, log_path):
    print('')
    print('  The secure link could not be made (cloudflared stopped, code %s). Is the internet on?' % code)
    print('  Details: %s' % log_path)
    print('  The app still works on this computer.')
    print('')
    sys.stdout.flush()


def parse_args(argv):
    parser = argparse.ArgumentParser(prog='main.py', description='PANAH - Disaster & Emergency Alert System server')
    parser.add_argument('--port', type=int, default=None,
                        help='port (default: PORT from the environment, else 8000; a busy one -> the next free one)')
    parser.add_argument('--db', default='data/panah.db',
                        help='database file; a relative path is taken from the project folder (default data/panah.db)')
    parser.add_argument('--host', default='0.0.0.0',
                        help='address to listen on (default 0.0.0.0 = all, phones in the Wi-Fi too; '
                             '127.0.0.1 = this computer only)')
    parser.add_argument('--no-browser', action='store_true', help='do not open a browser')
    parser.add_argument('--no-usgs', action='store_true', help='do not poll the USGS earthquake feed')
    parser.add_argument('--tunnel', metavar='CLOUDFLARED',
                        help='path to cloudflared(.exe): open a Cloudflare quick tunnel and print its https link')
    return parser.parse_args(argv)


def main(argv=None, environ=None):
    utf8_console()
    stop_on_sigterm()
    environ = os.environ if environ is None else environ
    args = parse_args(argv)
    port, exact = resolve_port(args.port, environ)
    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path
    db_path = db_path.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    setup_logging(db_path.parent)
    try:
        adopted = adopt_old_database(db_path)
    except OSError as exc:
        log.error('cannot rename the old database %s to %s (is another server window still open?): %s',
                  OLD_DB_NAME, db_path.name, exc)
        return 1
    if adopted:
        log.info('the old database %s was renamed to %s, its data is kept', OLD_DB_NAME, db_path.name)

    config = {'usgs': not args.no_usgs}
    if args.tunnel:
        config['short_streams'] = True  # quick tunnels hold SSE responses: end each one after its events
    app = web.App(db_path, config=config)
    app.setup()
    if adopted and renew_old_demo_password(app):
        log.info('demo admin password renewed (see README)')
    if apply_admin_password(app, environ.get('ADMIN_PASSWORD', '')):
        log.info('admin password set from ADMIN_PASSWORD')
    try:
        server = web.bind(app, args.host, port, tries=1 if exact else 20)
    except OSError as exc:
        log.error('cannot listen on port %s%s: %s', port, '' if exact else ' or the next ones', exc)
        return 1
    port = server.server_address[1]
    shown_host = 'localhost' if args.host in ('', '0.0.0.0', '::') else args.host
    url = 'http://%s:%d/' % ('[%s]' % shown_host if ':' in shown_host else shown_host, port)
    print('')
    print('  PANAH is running: %s' % url)
    lan = lan_address() if args.host in ('', '0.0.0.0', '::') else None
    if lan:
        print('  Phone in the same Wi-Fi: http://%s:%d/' % (lan, port))
    print('  Data: %s' % db_path.parent)
    print('  Stop: Ctrl+C')
    print('')
    sys.stdout.flush()

    app.start()
    tunnel = None
    if args.tunnel:
        tunnel_log = db_path.parent / 'tunnel.log'
        print('  Making the secure link for phones (Cloudflare quick tunnel)...')
        sys.stdout.flush()
        try:
            tunnel = Tunnel([args.tunnel], port, tunnel_log, print_link,
                            lambda code: tunnel_failed(code, tunnel_log)).start()
        except OSError as exc:
            log.error('cannot start %s: %s', args.tunnel, exc)
            tunnel_failed('-', tunnel_log)
        else:
            def remind():
                if not tunnel.found.wait(TUNNEL_WAIT_SECONDS) and tunnel.proc.poll() is None:
                    print('  Still waiting for the secure link... (details: %s)' % tunnel_log)
                    sys.stdout.flush()
            threading.Thread(target=remind, name='tunnel-remind', daemon=True).start()
    if not args.no_browser and not exact:
        timer = threading.Timer(0.8, open_browser, (url,))
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print('Stopping...')
    finally:
        if tunnel is not None:
            tunnel.stop()
        app.stop()
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
