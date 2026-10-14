"""Restricted local transport between an internal Docker network and the host broker."""
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Relay(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        if self.path not in ('/v1/chat/completions', '/tool', '/status', '/candidate_check', '/stage_boundary'):
            self.send_error(404)
            return
        length = int(self.headers.get('Content-Length', 0))
        if length <= 0:
            self.send_error(413)
            return
        request = urllib.request.Request(os.environ['HOST_BROKER'] + self.path,
                  data=self.rfile.read(length), headers={'Content-Type': 'application/json',
                  'Authorization': self.headers.get('Authorization', ''),
                  'X-RSI-Agent':self.headers.get('X-RSI-Agent','main'), 'X-RSI-Role':self.headers.get('X-RSI-Role','orchestrator')})
        try:
            configured_timeout = os.environ['RSI_RELAY_TIMEOUT']
            response = opener.open(request, timeout=None if configured_timeout == 'none' else float(configured_timeout))
        except urllib.error.HTTPError as e:
            response = e
        except Exception:
            self.send_error(502, 'Host broker transport unavailable')
            return
        with response:
            data = response.read()
            self.send_response(response.status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)


ThreadingHTTPServer(('0.0.0.0', 8000), Relay).serve_forever()
