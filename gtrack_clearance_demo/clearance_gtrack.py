# gtrack_clearance_demo / clearance_gtrack.py
# ---------------------------------------------------------------------------
# GTRACK 트랙 좌표 위에 얹는 좌석 clearance 판정.
#   - 좌석 존 = 센서 좌표계(X 측방, Y 전방/거리, m)의 사각 영역.
#   - 판정: 존 안에 confirmed 트랙이 있으면 OCCUPIED. 비대칭 히스테리시스로
#           OCCUPIED 는 빨리 래치, EMPTY 는 조용함이 확인돼야 확정, 초기/애매 = UNKNOWN(차단).
#   - fold 허용 = 전 존 EMPTY 확정.
#
# 왜 이 방식이 기존 clearance보다 나은가: 멀티패스·정적클러터·약반사·identity 문제를
#   펌웨어 GTRACK 이 이미 처리한 '트랙'만 받으므로 호스트는 존 포함 여부만 보면 된다.
#
# [fail-safe 주의] cold-start 정지자: 시작부터 완전 정지한 사람은 트랙이 안 생길 수
#   있어 존이 '빈 좌석'으로 오판될 수 있다. 이를 위해:
#     (a) 시작 후 WARMUP_BLOCK_SEC 동안 무조건 차단,
#     (b) presence(포인트클라우드) 병용 훅을 남겨둠(main 에서 주입 가능),
#     (c) EMPTY 확정에 EMPTY_CONFIRM_SEC 의 조용함 요구.
#   실차 적용 시 presence 병용을 반드시 켤 것.
# ---------------------------------------------------------------------------

STATE_UNKNOWN = 'UNKNOWN'
STATE_OCCUPIED = 'OCCUPIED'
STATE_EMPTY = 'EMPTY'

# ---- 튜닝 ----
OCC_CONFIRM_SEC = 0.4      # 존 내 트랙이 이만큼 지속되면 OCCUPIED 래치(빠름)
OCC_HOLD_SEC = 1.5         # 트랙 사라져도 이 시간 점유 유지(끊김 완화)
EMPTY_CONFIRM_SEC = 4.0    # 존이 비어(트랙 없음) 이만큼 지속되면 EMPTY 확정
WARMUP_BLOCK_SEC = 5.0     # 시작 직후 무조건 차단(cold-start 안전)


class Zone:
    """좌석 존: 센서 좌표 사각영역 [xmin,xmax]×[ymin,ymax] (Z 무시)."""
    def __init__(self, name, xmin, xmax, ymin, ymax):
        self.name = name
        self.xmin, self.xmax = min(xmin, xmax), max(xmin, xmax)
        self.ymin, self.ymax = min(ymin, ymax), max(ymin, ymax)

    def contains(self, track):
        return (self.xmin <= track.x <= self.xmax and
                self.ymin <= track.y <= self.ymax)


# 좌석 존 세트 (월드 좌표, m). X+ = 조수석쪽 / X− = 운전석쪽, Y = 전방 거리.
# 두 버전을 모두 보존 — 실행 시 --zones 로 선택해 차량에서 비교 테스트 가능.

# (1) vehicle: computer_GUI/vod_vs_clearance.cfg 의 zoneDef(실차 튜닝된 OD-demo
#     range/angle bin)를 월드 좌표로 변환(bin→극좌표→직교→FLIP_X→틸트15°).
SEATS_VEHICLE = [
    Zone('Passenger', 0.15, 0.52, 0.41, 0.83),
    Zone('Rear-L',   -0.75, -0.20, 1.06, 1.70),
    Zone('Rear-C',   -0.19, 0.19, 1.16, 1.46),
    Zone('Rear-R',    0.20, 0.75, 1.06, 1.70),
]

# (2) placeholder: clearance cfg 적용 전, 처음 잡은 임의 존(비교용 원본 보존).
SEATS_PLACEHOLDER = [
    Zone('Passenger', 0.10, 0.70, 0.40, 1.00),
    Zone('Rear-L',   -0.85, -0.20, 1.10, 1.90),
    Zone('Rear-C',   -0.25, 0.25, 1.10, 1.90),
    Zone('Rear-R',    0.20, 0.85, 1.10, 1.90),
]

SEAT_SETS = {'vehicle': SEATS_VEHICLE, 'placeholder': SEATS_PLACEHOLDER}
DEFAULT_SEATS = SEATS_VEHICLE   # 기본 = 실차 튜닝


