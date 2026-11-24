"""Processing and automatic ADEPT authorization. Compatible with Python 3.4+."""
from __future__ import unicode_literals
import base64
import hashlib
import json
import os
from .compat import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET

VERSION = '1.0.0'
MAX_UPLOAD = 2 * 1024 * 1024
KEY_FILES = ('device.xml', 'activation.xml', 'devicesalt')


class UserError(Exception):
    pass


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(str(path), 0o700)
    return path


def xml(data):
    # No DTDs, external entities or entity expansion in tickets or key imports.
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper() or b'\x00' in data:
        raise UserError('XML declarations with entities or DTDs are not supported.')
    try:
        return ET.fromstring(data)
    except (ET.ParseError, ValueError):
        raise UserError('The selected file is not valid XML.')


def is_pdf(path):
    if not path.is_file():
        return False
    with path.open('rb') as stream:
        return stream.read(5) == b'%PDF-'


def local_name(tag):
    return tag.rsplit('}', 1)[-1]


def ticket_title(data):
    root = xml(data)
    if local_name(root.tag) != 'fulfillmentToken':
        raise UserError('Choose an Adobe ACSM fulfillment ticket (.acsm).')
    title = 'document'
    for item in root.iter():
        if local_name(item.tag) == 'format' and item.text:
            if item.text.strip().lower() in ('application/epub+zip', 'application/epub'):
                raise UserError('This ticket is for an EPUB. Wallaby Ripper currently accepts PDF tickets.')
        if local_name(item.tag) == 'title' and item.text:
            title = item.text
    name = re.sub(r'[^\w .()-]', '_', title, flags=re.UNICODE).strip(' .')[:120]
    return (name or 'document') + '.pdf'


