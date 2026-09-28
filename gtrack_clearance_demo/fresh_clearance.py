# gtrack_clearance_demo / fresh_clearance.py
# ---------------------------------------------------------------------------
# B안 좌석 상태머신: '조용한 시간'을 마지막 실제 갱신부터 센다.
#
# 현행 clearance_gtrack.ZoneClearance 는 occupied 가 참인 동안 조용한 시간을 0 으로 되돌린다.
# 얼어붙은 트랙(연관 점 없이 예측만으로 유지돼 좌표가 멈춘 트랙, gtrack_unit_update.c
# 253-283·441-445)을 F초까지 근거로 인정하면, 그 F초 동안 측정이 없었는데도 조용한 시간이
# F 시점부터 새로 시작돼 해제까지 F+E 가 걸린다(A안).
#
# 이 상태머신은 입력을 둘로 나눈다.
#   occupied : 점유 근거. 점 근거 or 존 안 트랙 (얼어붙은 트랙은 호출하는 쪽에서 F초까지만 포함)
#   fresh    : 실제 갱신. 점 근거 or 이번 프레임에 좌표가 바뀐 존 안 트랙
# 조용한 시간 = now - 마지막 fresh.
#   OCCUPIED : occupied 가 참이거나, 마지막 fresh 후 hold_sec 이 안 지남
#   EMPTY    : 위가 아니고 조용한 시간 >= empty_confirm
#   그 사이   : 직전 상태 유지 (시작 직후는 UNKNOWN = 차단)
# 따라서 점 없이 얼어붙은 트랙만 남으면 EMPTY 는 마지막 갱신 기준 max(F, E) 에 확정된다.
#
# clearance_gtrack.py 는 수정하지 않는다(상태 상수·기본값만 재사용).
# 하드웨어 없이 검증:  py fresh_clearance.py
# ---------------------------------------------------------------------------
import clearance_gtrack as cg


class FreshZoneClearance:
    """존 1개. update(occupied, fresh, now) 로 갱신."""

    def __init__(self, zone, empty_confirm=None, hold_sec=None):
        self.zone = zone
        self.empty_confirm = cg.EMPTY_CONFIRM_SEC if empty_confirm is None else float(empty_confirm)
        self.hold_sec = cg.OCC_HOLD_SEC if hold_sec is None else float(hold_sec)
        self.state = cg.STATE_UNKNOWN
        self.last_fresh = None
        self.quietSec = 0.0
        self.lastTrackId = None
        self._t0 = None

    def update(self, occupied, fresh, now, trackId=None):
        if self._t0 is None:
            self._t0 = now
        if fresh:
            occupied = True                 # 실제 갱신은 항상 점유 근거
            self.last_fresh = now
        if occupied and trackId is not None:
            self.lastTrackId = trackId
        ref = self.last_fresh if self.last_fresh is not None else self._t0
        self.quietSec = max(0.0, now - ref)
        held = self.last_fresh is not None and self.quietSec < self.hold_sec
        if occupied or held:
            self.state = cg.STATE_OCCUPIED
        elif self.quietSec >= self.empty_confirm:
            self.state = cg.STATE_EMPTY
        return self.result()

    def result(self):
        return {'zone': self.zone.name, 'state': self.state,
                'foldPermitted': self.state == cg.STATE_EMPTY,
                'trackId': self.lastTrackId if self.state == cg.STATE_OCCUPIED else None,
                'quietSec': round(self.quietSec, 1)}


# ===========================================================================
def _selftest():
    FR = 0.05
    zone = cg.Zone('Rear-L', 0, 0, 0, 0)

    def run(m, t0, t1, occ, fresh):
        t = t0
        while t < t1 - 1e-9:
            m.update(occ(t), fresh(t), t)
            t = round(t + FR, 6)
        return t

    def empty_at(m, t0, t1, occ, fresh):
        t = t0
        while t < t1 - 1e-9:
            m.update(occ(t), fresh(t), t)
            if m.state == cg.STATE_EMPTY:
                return t
            t = round(t + FR, 6)
        return None

    # A. 시작 직후엔 UNKNOWN, 근거 없이 E 가 지나면 EMPTY
    m = FreshZoneClearance(zone, empty_confirm=4.0)
    m.update(False, False, 0.0)
    assert m.state == cg.STATE_UNKNOWN
    te = empty_at(m, 0.05, 10.0, lambda t: False, lambda t: False)
    assert te is not None and abs(te - 4.0) < 0.06, te
    print('OK: 근거 없이 시작하면 UNKNOWN -> {:.2f}s 에 EMPTY (E=4.0)'.format(te))

    # B. 착석(갱신 계속) 뒤 트랙이 얼어붙음, 점 없음. 호출 쪽 F=1 -> EMPTY 는 마지막 갱신 + E
    for F, expect in ((1.0, 4.0), (6.0, 6.0)):
        m = FreshZoneClearance(zone, empty_confirm=4.0)
        run(m, 0.0, 10.0, lambda t: True, lambda t: True)          # 마지막 갱신 = 9.95
        last = 9.95
        te = empty_at(m, 10.0, 30.0, lambda t, F=F: t < last + F, lambda t: False)
        assert te is not None and abs((te - last) - expect) < 0.06, (F, te)
        print('OK: 얼어붙은 트랙 F={:g} -> 마지막 갱신 후 {:.2f}s 에 EMPTY (max(F,E)={:g})'.format(
            F, te - last, expect))

    # C. 점 근거가 E 보다 짧게 끊기면 계속 OCCUPIED
    m = FreshZoneClearance(zone, empty_confirm=4.0)
    run(m, 0.0, 5.0, lambda t: True, lambda t: True)
    run(m, 5.0, 8.9, lambda t: False, lambda t: False)              # 3.9s 공백
    assert m.state == cg.STATE_OCCUPIED, m.state
    run(m, 8.9, 10.0, lambda t: True, lambda t: True)
    assert m.state == cg.STATE_OCCUPIED
    print('OK: 3.9s 근거 공백(E=4.0)은 OCCUPIED 유지')

    # D. 얼어붙은 트랙이 근거인 동안에도 조용한 시간은 흐른다(A안과의 차이)
    m = FreshZoneClearance(zone, empty_confirm=4.0)
    run(m, 0.0, 2.0, lambda t: True, lambda t: True)                # 마지막 갱신 1.95
    run(m, 2.0, 7.0, lambda t: True, lambda t: False)               # 5s 동안 얼어붙은 트랙만
    assert m.state == cg.STATE_OCCUPIED and m.quietSec > 4.9
    m.update(False, False, 7.0)                                     # F 도달로 제외되는 순간
    assert m.state == cg.STATE_EMPTY, m.state
    print('OK: 얼어붙은 트랙이 빠지는 순간 이미 조용한 시간 {:.2f}s >= E -> 즉시 EMPTY'.format(m.quietSec))

    # E. hold: 마지막 갱신 후 hold_sec 안에서는 occupied 가 거짓이어도 OCCUPIED
    m = FreshZoneClearance(zone, empty_confirm=1.0, hold_sec=1.5)
    run(m, 0.0, 1.0, lambda t: True, lambda t: True)                # 마지막 갱신 0.95
    m.update(False, False, 2.2)
    assert m.state == cg.STATE_OCCUPIED
    m.update(False, False, 2.5)
    assert m.state == cg.STATE_EMPTY, m.state
    print('OK: E < hold 이면 hold(1.5s)까지 유지 후 EMPTY')

    print('')
    print('all fresh_clearance selftests passed')


if __name__ == '__main__':
    _selftest()
