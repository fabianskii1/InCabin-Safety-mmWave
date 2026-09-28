# gtrack_clearance_demo / adaptive_clearance.py
# ---------------------------------------------------------------------------
# 근거 강도에 따라 '해제 시간'을 달리하는 존 점유 상태머신.
#
# [문제] clearance_gtrack.ZoneClearance 는 모든 점유에 같은 EMPTY_CONFIRM_SEC(4.0s)
#   를 적용한다. 정지 승객이 트랙을 잃어도 버티게 하려면 이 값을 줄일 수 없는데,
#   그 대가로 클러터 몇 프레임(0.16s)도 똑같이 4초 차단이 된다.
#   실측(2026-09-16, 빈 캐빈): Rear-C 가 presence n=3 (SNR med9) 한 번에 4.2초 차단.
#   Rear-C 점유 182프레임 중 트랙 근거는 10프레임(5.5%)뿐이고 나머지는 전부 꼬리였다.
#
# [해법] 유지 시간을 줄이지 않는다. 대신 점유를 '무엇이 세웠는가'로 나눈다.
#   - STRONG: 트랙이 STRONG_TRACK_SEC 이상 뒷받침했거나, 점유가 STRONG_SUSTAIN_SEC
#             이상 연속된 에피소드 -> 정지 승객일 수 있으므로 기존대로 길게 해제.
#   - WEAK  : 그 외(포인트만 스친 단발) -> 짧게 해제.
#   에피소드가 EMPTY 로 끝날 때만 등급이 초기화되므로, 트랙으로 STRONG 이 된 뒤
#   트랙이 죽고 presence 만 남아도 등급은 유지된다 = static death 보호는 그대로.
#
# 기존 파일 무수정. clearance_gtrack 의 상수/상태 이름을 그대로 따른다.
# 하드웨어 없이 검증:  py adaptive_clearance.py
# ---------------------------------------------------------------------------
import clearance_gtrack as cg

# ---- 튜닝 (실측 근거는 상단 주석) ----
EMPTY_CONFIRM_STRONG = cg.EMPTY_CONFIRM_SEC   # 4.0s. 정지 승객 보호용, 건드리지 않음
EMPTY_CONFIRM_WEAK = 1.0                      # 단발 근거는 빨리 놓아준다
OCC_HOLD_STRONG = cg.OCC_HOLD_SEC             # 1.5s
OCC_HOLD_WEAK = 0.3                           # 단발은 짧게만 붙든다
STRONG_TRACK_SEC = 0.5                        # 트랙이 이만큼 지지하면 STRONG 승격
STRONG_SUSTAIN_SEC = 3.0                      # 근거 무관, 에피소드 내 누적 점유가 이만큼이면 STRONG
# 주의: '연속'이 아니라 '누적'이어야 한다. 정지 승객의 presence 는 간헐적(실측 검출률
# 약 59%)이라 연속 조건으로는 영영 승격되지 않고 WEAK(1.0s 해제)로 남아 위험하다.


class AdaptiveZoneClearance:
    """존 1개의 점유 상태머신 (비대칭 히스테리시스 + 근거 강도별 해제).

    ZoneClearance 와 인터페이스 호환: update(occupied, trackId, now) -> result dict.
    다만 track 근거 여부를 알아야 하므로 track_in 을 별도 인자로 받는다.
    """

    def __init__(self, zone, weak_release=None, weak_hold=None,
                 strong_track_sec=None, strong_sustain_sec=None):
        self.zone = zone
        # 기본값은 모듈 상수. 오프라인 캘리브레이션(analyze_frames.py)에서 인스턴스별로 바꾼다.
        self.weak_release = EMPTY_CONFIRM_WEAK if weak_release is None else weak_release
        self.weak_hold = OCC_HOLD_WEAK if weak_hold is None else weak_hold
        self.strong_track_sec = STRONG_TRACK_SEC if strong_track_sec is None else strong_track_sec
        self.strong_sustain_sec = STRONG_SUSTAIN_SEC if strong_sustain_sec is None else strong_sustain_sec
        self.state = cg.STATE_UNKNOWN
        self.occSec = 0.0        # 현재 연속 점유 시간(비점유 프레임에 리셋)
        self.evidSec = 0.0       # 에피소드 내 누적 점유 시간(간헐적이어도 쌓임)
        self.trackSec = 0.0      # 그중 트랙이 뒷받침한 누적 시간
        self.holdSec = 0.0
        self.quietSec = 0.0
        self.strong = False      # 이 에피소드가 STRONG 으로 승격됐나
        self.lastTrackId = None
        self._t = None

    # ---- 현재 에피소드 등급에 따른 시간 상수 ----
    def _hold_sec(self):
        return OCC_HOLD_STRONG if self.strong else self.weak_hold

    def _release_sec(self):
        return EMPTY_CONFIRM_STRONG if self.strong else self.weak_release

    def update(self, occupied, trackId, now, track_in=None):
        if track_in is None:
            track_in = trackId is not None
        dt = 0.0 if self._t is None else max(0.0, now - self._t)
        self._t = now

        if occupied:
            self.occSec += dt
            self.evidSec += dt
            self.quietSec = 0.0
            if track_in:
                self.trackSec += dt
                self.lastTrackId = trackId
            # 승격 판정: 트랙이 충분히 지지했거나, 점유 근거가 충분히 쌓였거나
            if (self.trackSec >= self.strong_track_sec or
                    self.evidSec >= self.strong_sustain_sec):
                self.strong = True
            self.holdSec = self._hold_sec()
        else:
            self.occSec = 0.0
            if self.holdSec > 0:
                self.holdSec = max(0.0, self.holdSec - dt)
            self.quietSec += dt

        if self.holdSec > 0:
            self.state = cg.STATE_OCCUPIED
        elif self.quietSec >= self._release_sec():
            if self.state != cg.STATE_EMPTY:
                # 에피소드 종료 -> 등급 초기화
                self.strong = False
                self.trackSec = 0.0
                self.evidSec = 0.0
            self.state = cg.STATE_EMPTY
        # 그 사이는 직전 상태 유지
        return self.result()

    def result(self):
        return {'zone': self.zone.name, 'state': self.state,
                'foldPermitted': self.state == cg.STATE_EMPTY,
                'trackId': self.lastTrackId if self.state == cg.STATE_OCCUPIED else None,
                'quietSec': round(self.quietSec, 1),
                'strong': self.strong,
                'releaseSec': self._release_sec()}


