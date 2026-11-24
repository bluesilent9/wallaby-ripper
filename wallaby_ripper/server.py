"""Token-protected loopback HTTP UI; no external Python dependencies."""
from __future__ import unicode_literals
import argparse
import base64
import binascii
import fcntl
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import re
import shutil
import signal
from socketserver import ThreadingMixIn
import threading
import time
from urllib.parse import quote, urlsplit
import uuid
import webbrowser

from .core import Engine, MAX_UPLOAD, KEY_FILES, UserError, VERSION


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, engine, port=0):
        self.engine = engine
        self.token = uuid.uuid4().hex + uuid.uuid4().hex
        HTTPServer.__init__(self, ('127.0.0.1', port), Handler)
        self.origin = 'http://127.0.0.1:' + str(self.server_port)
        self.url = self.origin + '/' + self.token + '/'


class Handler(BaseHTTPRequestHandler):
    server_version = 'WallabyRipper/' + VERSION
    protocol_version = 'HTTP/1.0'

    def setup(self):
        BaseHTTPRequestHandler.setup(self)
        self.connection.settimeout(30)

    def log_message(self, *args):
        pass  # Access URLs contain the session secret.

    def send_bytes(self, status, data, mime='application/json; charset=utf-8', extra=None):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def reply(self, status, value):
        self.send_bytes(status, json.dumps(value).encode('utf-8'))

    def route(self, write=False):
        expected_host = '127.0.0.1:' + str(self.server.server_port)
        if self.headers.get('Host') != expected_host:
            self.reply(403, {'error': 'Invalid host.'})
            return None
        prefix = '/' + self.server.token + '/'
        path = urlsplit(self.path).path
        if not path.startswith(prefix):
            self.reply(403, {'error': 'Open Wallaby Ripper from the application menu.'})
            return None
        origin = self.headers.get('Origin')
        if origin is not None and origin != self.server.origin:
            self.reply(403, {'error': 'Invalid origin.'})
            return None
        if write and self.headers.get('X-Wallaby-Token') != self.server.token:
            self.reply(403, {'error': 'Invalid session token.'})
            return None
        return path[len(prefix):]

    def do_GET(self):
        route = self.route()
        if route is None:
            return
        engine = self.server.engine
        engine.clean()
        if route == 'api/status':
            self.reply(200, engine.status())
        elif re.match(r'^api/jobs/[a-f0-9]{32}$', route):
            try:
                self.reply(200, engine.public_job(route.split('/')[-1]))
            except UserError as error:
                self.reply(404, {'error': str(error)})
        elif re.match(r'^download/[a-f0-9]{32}$', route):
            job_id = route.split('/')[-1]
            with engine.jobs_lock:
                job = engine.jobs.get(job_id)
                if not job or job['state'] != 'ready':
                    self.reply(404, {'error': 'This PDF is not available.'})
                    return
                # Open while holding lock so expiry cannot remove it before open.
                stream = (job['folder'] / 'decrypted.pdf').open('rb')
                size = os.fstat(stream.fileno()).st_size
                filename = job['filename']
            with stream:
                self.send_response(200)
                self.send_header('Content-Type', 'application/pdf')
                self.send_header('Content-Length', str(size))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Disposition', "attachment; filename=\"document.pdf\"; filename*=UTF-8''" + quote(filename))
                self.end_headers()
                shutil.copyfileobj(stream, self.wfile, 64 * 1024)
        elif route in ('', 'app.js', 'style.css', 'icon.png'):
            name = route or 'index.html'
            mime = {'index.html': 'text/html; charset=utf-8', 'app.js': 'text/javascript; charset=utf-8',
                    'style.css': 'text/css; charset=utf-8', 'icon.png': 'image/png'}[name]
            data = (engine.root / 'wallaby_ripper/web' / name).read_bytes()
            self.send_bytes(200, data, mime)
        else:
            self.reply(404, {'error': 'Not found.'})

    def body(self):
        if self.headers.get('Transfer-Encoding'):
            raise UserError('Chunked uploads are not supported.')
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise UserError('Invalid upload size.')
        if size < 1 or size > MAX_UPLOAD:
            raise UserError('Choose a file smaller than 2 MB.')
        data = self.rfile.read(size)
        if len(data) != size:
            raise UserError('The upload was incomplete.')
        return data

    def do_POST(self):
        route = self.route(write=True)
        if route is None:
            return
        engine = self.server.engine
        try:
            data = self.body()
            if route == 'api/convert':
                job_id = engine.start(data)
                self.reply(202, {'id': job_id})
                return
            if route not in ('api/activate', 'api/import'):
                self.reply(404, {'error': 'Not found.'})
                return
            if engine.build_state != 'ready':
                raise UserError('The processing tools are still being prepared.')
            if not engine.lock.acquire(False):
                raise UserError('Wait for the current conversion to finish.')
            try:
                payload = json.loads(data.decode('utf-8'))
                if not isinstance(payload, dict):
                    raise UserError('Invalid settings data.')
                if route == 'api/activate':
                    email = payload.get('email', '').strip()
                    password = payload.get('password', '')
                    if not isinstance(password, str) or '\n' in password or '\r' in password:
                        raise UserError('Invalid password.')
                    if email and not password:
                        raise UserError('Enter your Adobe password.')
                    engine.activate(email or None, password if email else None)
                else:
                    files = {name: base64.b64decode(payload[name], validate=True) for name in KEY_FILES}
                    engine.install_authorization(files, 'Imported authorization')
            finally:
                engine.lock.release()
            self.reply(200, engine.status())
        except (UserError, ValueError, KeyError, TypeError, binascii.Error) as error:
            self.reply(400, {'error': str(error) if isinstance(error, UserError) else 'Invalid settings data.'})
        except Exception:
            self.reply(500, {'error': 'The operation failed. Check the authorization and try again.'})


def main():
    parser = argparse.ArgumentParser(description='Wallaby Ripper — local ACSM to PDF')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--version', action='version', version=VERSION)
    args = parser.parse_args()
    os.umask(0o077)
    engine = Engine()
    # One server per user; subsequent menu launches reopen the existing browser UI.
    lock = (engine.cache / 'server.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            url = (engine.cache / 'session-url').read_text()
            if not args.no_browser:
                webbrowser.open(url)
            else:
                print(url, flush=True)
        except OSError:
            print('Wallaby Ripper is starting. Try again shortly.', flush=True)
        return
    # Remove leftovers from previous interrupted sessions; no authorization is removed.
    for old in engine.cache.glob('job-*'):
        if old.is_dir():
            shutil.rmtree(str(old), ignore_errors=True)
    server = Server(engine, args.port)
    (engine.cache / 'session-url').write_text(server.url)
    print(server.url, flush=True)
    preparation = threading.Thread(target=engine.prepare)
    preparation.daemon = True
    preparation.start()
    maintenance_stop = threading.Event()
    def maintain():
        while not maintenance_stop.wait(30):
            engine.clean()
    maintenance = threading.Thread(target=maintain)
    maintenance.daemon = True
    maintenance.start()
    if not args.no_browser:
        webbrowser.open(server.url)
    def stop(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        maintenance_stop.set()
        server.server_close()
        (engine.cache / 'session-url').unlink()
        for old in engine.cache.glob('job-*'):
            if old.is_dir():
                shutil.rmtree(str(old), ignore_errors=True)
        lock.close()


if __name__ == '__main__':
    main()
