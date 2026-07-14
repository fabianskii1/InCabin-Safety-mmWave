# 시트 폴딩 안전(끼임 방지)을 위한 존별 "빈 좌석 확인(clearance)" 판정 모듈.
#
# 설계 원칙: 오류 비용이 비대칭이므로 (없는데 있다고 판정 = 불편, 있는데 없다고 판정 = 사고)
#   - OCCUPIED 는 즉시 래치 (한 프레임의 근거로도 차단)
#   - EMPTY 는 "두 채널 모두 조용한 상태"가 연속 EMPTY_CONFIRM_SEC 유지될 때만 확정
#   - 판단 근거가 부족하면 UNKNOWN = 폴딩 차단 (fail-safe)
#
# 채널 A (대동작): 펌웨어 CPD 점유 판정 + 프레임 간 위상 급변 감지 -> 즉시 반응
# 채널 B (미세움직임): unwrapped 위상의 호흡 대역(0.08~0.8Hz) 에너지 집중도
#   -> 자는 아동처럼 호흡 외 움직임이 없는 경우를 담당 (HR/RR 수치 추정은 사용하지 않음)

import numpy as np
from collections import deque

STATE_UNKNOWN = 'UNKNOWN'    # 판단 근거 부족 -> 폴딩 차단
STATE_OCCUPIED = 'OCCUPIED'  # 사람 있음 -> 폴딩 차단
STATE_EMPTY = 'EMPTY'        # 빈 좌석 확증 -> 폴딩 허용

# ---- 튜닝 파라미터 (실차에서 빈 좌석/아동 재실 데이터로 캘리브레이션 권장) ----
MICRO_WINDOW_SEC = 8.0       # 채널 B 분석 윈도우 (아동 호흡 0.3~0.7Hz 를 충분히 담는 길이)
EMPTY_CONFIRM_SEC = 20.0     # 이 시간 동안 연속으로 조용해야 EMPTY 확정
BREATH_BAND_HZ = (0.08, 0.8) # 호흡 미세움직임 대역 (성인 휴식 ~ 아동 빠른 호흡)
BAND_RATIO_THRESH = 0.6      # 대역 에너지 집중도 임계값 (백색잡음 ~0.36, 호흡 >0.8)
MIN_BAND_RMS = 0.01          # 대역 RMS 절대 하한 (수치적으로 작은 잡음의 우연한 집중 배제)
PHASE_JUMP_MAD_SCALE = 8.0   # 위상 급변 판정: 최근 |d(phase)| 의 median 대비 배수
PHASE_JUMP_ABS_MIN = 0.5     # 위상 급변 절대 하한 (정지 상태의 MAD 가 0 에 가까울 때 오발 방지)


class ZoneClearance:
    """존 1개의 2채널 감지 + 비대칭 히스테리시스 상태머신."""

    def __init__(self, fps):
        self.fps = float(fps)
        self.dt = 1.0 / self.fps

        windowLen = max(8, int(round(MICRO_WINDOW_SEC * self.fps)))
        self.phaseBuf = deque(maxlen=windowLen)
        # 위상 급변 판정용 최근 |d(phase)| 이력 (약 30초)
        self.dphaseBuf = deque(maxlen=max(8, int(round(30.0 * self.fps))))
        self.prevPhase = None

        self.state = STATE_UNKNOWN
        self.quietSec = 0.0
        self.lastTrigger = 'startup'
        # 진단용 최근 메트릭
        self.bandRatio = 0.0
        self.bandRms = 0.0

    # ---- 채널 B: 호흡 대역 에너지 집중도 ----
    def _micro_motion_metric(self):
        n = len(self.phaseBuf)
        if n < self.phaseBuf.maxlen:
            return None  # 윈도우가 차기 전에는 판단 유보 (fail-safe)

        x = np.asarray(self.phaseBuf, dtype=float)
        # 선형 추세(온도 드리프트 등) 제거
        t = np.arange(n)
        x = x - np.polyval(np.polyfit(t, x, 1), t)

        mag2 = np.abs(np.fft.rfft(x * np.hanning(n))) ** 2
        freqs = np.fft.rfftfreq(n, self.dt)
        mag2[0] = 0.0  # DC 제거

        total = mag2.sum()
        if total <= 0:
            return 0.0, 0.0
        band = (freqs >= BREATH_BAND_HZ[0]) & (freqs <= BREATH_BAND_HZ[1])
        bandEnergy = mag2[band].sum()
        ratio = bandEnergy / total
        rms = float(np.sqrt(bandEnergy / n))
        return float(ratio), rms

    # ---- 채널 A 보조: 프레임 간 위상 급변 (탑승/이동 같은 대동작) ----
    def _phase_jump(self, phase):
        if self.prevPhase is None:
            self.prevPhase = phase
            return False
        d = abs(phase - self.prevPhase)
        self.prevPhase = phase

        if len(self.dphaseBuf) >= 8:
            med = float(np.median(self.dphaseBuf))
            thresh = max(PHASE_JUMP_ABS_MIN, PHASE_JUMP_MAD_SCALE * (med + 1e-6))
            jump = d > thresh
        else:
            jump = False
        self.dphaseBuf.append(d)
        return jump

    def update(self, cpdOccupied, phase):
        """프레임마다 호출. cpdOccupied: 펌웨어 CPD decision(0/1), phase: unwrapped 위상.

        반환: dict(state, foldPermitted, quietSec, trigger, bandRatio, bandRms)
        """
        valid = np.isfinite(phase)

        if not valid:
            # 데이터 이상 -> 판단 불가, 지금까지의 확신도 무효화
            self.state = STATE_UNKNOWN
            self.quietSec = 0.0
            self.lastTrigger = 'invalid data'
            self.prevPhase = None
            self.phaseBuf.clear()
            return self._result()

        jump = self._phase_jump(phase)
        self.phaseBuf.append(phase)

        metric = self._micro_motion_metric()
        metricReady = metric is not None
        if metricReady:
            self.bandRatio, self.bandRms = metric
            microTrig = (self.bandRatio > BAND_RATIO_THRESH
                         and self.bandRms > MIN_BAND_RMS)
        else:
            microTrig = False

        trigA = bool(cpdOccupied) or jump
        if trigA or microTrig:
            # OCCUPIED 즉시 래치, EMPTY 진행 상황 리셋
            self.state = STATE_OCCUPIED
            self.quietSec = 0.0
            if bool(cpdOccupied):
                self.lastTrigger = 'CPD decision'
            elif jump:
                self.lastTrigger = 'phase jump'
            else:
                self.lastTrigger = 'micro-motion (breathing band)'
        else:
            # 조용함 -> 채널 B 가 유효할 때만 EMPTY 확신도 누적 (윈도우 미충족 시 유보)
            if metricReady:
                self.quietSec += self.dt
                if self.quietSec >= EMPTY_CONFIRM_SEC:
                    self.state = STATE_EMPTY
                    self.lastTrigger = 'quiet confirmed'
            # metric 이 준비 안 됐으면 이전 상태 유지 (startup 은 UNKNOWN)

        return self._result()

    def _result(self):
        return {
            'state': self.state,
            'foldPermitted': self.state == STATE_EMPTY,
            'quietSec': self.quietSec,
            'trigger': self.lastTrigger,
            'bandRatio': self.bandRatio,
            'bandRms': self.bandRms,
        }


