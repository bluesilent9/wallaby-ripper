from __future__ import unicode_literals
import base64
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from wallaby_ripper.compat import Path
from wallaby_ripper.core import Engine, UserError, ticket_title, run_tool, MAX_UPLOAD
from wallaby_ripper.server import Server

TICKET = b'''<fulfillmentToken xmlns="http://ns.adobe.com/adept"><resourceItemInfo><metadata><title>Test / Book</title><format>application/pdf</format></metadata></resourceItemInfo></fulfillmentToken>'''
AUTH = {'device.xml': b'<deviceInfo/>',
        'activation.xml': b'<activationInfo><privateLicenseKey>test</privateLicenseKey><activationToken/></activationInfo>',
        'devicesalt': b'0123456789abcdef'}


def pdf_bytes():
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>',
               b'<< /Length 0 >>\nstream\n\nendstream']
    data = b'%PDF-1.4\n'
    offsets = []
    for i, content in enumerate(objects, 1):
        offsets.append(len(data))
        data += ('%d 0 obj\n' % i).encode() + content + b'\nendobj\n'
    start = len(data)
    data += b'xref\n0 5\n0000000000 65535 f \n'
    for offset in offsets:
        data += ('%010d 00000 n \n' % offset).encode()
    return data + ('trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % start).encode()


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.calls = []
        self.fail_tool = None
        self.encrypted_result = False
        root = Path(__file__).resolve().parent.parent
        self.engine = Engine(root=root, config=self.temp / 'config', cache=self.temp / 'cache', runner=self.fake_runner)
        self.engine.backend = self.temp
        self.engine.build_state = 'ready'
        self.engine.discover = lambda: False

    def tearDown(self):
        shutil.rmtree(str(self.temp))

    def fake_runner(self, args, cwd, timeout=660, password=None):
        tool = Path(args[0]).name
        self.calls.append((tool, [str(x) for x in args], password))
        if tool == self.fail_tool:
            raise UserError('Deliberate tool failure')
        if tool == 'adept_activate':
            folder = Path(args[args.index('-O') + 1])
            folder.mkdir()
            for name, content in AUTH.items():
                (folder / name).write_bytes(content)
        elif tool == 'acsmdownloader':
            Path(args[args.index('-o') + 1]).write_bytes(b'%PDF-1.4\nencrypted fixture')
        elif tool == 'adept_remove':
            Path(args[args.index('-o') + 1]).write_bytes(pdf_bytes())
        elif tool == 'pdfinfo':
            if self.encrypted_result:
                return 'Encrypted: yes\n'
            # Verify the generated PDF through the real system PDF parser.
            return run_tool(args, cwd, timeout)
        return ''

    def finish(self, job_id):
        end = time.time() + 10
        while time.time() < end:
            status = self.engine.public_job(job_id)
            if status['state'] in ('ready', 'error'):
                # Worker releases the operation lock immediately after setting terminal state.
                with self.engine.lock:
                    return status
            time.sleep(.01)
        self.fail('Worker timed out')

    def test_automatic_keys_conversion_and_encrypted_cleanup(self):
        job_id = self.engine.start(TICKET)
        self.assertEqual(self.finish(job_id)['state'], 'ready')
        self.assertEqual([x[0] for x in self.calls], ['adept_activate', 'acsmdownloader', 'adept_remove', 'pdfinfo'])
        self.assertIn('--anonymous', self.calls[0][1])
        job = self.engine.jobs[job_id]
        self.assertFalse((job['folder'] / 'encrypted.pdf').exists())
        self.assertFalse((job['folder'] / 'ticket.acsm').exists())
        self.assertEqual((job['folder'] / 'decrypted.pdf').read_bytes(), pdf_bytes())
        self.assertTrue(self.engine.authorization_exists())

    def test_saved_keys_are_reused(self):
        self.engine.install_authorization(AUTH, 'Test')
        self.finish(self.engine.start(TICKET))
        self.assertNotIn('adept_activate', [x[0] for x in self.calls])

    def test_existing_authorization_is_discovered(self):
        candidate = self.temp / 'existing'
        candidate.mkdir()
        for name, content in AUTH.items():
            (candidate / name).write_bytes(content)
        old = os.environ.get('ADEPT_DIR')
        os.environ['ADEPT_DIR'] = str(candidate)
        try:
            self.assertTrue(Engine.discover(self.engine))
            self.assertEqual((self.engine.adept / 'devicesalt').read_bytes(), AUTH['devicesalt'])
        finally:
            if old is None:
                del os.environ['ADEPT_DIR']
            else:
                os.environ['ADEPT_DIR'] = old

    def test_password_is_passed_only_on_stdin(self):
        self.engine.activate('user@example.test', 'secret value')
        args = self.calls[0][1]
        self.assertNotIn('secret value', args)
        self.assertEqual(self.calls[0][2], 'secret value')
        self.assertFalse(any(b'secret value' in p.read_bytes() for p in self.engine.adept.iterdir()))

    def test_previous_authorization_is_backed_up(self):
        self.engine.install_authorization(AUTH, 'Original')
        updated = dict(AUTH, devicesalt=b'fedcba9876543210')
        self.engine.install_authorization(updated, 'Replacement')
        backups = list(self.engine.config.glob('authorization-backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / 'devicesalt').read_bytes(), AUTH['devicesalt'])

    def test_invalid_authorization_cannot_replace_good_keys(self):
        self.engine.install_authorization(AUTH, 'Original')
        with self.assertRaises(UserError):
            self.engine.install_authorization(dict(AUTH, devicesalt=b'wrong'), 'Bad')
        self.assertEqual((self.engine.adept / 'devicesalt').read_bytes(), AUTH['devicesalt'])

    def test_failure_cleans_all_temporary_files(self):
        self.fail_tool = 'adept_remove'
        job_id = self.engine.start(TICKET)
        self.assertEqual(self.finish(job_id)['state'], 'error')
        self.assertFalse(self.engine.jobs[job_id]['folder'].exists())
        self.assertTrue(self.engine.authorization_exists())

    def test_encrypted_output_is_never_offered(self):
        self.encrypted_result = True
        job_id = self.engine.start(TICKET)
        self.assertEqual(self.finish(job_id)['state'], 'error')
        self.assertFalse(self.engine.jobs[job_id]['folder'].exists())

    def test_bad_xml_and_epub_rejected_before_fulfillment(self):
        for data in (b'bad', b'<other/>', b'<!DOCTYPE x [<!ENTITY e "bad">]><fulfillmentToken/>',
                     TICKET.replace(b'application/pdf', b'application/epub+zip')):
            with self.assertRaises(UserError):
                self.engine.start(data)
        self.assertFalse(self.calls)

    def test_filename_is_safe(self):
        self.assertEqual(ticket_title(TICKET), 'Test _ Book.pdf')
        self.assertNotIn('\n', ticket_title(TICKET.replace(b'Test / Book', b'../Bad&#10;Name')))

    def test_concurrent_operation_is_rejected(self):
        with self.engine.lock:
            with self.assertRaises(UserError):
                self.engine.start(TICKET)

    def test_expiry_removes_finished_pdf_and_job(self):
        job_id = self.engine.start(TICKET)
        self.finish(job_id)
        folder = self.engine.jobs[job_id]['folder']
        self.engine.jobs[job_id]['created'] -= 3601
        self.engine.clean()
        self.assertFalse(folder.exists())
        with self.assertRaises(UserError):
            self.engine.public_job(job_id)

    def test_http_upload_poll_download_and_security(self):
        server = Server(self.engine)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        try:
            page = urlopen(server.url)
            self.assertIn(b'Wallaby Ripper', page.read())
            self.assertIn("frame-ancestors 'none'", page.headers['Content-Security-Policy'])
            with self.assertRaises(HTTPError) as failure:
                urlopen(server.origin + '/api/status')
            self.assertEqual(failure.exception.code, 403)
            failure.exception.close()
            request = Request(server.url + 'api/convert', data=TICKET)
            with self.assertRaises(HTTPError) as failure:
                urlopen(request)
            self.assertEqual(failure.exception.code, 403)
            failure.exception.close()
            request.add_header('X-Wallaby-Token', server.token)
            request.add_header('Origin', 'https://evil.example')
            with self.assertRaises(HTTPError) as failure:
                urlopen(request)
            self.assertEqual(failure.exception.code, 403)
            failure.exception.close()
            request.remove_header('Origin')
            result = json.loads(urlopen(request).read().decode('utf-8'))
            self.finish(result['id'])
            status = json.loads(urlopen(server.url + 'api/jobs/' + result['id']).read().decode('utf-8'))
            self.assertEqual(status['state'], 'ready')
            result = urlopen(server.url + 'download/' + result['id'])
            self.assertEqual(result.headers['Content-Type'], 'application/pdf')
            self.assertIn('attachment;', result.headers['Content-Disposition'])
            self.assertEqual(result.read(), pdf_bytes())
            with self.assertRaises(HTTPError) as failure:
                urlopen(Request(server.url + 'api/status', headers={'Host': 'evil.example'}))
            self.assertEqual(failure.exception.code, 403)
            failure.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