class ZoneClearance:
    """존 1개의 점유 상태머신 (비대칭 히스테리시스)."""
    def __init__(self, zone):
        self.zone = zone
        self.state = STATE_UNKNOWN
        self.occSec = 0.0        # 연속 점유 시간
        self.holdSec = 0.0       # 점유 홀드 잔여
        self.quietSec = 0.0      # 연속 비점유 시간
        self.lastTrackId = None
        self._t = None

    def update(self, occupied, trackId, now):
        dt = 0.0 if self._t is None else max(0.0, now - self._t)
        self._t = now

        if occupied:
            self.occSec += dt
            self.quietSec = 0.0
            self.holdSec = OCC_HOLD_SEC
            self.lastTrackId = trackId
        else:
            self.occSec = 0.0
            if self.holdSec > 0:
                self.holdSec = max(0.0, self.holdSec - dt)
            self.quietSec += dt

        occTrig = (self.occSec >= OCC_CONFIRM_SEC) or (self.holdSec > 0)
        if occTrig:
            self.state = STATE_OCCUPIED
        elif self.quietSec >= EMPTY_CONFIRM_SEC:
            self.state = STATE_EMPTY
        # 그 사이는 직전 상태 유지(EMPTY→비면 quiet 쌓이다 유지, 초기엔 UNKNOWN)
        return self.result()

    def result(self):
        return {'zone': self.zone.name, 'state': self.state,
                'foldPermitted': self.state == STATE_EMPTY,
                'trackId': self.lastTrackId if self.state == STATE_OCCUPIED else None,
                'quietSec': round(self.quietSec, 1)}


class ClearanceMonitor:
    """전 존 관리 + fold 허용 판정 + 전이 로그."""
    def __init__(self, seats=None):
        self.zones = [ZoneClearance(z) for z in (seats or DEFAULT_SEATS)]
        self._prev = {z.zone.name: STATE_UNKNOWN for z in self.zones}
        self._startT = None
        self.transitions = []

    def update(self, tracks, now):
        if self._startT is None:
            self._startT = now
        # 각 존에 대해: 안에 트랙이 있나
        results = []
        for zc in self.zones:
            inside = None
            for t in tracks:
                if zc.zone.contains(t):
                    inside = t.id
                    break
            r = zc.update(inside is not None, inside, now)
            results.append(r)
            if r['state'] != self._prev[r['zone']]:
                self.transitions.append((now, r['zone'], self._prev[r['zone']], r['state']))
                print('[clearance] t={:.1f}s {} {} -> {}{}'.format(
                    now, r['zone'], self._prev[r['zone']], r['state'],
                    ' (track#{})'.format(r['trackId']) if r['trackId'] else ''))
                self._prev[r['zone']] = r['state']
        return results

    def fold_permitted(self, now):
        """전 존 EMPTY 확정 + 워밍업 경과일 때만 허용(cold-start 안전)."""
        if self._startT is None or (now - self._startT) < WARMUP_BLOCK_SEC:
            return False
        return all(zc.state == STATE_EMPTY for zc in self.zones)


# ===========================================================================
def _selftest():
    from track_parser import Track
    mon = ClearanceMonitor()
    fps = 10.0

    def run(seconds, tracks, t0):
        t = t0
        for k in range(int(seconds * fps)):
            t = t0 + k / fps
            mon.update(tracks, t)
        return t

    # 빈 캐빈: 워밍업+EMPTY 확정 후 fold 허용
    t = run(WARMUP_BLOCK_SEC + EMPTY_CONFIRM_SEC + 1.0, [], 0.0)
    assert mon.fold_permitted(t), '빈 캐빈 -> 허용'
    assert all(zc.state == STATE_EMPTY for zc in mon.zones)
    print('OK: 빈 캐빈 전 존 EMPTY, fold 허용')

    # 뒷좌-중(Rear-C, x~0, y~1.3)에 사람 -> 그 존 OCCUPIED, fold 차단
    person = Track(5, 0.0, 1.3, 0.1)
    t = run(2.0, [person], t)
    rc = [zc for zc in mon.zones if zc.zone.name == 'Rear-C'][0]
    assert rc.state == STATE_OCCUPIED, rc.state
    assert not mon.fold_permitted(t), '점유 중 -> 차단'
    print('OK: Rear-C 착석 감지, fold 차단')

    # 하차 -> 조용함 확정 후 다시 EMPTY, 허용
    t = run(EMPTY_CONFIRM_SEC + 1.0, [], t)
    assert rc.state == STATE_EMPTY, rc.state
    assert mon.fold_permitted(t)
    print('OK: 하차 후 EMPTY 복귀, fold 허용')
    print('\nall selftests passed')


if __name__ == '__main__':
    _selftest()