class ClearanceMonitor:
    """전 존 관리 + 상태 전이 로그."""

    def __init__(self, numZones, fps):
        self.numZones = int(numZones)
        self.zones = [ZoneClearance(fps) for _ in range(self.numZones)]
        self._prevStates = [STATE_UNKNOWN] * self.numZones
        self.transitions = []  # (time, zoneIdx, from, to, trigger)

    def update(self, decisions, vitalSigns, now=0.0):
        """decisions: 존별 CPD 점유(iterable), vitalSigns: 20칸 배열(존별 5칸, +0 이 위상)."""
        results = []
        for i, z in enumerate(self.zones):
            occ = decisions[i] if i < len(decisions) else 0
            phase = vitalSigns[i * 5 + 0] if vitalSigns is not None else float('nan')
            r = z.update(occ, phase)
            results.append(r)
            if r['state'] != self._prevStates[i]:
                self.transitions.append((now, i, self._prevStates[i], r['state'], r['trigger']))
                print("[clearance] t={:.1f}s zone{} {} -> {} ({})".format(
                    now, i, self._prevStates[i], r['state'], r['trigger']))
                self._prevStates[i] = r['state']
        return results

    def all_fold_permitted(self):
        return all(z.state == STATE_EMPTY for z in self.zones)


# ---- 하드웨어 없이 동작을 검증하는 합성 신호 셀프테스트 ----
def _selftest():
    fps = 4.0
    rng = np.random.default_rng(0)

    def run(mon, seconds, phaseFn, occ=0, t0=0.0):
        last = None
        for k in range(int(seconds * fps)):
            t = t0 + k / fps
            vs = np.zeros(20)
            vs[0] = phaseFn(t)
            last = mon.update([occ, 0, 0, 0], vs, now=t)
        return last, t0 + seconds

    print('--- selftest: empty cabin noise ---')
    mon = ClearanceMonitor(1, fps)
    noise = lambda t: 0.002 * rng.standard_normal()
    res, t = run(mon, 40, noise)
    assert res[0]['state'] == STATE_EMPTY, res[0]
    assert res[0]['foldPermitted']
    print('OK: empty confirmed after quiet period, fold permitted\n')

    print('--- selftest: sleeping child (breathing only, no CPD) ---')
    breath = lambda t: 0.15 * np.sin(2 * np.pi * 0.45 * t) + 0.002 * rng.standard_normal()
    res, t = run(mon, 15, breath, t0=t)
    assert res[0]['state'] == STATE_OCCUPIED, res[0]
    assert not res[0]['foldPermitted']
    print('OK: micro-motion latched OCCUPIED, fold blocked\n')

    print('--- selftest: person leaves -> EMPTY only after confirm window ---')
    res, t = run(mon, EMPTY_CONFIRM_SEC * 0.5, noise, t0=t)
    assert res[0]['state'] == STATE_OCCUPIED, res[0]  # 아직 확정 전 -> 차단 유지
    res, t = run(mon, MICRO_WINDOW_SEC + EMPTY_CONFIRM_SEC, noise, t0=t)
    assert res[0]['state'] == STATE_EMPTY, res[0]
    print('OK: asymmetric hysteresis (instant latch / slow release)\n')

    print('--- selftest: invalid data -> UNKNOWN, fold blocked ---')
    res, t = run(mon, 2, lambda t: float('nan'), t0=t)
    assert res[0]['state'] == STATE_UNKNOWN, res[0]
    assert not res[0]['foldPermitted']
    print('OK: fail-safe on invalid data\n')

    print('all selftests passed')


if __name__ == '__main__':
    _selftest()