# ===========================================================================
def _selftest():
    z = cg.Zone('T', -1, 1, -1, 1)
    FPS = 1 / 0.055

    def run(zc, sec, occupied, track_in, t0):
        t = t0
        n = int(sec * FPS)
        for _ in range(n):
            zc.update(occupied, 1 if track_in else None, t, track_in)
            t += 0.055
        return t

    # ---- A. 단발 클러터(포인트만 1프레임): WEAK -> 빨리 풀린다 ----
    zc = AdaptiveZoneClearance(z)
    t = run(zc, 6.0, False, False, 0.0)          # EMPTY 확정
    assert zc.state == cg.STATE_EMPTY
    zc.update(True, None, t, False)              # 딱 한 프레임, presence 만
    assert zc.state == cg.STATE_OCCUPIED and not zc.strong
    t += 0.055
    t0 = t
    while zc.state != cg.STATE_EMPTY and t - t0 < 10.0:
        zc.update(False, None, t, False)
        t += 0.055
    weak_dur = t - t0
    print('OK: 단발 presence 점유 지속 {:.2f}초 (기존 4.0초)'.format(weak_dur))
    assert 1.0 <= weak_dur <= 1.6, weak_dur

    # ---- B. 트랙이 지지한 점유: STRONG -> 기존과 동일하게 길게 ----
    zc = AdaptiveZoneClearance(z)
    t = run(zc, 6.0, False, False, 0.0)
    t = run(zc, 1.0, True, True, t)              # 트랙 1초 지지
    assert zc.strong, '트랙 1초면 STRONG 이어야 함'
    t0 = t
    while zc.state != cg.STATE_EMPTY and t - t0 < 10.0:
        zc.update(False, None, t, False)
        t += 0.055
    strong_dur = t - t0
    print('OK: 트랙 지지 점유 지속 {:.2f}초 (기존 4.0초 유지)'.format(strong_dur))
    assert 4.0 <= strong_dur <= 5.6, strong_dur

    # ---- C. static death 보호: STRONG 승격 후 트랙이 죽고 presence 만 남아도 유지 ----
    zc = AdaptiveZoneClearance(z)
    t = run(zc, 6.0, False, False, 0.0)
    t = run(zc, 1.0, True, True, t)              # 트랙으로 STRONG
    t = run(zc, 8.0, True, False, t)             # 트랙 없이 presence 만 8초
    assert zc.state == cg.STATE_OCCUPIED, 'presence 로 유지돼야 함'
    assert zc.strong, '등급이 유지돼야 함'
    assert zc._release_sec() == EMPTY_CONFIRM_STRONG
    print('OK: 트랙 소실 후 presence 만 남아도 STRONG 유지 (static death 보호)')

    # ---- D. 콜드스타트 정지 승객: 트랙 없이 presence 만이어도 오래가면 STRONG ----
    zc = AdaptiveZoneClearance(z)
    t = run(zc, 6.0, False, False, 0.0)
    t = run(zc, STRONG_SUSTAIN_SEC + 0.5, True, False, t)   # presence 만 3.5초
    assert zc.strong, '지속 점유는 근거 무관 STRONG 승격'
    print('OK: presence 만으로도 {:.0f}초 지속되면 STRONG 승격 (콜드스타트 보호)'.format(
        STRONG_SUSTAIN_SEC))

    # ---- D2. 간헐 presence(50% 듀티)로도 누적되어 STRONG 승격 ----
    zc2 = AdaptiveZoneClearance(z)
    t2 = run(zc2, 6.0, False, False, 0.0)
    for k in range(int(8.0 * FPS)):
        zc2.update(k % 2 == 0, None, t2, False)
        t2 += 0.055
    assert zc2.strong, '간헐 presence 도 누적 승격돼야 함(정지 승객 보호)'
    print('OK: 간헐 presence(50% 듀티)도 누적 3초면 STRONG 승격')

    # ---- E. 에피소드 종료 후 등급 초기화 ----
    t0 = t
    while zc.state != cg.STATE_EMPTY and t - t0 < 15.0:
        zc.update(False, None, t, False)
        t += 0.055
    assert not zc.strong, 'EMPTY 되면 등급 초기화'
    print('OK: EMPTY 확정 시 등급 초기화')

    print('')
    print('all adaptive clearance selftests passed')


if __name__ == '__main__':
    _selftest()
