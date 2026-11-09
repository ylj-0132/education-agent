"""Loopback HTTP transport for the workbench."""
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .service import WorkbenchService


def make_server(root, port=8765, service=None):
    service = service or WorkbenchService(root)
    static = Path(root) / 'webapp/static'

    class Handler(BaseHTTPRequestHandler):

        def log_message(self, *args):
            pass

        def send(self, status, data, content_type='application/json; charset=utf-8'):
            body = (
                json.dumps(data, ensure_ascii=False).encode('utf-8')
                if content_type.startswith('application/json') else data
            )
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header(
                'Content-Security-Policy',
                "default-src 'self'; style-src 'self'; script-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def trusted(self):
            expected = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != expected:
                self.send(403, {'error': 'Untrusted Host'})
                return False
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + expected:
                self.send(403, {'error': 'Untrusted Origin'})
                return False
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                self.send(403, {'error': 'Cross-site request forbidden'})
                return False
            return True

        def do_GET(self):
            if not self.trusted():
                return
            url = urlsplit(self.path)
            try:
                if url.path == '/api/bootstrap':
                    return self.send(200, service.bootstrap())
                if url.path == '/api/artifact':
                    return self.send(200, {
                        'artifact': service.artifact(parse_qs(url.query).get('path', [''])[0]),
                    })
                if url.path == '/api/jobs':
                    return self.send(200, {'jobs': service.jobs()})
                if url.path.startswith('/api/jobs/'):
                    return self.send(200, {'job': service.get_job(url.path.rsplit('/', 1)[-1])})
                names = {
                    '/': 'index.html', '/index.html': 'index.html',
                    '/app.js': 'app.js', '/demo-route.js': 'demo-route.js',
                    '/job-poller.js': 'job-poller.js', '/style.css': 'style.css',
                }
                if url.path in names:
                    path = static / names[url.path]
                    mime = {
                        '.html': 'text/html; charset=utf-8',
                        '.css': 'text/css; charset=utf-8',
                        '.js': 'text/javascript; charset=utf-8',
                    }[path.suffix]
                    return self.send(200, path.read_bytes(), mime)
                self.send(404, {'error': 'Not found'})
            except ValueError as exc:
                self.send(400, {'error': str(exc)})
            except (KeyError, FileNotFoundError):
                self.send(404, {'error': 'Not found'})
            except Exception:
                self.send(500, {'error': 'Local read failed'})

        def do_POST(self):
            if not self.trusted():
                return
            if self.path not in ('/api/jobs', '/api/submission-draft'):
                return self.send(404, {'error': 'Not found'})
            if not hmac.compare_digest(self.headers.get('X-Workbench-Token', ''), service.csrf_token):
                return self.send(403, {'error': 'Missing or invalid workbench token'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 65536:
                    raise ValueError('Request body must be 1–65536 bytes')
                if self.headers.get_content_type() != 'application/json':
                    raise ValueError('Content-Type application/json required')
                payload = json.loads(self.rfile.read(size).decode('utf-8'))
                if self.path == '/api/submission-draft':
                    if not isinstance(payload, dict):
                        raise ValueError('JSON object required')
                    return self.send(201, {'path': service.save_submission_draft(payload.get('answer'))})
                self.send(202, {'job': service.launch(payload)})
            except (ValueError, UnicodeError) as exc:
                self.send(400, {'error': str(exc)})
            except RuntimeError as exc:
                self.send(409, {'error': str(exc)})
            except Exception:
                self.send(500, {'error': 'Local task launch failed'})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server
