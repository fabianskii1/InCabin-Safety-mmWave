"""gtrack_clearance_demo → digital_twin HTTP 브리지 (서버가 꺼져 있으면 조용히 무시)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

DEFAULT_URL = 'http://127.0.0.1:8766/event'


class ClearancePublisher:
    def __init__(self, url=DEFAULT_URL, timeout=0.05):
        self.url = url
        self.timeout = timeout
        self._warned = False
        self._next_try = 0.0

    def publish(self, results, fold_permitted: bool) -> None:
        now = time.monotonic()
        if self._warned and now < self._next_try:
            return

        # 뒷좌석 존(Rear-L/C/R)의 점유만 L,C,R 로 추출. (부분문자열 매칭 금지 —
        # 'Passenger' 에 'R'이 들어가 오매핑되던 버그 방지). 조수석은 폴딩 대상 아님.
        seats_occ = {'L': False, 'C': False, 'R': False}
        ZONE_TO_SEAT = {'Rear-L': 'L', 'Rear-C': 'C', 'Rear-R': 'R'}
        passenger_occ = False
        for r in results:
            zone = r.get('zone', '')
            occ = (r.get('state', '').upper() == 'OCCUPIED')
            seat = ZONE_TO_SEAT.get(zone)
            if seat is not None:
                seats_occ[seat] = occ
            elif zone == 'Passenger':
                passenger_occ = occ   # 앞좌석 점유(폴딩 무관, 표시용)

        # 뒷좌석 폴딩 허용은 뒷좌석(L/C/R) 점유로만 판단 — 조수석(앞좌석)은 폴딩 무관.
        # (monitor.fold_permitted 는 전 존 EMPTY 기준이라 조수석까지 막으므로 여기선 뒷좌만)
        rear_fold_ok = not (seats_occ['L'] or seats_occ['C'] or seats_occ['R'])

        payload = {
            'source': 'gtrack_clearance',
            'clearance': {
                'L': seats_occ['L'],
                'C': seats_occ['C'],
                'R': seats_occ['R'],
                'passenger': passenger_occ,
                'fold_permit': rear_fold_ok,
            }
        }

        body = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            self.url, data=body, method='POST',
            headers={'Content-Type': 'application/json'}
        )

        try:
            urllib.request.urlopen(req, timeout=self.timeout)
            self._warned = False
        except (urllib.error.URLError, TimeoutError, OSError):
            if not self._warned:
                print('[gtrack-publisher] Digital twin server not running (python computer_GUI/digital_twin/server.py)')
                self._warned = True
            self._next_try = now + 2.0