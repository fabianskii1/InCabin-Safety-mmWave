"""driver_vitals → digital_twin HTTP 브리지 (서버가 꺼져 있으면 조용히 무시)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

DEFAULT_URL = 'http://127.0.0.1:8766/event'


class TwinPublisher:
    def __init__(self, url=DEFAULT_URL, timeout=0.05):
        self.url = url
        self.timeout = timeout
        self._warned = False
        self._next_try = 0.0

    def publish(self, cabin_state) -> None:
        now = time.monotonic()
        if self._warned and now < self._next_try:
            return
        payload = {
            'source': 'driver_vitals',
            'status': getattr(cabin_state.status, 'value', str(cabin_state.status)),
            'present': getattr(cabin_state.status, 'value', '') != 'SEARCHING',
            'rr': int(getattr(cabin_state, 'rr', 0) or 0),
            'hr': int(getattr(cabin_state, 'hr', 0) or 0),
            'apnea_sec': float(getattr(cabin_state, 'apnea_sec', 0.0) or 0.0),
            'detail': getattr(cabin_state, 'detail', '') or '',
        }
        body = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            self.url, data=body, method='POST',
            headers={'Content-Type': 'application/json'})
        try:
            urllib.request.urlopen(req, timeout=self.timeout)
            self._warned = False
        except (urllib.error.URLError, TimeoutError, OSError):
            if not self._warned:
                print('Digital twin: server not running (python digital_twin/server.py)')
                self._warned = True
            self._next_try = now + 2.0