def run_tool(args, cwd, timeout=660, password=None):
    # Password travels through stdin, never through argv or an environment variable.
    with tempfile.TemporaryFile() as log:
        command = [sys.executable, str(Path(__file__).resolve().parent / 'limit_exec.py')] + [str(a) for a in args]
        proc = subprocess.Popen(command, cwd=str(cwd),
                                stdin=subprocess.PIPE, stdout=log, stderr=log)
        try:
            proc.communicate(None if password is None else (password + '\n').encode('utf-8'),
                             timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise UserError('The operation timed out. Check your connection and try again.')
        log.seek(0)
        message = log.read(128 * 1024).decode('utf-8', 'replace')
    if proc.returncode:
        # Recognize useful upstream errors without disclosing account/key data or raw XML.
        for code, help_text in (
            ('E_ADEPT_DOCUMENT_TYPE_UNKNOWN', 'The provider returned an unsupported document type.'),
            ('E_ADEPT_DISTRIBUTOR_AUTH', 'The book provider rejected this authorization.'),
            ('E_ADEPT_REQUEST_EXPIRED', 'This ACSM ticket has expired. Download a fresh ticket.'),
            ('E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER', 'This ticket is tied to another authorization. Open Settings to sign in to its Adobe account or import its authorization.'),
            ('E_ADEPT_NO_TOKEN', 'The provider did not supply a usable license.'),
            ('E_AUTH_FAILED', 'Adobe rejected the account credentials.'),
            ('E_ADEPT_ACTIVATION', 'Adobe could not activate this device.'),
        ):
            if code in message:
                raise UserError(help_text)
        codes = re.findall(r'\bE_[A-Z0-9_]+\b', message)
        suffix = ' Provider code: ' + codes[0] + '.' if codes else ''
        raise UserError('The processing tool could not complete this step. Check the ticket and authorization in Settings.' + suffix)
    return message


class Engine(object):
    def __init__(self, root=None, config=None, cache=None, runner=None):
        self.root = Path(root or Path(__file__).resolve().parent.parent).resolve()
        self.config = private_dir(Path(config or os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'wallaby-ripper').resolve()
        self.cache = private_dir(Path(cache or os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'wallaby-ripper').resolve()
        self.adept = self.config / 'adept'
        self.runner = runner or run_tool
        self.lock = threading.Lock()
        self.jobs_lock = threading.Lock()
        self.jobs = {}
        self.backend = None
        self.build_state = 'waiting'
        self.build_error = ''

    def prepare(self):
        self.build_state = 'building'
        try:
            fingerprint = VERSION + os.uname().machine
            for source in sorted((self.root / 'vendor').rglob('*')):
                if source.is_file():
                    fingerprint += hashlib.sha256(source.read_bytes()).hexdigest()
            fingerprint += (self.root / 'scripts/build-native.sh').read_text()
            release = Path('/etc/os-release')
            if release.exists():
                fingerprint += release.read_text()
            key = hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:16]
            target = private_dir(self.cache / ('tools-' + key))
            # A process lock also prevents two launches from compiling into the same cache.
            import fcntl
            with (self.cache / 'build.lock').open('w') as guard:
                fcntl.flock(guard, fcntl.LOCK_EX)
                if not (target / 'ready').exists():
                    with (self.cache / 'build.log').open('wb') as log:
                        p = subprocess.Popen(['sh', str(self.root / 'scripts/build-native.sh'),
                                              str(self.root / 'vendor/libgourou'), str(target)],
                                             stdout=log, stderr=log, start_new_session=True)
                        try:
                            p.wait(timeout=600)
                        except subprocess.TimeoutExpired:
                            os.killpg(p.pid, signal.SIGKILL)
                            p.wait()
                            raise UserError('Building the processing tools timed out.')
                    if p.returncode:
                        raise UserError('Could not build processing tools. See ' + str(self.cache / 'build.log'))
                    (target / 'ready').write_text(VERSION)
            self.backend = target
            self.build_state = 'ready'
        except Exception as error:
            self.build_state = 'error'
            self.build_error = str(error)

    def authorization_exists(self):
        return all((self.adept / name).is_file() for name in KEY_FILES)

    def valid_authorization(self, files):
        if set(files) != set(KEY_FILES):
            raise UserError('Import device.xml, activation.xml and devicesalt together.')
        if not files['devicesalt'] or len(files['devicesalt']) != 16:
            raise UserError('devicesalt must be a 16-byte ADEPT device key.')
        if local_name(xml(files['device.xml']).tag) != 'deviceInfo':
            raise UserError('device.xml is not an ADEPT device file.')
        activation = xml(files['activation.xml'])
        if local_name(activation.tag) != 'activationInfo':
            raise UserError('activation.xml is not an ADEPT activation file.')
        names = set(local_name(node.tag) for node in activation.iter())
        if not {'privateLicenseKey', 'activationToken'}.issubset(names):
            raise UserError('The authorization has no usable license key or activation token.')

    def install_authorization(self, files, source):
        self.valid_authorization(files)
        stage = Path(tempfile.mkdtemp(prefix='auth-', dir=str(self.config)))
        backup = self.config / ('authorization-backup-' + uuid.uuid4().hex)
        try:
            for name, data in files.items():
                (stage / name).write_bytes(data)
                os.chmod(str(stage / name), 0o600)
            if self.adept.exists():
                self.adept.rename(backup)
            try:
                stage.rename(self.adept)
            except Exception:
                if backup.exists():
                    backup.rename(self.adept)
                raise
            (self.config / 'authorization.json').write_text(json.dumps({'source': source}))
        finally:
            if stage.exists():
                shutil.rmtree(str(stage))
        # Keep any previous authorization: old books may depend on it.

    def discover(self):
        home = Path.home()
        base = Path(os.environ.get('XDG_CONFIG_HOME', str(home / '.config')))
        candidates = [base / 'adept', home / '.adept',
                      base / 'calibre/plugins/DeACSM/account',
                      base / 'calibre/plugins/ACSMInput/account']
        override = os.environ.get('ADEPT_DIR')
        if override:
            candidates.insert(0, Path(override).expanduser())
        for directory in candidates:
            if all((directory / name).is_file() for name in KEY_FILES):
                try:
                    files = {name: (directory / name).read_bytes() for name in KEY_FILES}
                    self.install_authorization(files, 'Existing local authorization')
                    return True
                except (UserError, OSError):
                    continue
        return False

    def activate(self, email=None, password=None):
        stage = Path(tempfile.mkdtemp(prefix='activation-', dir=str(self.config)))
        try:
            destination = stage / 'adept'
            args = [self.backend / 'adept_activate', '-O', destination]
            args += ['-u', email] if email else ['--anonymous']
            self.runner(args, stage, timeout=180, password=password)
            files = {name: (destination / name).read_bytes() for name in KEY_FILES}
            self.install_authorization(files, 'Adobe account' if email else 'Automatic anonymous authorization')
        finally:
            shutil.rmtree(str(stage))

    def ensure_authorization(self, progress):
        if self.authorization_exists():
            progress('authorizing', 'Using the saved authorization key…')
            return
        progress('authorizing', 'Preparing automatic authorization…')
        if not self.discover():
            progress('authorizing', 'Obtaining a new anonymous authorization key…')
            self.activate()
        else:
            progress('authorizing', 'Found an existing local authorization key…')

    def status(self):
        source = 'Automatic authorization on first conversion'
        metadata = self.config / 'authorization.json'
        if metadata.exists():
            try:
                source = json.loads(metadata.read_text())['source']
            except (ValueError, KeyError):
                pass
        return {'backend': self.build_state, 'error': self.build_error,
                'authorized': self.authorization_exists(), 'authorization': source}

    def start(self, data):
        if self.build_state != 'ready':
            raise UserError('The processing tools are still being prepared.')
        filename = ticket_title(data)
        if not self.lock.acquire(False):
            raise UserError('Another operation is running. Wait for it to finish.')
        job_id = uuid.uuid4().hex
        folder = Path(tempfile.mkdtemp(prefix='job-', dir=str(self.cache)))
        job = {'id': job_id, 'state': 'queued', 'message': 'Starting…',
               'filename': filename, 'folder': folder, 'created': time.time()}
        with self.jobs_lock:
            self.jobs[job_id] = job
        thread = threading.Thread(target=self.convert, args=(job, data))
        thread.daemon = True
        thread.start()
        return job_id

    def update(self, job, state, message):
        with self.jobs_lock:
            job.update(state=state, message=message)

    def convert(self, job, data):
        folder = job['folder']
        try:
            self.ensure_authorization(lambda state, message: self.update(job, state, message))
            ticket = folder / 'ticket.acsm'
            encrypted = folder / 'encrypted.pdf'
            output = folder / 'decrypted.pdf'
            ticket.write_bytes(data)
            self.update(job, 'downloading', 'Downloading the encrypted PDF…')
            self.runner([self.backend / 'acsmdownloader', '-D', self.adept,
                         '-o', encrypted, ticket], folder)
            if not is_pdf(encrypted):
                raise UserError('The provider returned something other than a PDF.')
            self.update(job, 'decrypting', 'Removing PDF encryption…')
            self.runner([self.backend / 'adept_remove', '-D', self.adept,
                         '-o', output, encrypted], folder)
            self.update(job, 'checking', 'Checking the finished PDF…')
            if not is_pdf(output):
                raise UserError('The processing tool did not produce a PDF.')
            info = self.runner(['pdfinfo', output], folder, timeout=30)
            if not re.search(r'^Encrypted:\s+no\s*$', info, re.MULTILINE):
                raise UserError('The resulting PDF is still encrypted. No download was sent.')
            ticket.unlink()
            encrypted.unlink()
            self.update(job, 'ready', 'Your PDF is ready. Downloading…')
        except UserError as error:
            self.update(job, 'error', str(error))
            shutil.rmtree(str(folder), ignore_errors=True)
        except Exception:
            self.update(job, 'error', 'Processing failed. Check your authorization and try a fresh ACSM ticket.')
            shutil.rmtree(str(folder), ignore_errors=True)
        finally:
            self.lock.release()

    def public_job(self, job_id):
        with self.jobs_lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise UserError('This download has expired. Select the ACSM file again.')
            return {key: job[key] for key in ('id', 'state', 'message', 'filename')}

    def clean(self):
        with self.jobs_lock:
            expired = [key for key, value in self.jobs.items()
                       if value['state'] in ('ready', 'error') and time.time() - value['created'] > 3600]
            for key in expired:
                shutil.rmtree(str(self.jobs.pop(key)['folder']), ignore_errors=True)
