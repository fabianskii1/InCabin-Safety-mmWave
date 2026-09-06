"""디지털 트윈 정적 파일 + /state /event (표준 라이브러리만 사용).

실행 (computer_GUI 기준):
  python digital_twin/server.py
브라우저: http://127.0.0.1:8766
"""

from __future__ import annotations

import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = '127.0.0.1'
PORT = 8766
ROOT = Path(__file__).resolve().parent

_lock = threading.Lock()
_state = {
    'source': 'idle',
    'status': 'NORMAL',
    'present': True,
    'rr': 14,
    'hr': 72,
    'apnea_sec': 0.0,
    'detail': '데모 대기',
    'cmd': None,
    'clearance': {                     # <--- 추가
        'L': False,
        'C': False,
        'R': False,
        'fold_permit': True,
    }
}


class TwinHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        if self.path.startswith('/state'):
            return
        super().log_message(fmt, *args)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cache-Control', 'no-store')

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def send_head(self):
        if 'If-Modified-Since' in self.headers:
            del self.headers['If-Modified-Since']
        if 'If-None-Match' in self.headers:
            del self.headers['If-None-Match']
        return super().send_head()

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path.split('?', 1)[0] == '/state':
            with _lock:
                body = json.dumps(_state).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self._cors()
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):
        if self.path.split('?', 1)[0] != '/event':
            self.send_error(404)
            return
        length = int(self.headers.get('Content-Length', '0') or 0)
        raw = self.rfile.read(length) if length else b'{}'
        try:
            incoming = json.loads(raw.decode('utf-8') or '{}')
        except json.JSONDecodeError:
            incoming = {}
        if not isinstance(incoming, dict):
            incoming = {}
        with _lock:
            if 'clearance' in incoming:  # <--- clearance 데이터 처리 추가(아래 3줄)
                _state['clearance'] = incoming['clearance']
                _state['source'] = incoming.get('source', 'gtrack_clearance')
            _state.update({k: incoming[k] for k in incoming if k in _state or k == 'cmd'})
            if 'cmd' not in incoming:
                status = str(_state.get('status', 'NORMAL')).upper()
                if status == 'DANGER':
                    _state['cmd'] = 'EMERGENCY_START'
                elif status in ('NORMAL', 'SEARCHING'):
                    _state['cmd'] = None
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self._cors()
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    server = ThreadingHTTPServer((HOST, PORT), TwinHandler)
    print('Digital twin: http://{}:{}'.format(HOST, PORT))
    print('Mock demo in the browser. Live sensor: run main_driver.py at the same time.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nstopped')


if __name__ == '__main__':
    main()
