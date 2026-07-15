# 시트 폴딩 안전(끼임 방지)을 위한 존별 "빈 좌석 확인(clearance)" 판정 모듈.
#
# 설계 원칙: 오류 비용이 비대칭이므로 (없는데 있다고 판정 = 불편, 있는데 없다고 판정 = 사고)
#   - OCCUPIED 는 짧은 디바운스 후 빠르게 래치 (노이즈 1프레임은 무시)
#   - EMPTY 는 조용함이 EMPTY_CONFIRM_SEC 유지될 때만 확정
#   - 판단 근거가 부족하면 UNKNOWN = 폴딩 차단 (fail-safe)
#
# 채널 A (대동작): 좌석 피크 점유 + 프레임 간 위상 급변
# 채널 B (미세움직임): unwrapped 위상의 호흡 대역(0.08~0.8Hz) 에너지 집중도

import numpy as np
from collections import deque

STATE_UNKNOWN = 'UNKNOWN'    # 판단 근거 부족 -> 폴딩 차단
STATE_OCCUPIED = 'OCCUPIED'  # 사람 있음 -> 폴딩 차단
STATE_EMPTY = 'EMPTY'        # 빈 좌석 확증 -> 폴딩 허용

# ---- 튜닝 파라미터 (실차 캘리브레이션 권장) ----
MICRO_WINDOW_SEC = 5.0       # 채널 B 윈도우 (짧을수록 EMPTY/점유 판정 빠름)
EMPTY_CONFIRM_SEC = 6.0      # 연속 조용 시간 -> EMPTY (기존 20s → 빠르게)
BREATH_BAND_HZ = (0.10, 0.70)  # 호흡 대역 (저주파 드리프트/고주파 잡음 완화)
BAND_RATIO_THRESH = 0.68     # 대역 집중도 (노이즈 오탐↓)
MIN_BAND_RMS = 0.035         # 대역 RMS 하한 (작은 잡음 배제)
PHASE_EMA_ALPHA = 0.40       # 위상 EMA (점프/FFT 전 노이즈 완화)
PHASE_SPIKE_MAD = 4.0        # |phase-median| > MAD*scale 이면 스파이크 클립
PHASE_JUMP_MAD_SCALE = 10.0  # 급변: 최근 |dφ| median 배수
PHASE_JUMP_ABS_MIN = 0.85    # 급변 절대 하한

# 디바운스: 단발 노이즈로 OCCUPIED 래치/ quiet 리셋 방지
PEAK_ON_FRAMES = 4           # 피크 점유 확정 (~0.8s @5fps)
PEAK_HOLD_SEC = 1.5          # 피크 소실 후에도 점유 유지 (끊김 완화)
MICRO_ON_FRAMES = 5          # 호흡대역 트리거 연속 프레임
JUMP_ON_FRAMES = 3           # 위상 급변 연속 프레임

# 피크 품질/안정성
PEAK_HEAT_ABS_MIN = 420.0
PEAK_HEAT_BG_RATIO = 2.5     # 신규 획득
PEAK_HEAT_TRACK_RATIO = 1.7  # 이미 추적 중이면 완화 (미인식 끊김↓)
PEAK_STABLE_FRAMES = 3       # 신규 확정
PEAK_RELOCK_FRAMES = 2       # 재획득 (잠깐 놓친 뒤)
PEAK_STABLE_DIST = 3.0       # 허용 위치 점프 (bin)
PEAK_MISS_HOLD_FRAMES = 8    # 피크 없어도 ~1.6s 점유/위상 유지

# 앞열 → 뒷열 크로스톡 가드 (조수석 방위와 겹칠 때)
FRONT_SEAT_IDX = 3
CROSSTALK_FRONT_RATIO = 0.80  # front_heat >= rear*ratio 이면 뒷좌석 피크 무시


class ZoneClearance:
    """존 1개의 2채널 감지 + 비대칭 히스테리시스 상태머신."""

    def __init__(self, fps):
        self.fps = float(fps)
        self.dt = 1.0 / self.fps

        windowLen = max(8, int(round(MICRO_WINDOW_SEC * self.fps)))
        self.phaseBuf = deque(maxlen=windowLen)
        self.rawPhaseBuf = deque(maxlen=max(8, int(round(2.0 * self.fps))))
        self.dphaseBuf = deque(maxlen=max(8, int(round(20.0 * self.fps))))
        self.prevPhase = None
        self.emaPhase = None

        self.state = STATE_UNKNOWN
        self.quietSec = 0.0
        self.lastTrigger = 'startup'
        self.bandRatio = 0.0
        self.bandRms = 0.0

        self.peakOnCount = 0
        self.peakHoldSec = 0.0
        self.microOnCount = 0
        self.jumpOnCount = 0

    def _clip_spike(self, phase):
        """최근 위상 median 대비 이상치는 median 쪽으로 클립."""
        self.rawPhaseBuf.append(phase)
        if len(self.rawPhaseBuf) < 5:
            return phase
        arr = np.asarray(self.rawPhaseBuf, dtype=float)
        med = float(np.median(arr))
        mad = float(np.median(np.abs(arr - med))) + 1e-6
        lim = PHASE_SPIKE_MAD * mad
        if abs(phase - med) > max(lim, 0.35):
            return med + np.sign(phase - med) * max(lim, 0.35)
        return phase

    def _smooth_phase(self, phase):
        phase = self._clip_spike(phase)
        if self.emaPhase is None:
            self.emaPhase = phase
        else:
            a = PHASE_EMA_ALPHA
            self.emaPhase = a * phase + (1.0 - a) * self.emaPhase
        return float(self.emaPhase)

    def _micro_motion_metric(self):
        n = len(self.phaseBuf)
        if n < self.phaseBuf.maxlen:
            return None

        x = np.asarray(self.phaseBuf, dtype=float)
        t = np.arange(n)
        x = x - np.polyval(np.polyfit(t, x, 1), t)

        mag2 = np.abs(np.fft.rfft(x * np.hanning(n))) ** 2
        freqs = np.fft.rfftfreq(n, self.dt)
        mag2[0] = 0.0

        total = mag2.sum()
        if total <= 0:
            return 0.0, 0.0
        band = (freqs >= BREATH_BAND_HZ[0]) & (freqs <= BREATH_BAND_HZ[1])
        bandEnergy = mag2[band].sum()
        ratio = bandEnergy / total
        rms = float(np.sqrt(bandEnergy / n))
        return float(ratio), rms

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
        """프레임마다 호출. cpdOccupied: 좌석 피크 점유(0/1), phase: unwrapped 위상."""
        valid = np.isfinite(phase)

        if not valid:
            self.state = STATE_UNKNOWN
            self.quietSec = 0.0
            self.lastTrigger = 'invalid data'
            self.prevPhase = None
            self.emaPhase = None
            self.phaseBuf.clear()
            self.rawPhaseBuf.clear()
            self.peakOnCount = 0
            self.peakHoldSec = 0.0
            self.microOnCount = 0
            self.jumpOnCount = 0
            return self._result()

        phase = self._smooth_phase(float(phase))
        jumpRaw = self._phase_jump(phase)
        self.phaseBuf.append(phase)

        # ---- 피크 점유 디바운스 + 홀드 ----
        if bool(cpdOccupied):
            self.peakOnCount += 1
            if self.peakOnCount >= PEAK_ON_FRAMES:
                self.peakHoldSec = PEAK_HOLD_SEC  # 확정 시마다 홀드 갱신
        else:
            self.peakOnCount = 0
            if self.peakHoldSec > 0:
                self.peakHoldSec = max(0.0, self.peakHoldSec - self.dt)
        peakTrig = (self.peakOnCount >= PEAK_ON_FRAMES) or (self.peakHoldSec > 0)

        # ---- 미세움직임 / 급변 디바운스 ----
        metric = self._micro_motion_metric()
        metricReady = metric is not None
        if metricReady:
            self.bandRatio, self.bandRms = metric
            microRaw = (self.bandRatio > BAND_RATIO_THRESH
                        and self.bandRms > MIN_BAND_RMS)
        else:
            microRaw = False

        if microRaw:
            self.microOnCount += 1
        else:
            self.microOnCount = 0
        microTrig = self.microOnCount >= MICRO_ON_FRAMES

        if jumpRaw:
            self.jumpOnCount += 1
        else:
            self.jumpOnCount = 0
        jumpTrig = self.jumpOnCount >= JUMP_ON_FRAMES

        if peakTrig or jumpTrig or microTrig:
            self.state = STATE_OCCUPIED
            self.quietSec = 0.0
            if peakTrig:
                self.lastTrigger = 'seat occupancy'
            elif jumpTrig:
                self.lastTrigger = 'phase jump'
            else:
                self.lastTrigger = 'micro-motion (breathing band)'
        else:
            if metricReady:
                self.quietSec += self.dt
                if self.quietSec >= EMPTY_CONFIRM_SEC:
                    self.state = STATE_EMPTY
                    self.lastTrigger = 'quiet confirmed'
                    self.peakHoldSec = 0.0

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


def zone_contains(zone, rangeBin, angleBin):
    """zoneDef 항목 [rangeStart, rangeLen, angleStart, angleLen] 포함 여부."""
    r0, rLen, a0, aLen = zone
    return (r0 <= rangeBin < r0 + rLen) and (a0 <= angleBin < a0 + aLen)


def _peak_heat_ok(heatmap, r, a, zone, tracking=False):
    """약한/클러터성 피크 배제: 존 내부 배경 대비 충분히 밝아야 함."""
    if heatmap is None:
        return True
    heat = np.asarray(heatmap)
    ri, ai = int(round(r)), int(round(a))
    nR, nA = heat.shape
    if not (0 <= ri < nR and 0 <= ai < nA):
        return False
    val = float(heat[ri, ai])
    if val < PEAK_HEAT_ABS_MIN * (0.85 if tracking else 1.0):
        return False
    r0, rLen, a0, aLen = [int(x) for x in zone]
    r1 = min(nR, r0 + rLen)
    a1 = min(nA, a0 + aLen)
    r0 = max(0, r0)
    a0 = max(0, a0)
    if r0 >= r1 or a0 >= a1:
        bg = float(np.median(heat))
    else:
        bg = float(np.median(heat[r0:r1, a0:a1]))
    ratio = PEAK_HEAT_TRACK_RATIO if tracking else PEAK_HEAT_BG_RATIO
    return val >= max(PEAK_HEAT_ABS_MIN * 0.7, bg * ratio)


def _front_crosstalk(heatmap, zoneDef, seat, r, a):
    """앞자리(조수석) 활동이 비슷한 방위의 뒷좌석 피크로 새는 경우 차단."""
    if heatmap is None or seat >= FRONT_SEAT_IDX or len(zoneDef) <= FRONT_SEAT_IDX:
        return False
    front = zoneDef[FRONT_SEAT_IDX]
    fa0, faLen = int(front[2]), int(front[3])
    # 조수석 방위와 거의 안 겹치면 크로스톡으로 보지 않음
    if a < fa0 - 2 or a >= fa0 + faLen + 2:
        return False

    heat = np.asarray(heatmap)
    nR, nA = heat.shape
    ri, ai = int(round(r)), int(round(a))
    if not (0 <= ri < nR and 0 <= ai < nA):
        return False
    rear_val = float(heat[ri, ai])

    fr0 = max(0, int(front[0]))
    fr1 = min(nR, fr0 + int(front[1]))
    a0 = max(0, ai - 2)
    a1 = min(nA, ai + 3)
    if fr0 >= fr1 or a0 >= a1:
        return False
    front_max = float(np.max(heat[fr0:fr1, a0:a1]))
    # 앞이 뒷 피크와 비슷하거나 더 세면 → 앞자리 누설로 간주
    return front_max >= rear_val * CROSSTALK_FRONT_RATIO


class SeatPeakFilter:
    """좌석별 피크 안정성 + 짧은 미검출 홀드(끊김 완화)."""

    def __init__(self, numZones):
        self.numZones = int(numZones)
        self.lastPos = [None] * self.numZones
        self.stable = [0] * self.numZones
        self.locked = [False] * self.numZones
        self.miss = [0] * self.numZones
        self.lastVS = [None] * self.numZones

    def is_tracking(self, seat):
        return bool(self.locked[seat])

    def remember_vitals(self, seat, vitals5):
        self.lastVS[seat] = np.array(vitals5, dtype=float, copy=True)

    def update_seat(self, seat, r, a):
        self.miss[seat] = 0
        prev = self.lastPos[seat]
        need = PEAK_RELOCK_FRAMES if self.locked[seat] else PEAK_STABLE_FRAMES
        if prev is None:
            self.lastPos[seat] = (r, a)
            self.stable[seat] = 1
            return False
        dist = ((r - prev[0]) ** 2 + (a - prev[1]) ** 2) ** 0.5
        # 추적 중이면 점프 허용 폭을 조금 넓힘
        maxDist = PEAK_STABLE_DIST * (1.4 if self.locked[seat] else 1.0)
        if dist <= maxDist:
            self.stable[seat] += 1
            self.lastPos[seat] = (0.7 * prev[0] + 0.3 * r, 0.7 * prev[1] + 0.3 * a)
        else:
            self.lastPos[seat] = (r, a)
            self.stable[seat] = 1
            return False
        if self.stable[seat] >= need:
            self.locked[seat] = True
            return True
        return self.locked[seat] and self.stable[seat] >= PEAK_RELOCK_FRAMES

    def reset_absent(self, presentSeats):
        for zi in range(self.numZones):
            if zi in presentSeats:
                continue
            self.miss[zi] += 1
            if self.miss[zi] > PEAK_MISS_HOLD_FRAMES:
                self.stable[zi] = 0
                self.locked[zi] = False
                self.lastVS[zi] = None

    def hold_decisions_and_vitals(self, decisions, seatVS):
        """피크가 잠깐 없어도 locked 좌석은 점유/위상 유지."""
        for zi in range(self.numZones):
            if decisions[zi]:
                continue
            if self.locked[zi] and self.miss[zi] <= PEAK_MISS_HOLD_FRAMES:
                decisions[zi] = 1
                if self.lastVS[zi] is not None:
                    base = zi * 5
                    seatVS[base:base + 5] = self.lastVS[zi]


def remap_peaks_to_seats(zoneDef, updatedZones, personsDetected, vitalSigns,
                         heatmap=None, peakFilter=None):
    """펌웨어 피크 슬롯(방위 정렬)을 zoneDef 좌석 슬롯으로 재매핑."""
    numZones = len(zoneDef) if zoneDef is not None else 0
    decisions = [0] * max(numZones, 4)
    seatVS = np.zeros(20, dtype=float)
    nPerson = int(personsDetected) if personsDetected is not None else 0

    if zoneDef is None or numZones == 0 or updatedZones is None or vitalSigns is None:
        return decisions, seatVS

    present = set()
    for i in range(min(nPerson, 4)):
        r = float(updatedZones[i][0])
        a = float(updatedZones[i][1])
        if r == 0 and a == 0:
            continue
        seat = None
        zone = None
        for zi, z in enumerate(zoneDef[:numZones]):
            if zone_contains(z, r, a):
                seat = zi
                zone = z
                break
        if seat is None:
            continue

        tracking = peakFilter.is_tracking(seat) if peakFilter is not None else False
        if not _peak_heat_ok(heatmap, r, a, zone, tracking=tracking):
            continue
        if _front_crosstalk(heatmap, zoneDef, seat, r, a):
            continue

        present.add(seat)
        baseSrc = i * 5
        baseDst = seat * 5
        for k in range(5):
            seatVS[baseDst + k] = vitalSigns[baseSrc + k]
        if peakFilter is not None:
            peakFilter.remember_vitals(seat, seatVS[baseDst:baseDst + 5])

        if peakFilter is None:
            decisions[seat] = 1
        elif peakFilter.update_seat(seat, r, a):
            decisions[seat] = 1

    if peakFilter is not None:
        peakFilter.reset_absent(present)
        peakFilter.hold_decisions_and_vitals(decisions, seatVS)

    return decisions, seatVS


class ClearanceMonitor:
    """전 존 관리 + 상태 전이 로그."""

    def __init__(self, numZones, fps):
        self.numZones = int(numZones)
        self.zones = [ZoneClearance(fps) for _ in range(self.numZones)]
        self.peakFilter = SeatPeakFilter(self.numZones)
        self._prevStates = [STATE_UNKNOWN] * self.numZones
        self.transitions = []

    def update(self, decisions, vitalSigns, now=0.0):
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


def _selftest():
    fps = 5.0
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
    res, t = run(mon, MICRO_WINDOW_SEC + EMPTY_CONFIRM_SEC + 2.0, noise)
    assert res[0]['state'] == STATE_EMPTY, res[0]
    assert res[0]['foldPermitted']
    print('OK: empty confirmed after quiet period, fold permitted\n')

    print('--- selftest: sleeping child (breathing only) ---')
    breath = lambda t: 0.15 * np.sin(2 * np.pi * 0.45 * t) + 0.002 * rng.standard_normal()
    res, t = run(mon, 12, breath, t0=t)
    assert res[0]['state'] == STATE_OCCUPIED, res[0]
    assert not res[0]['foldPermitted']
    print('OK: micro-motion latched OCCUPIED, fold blocked\n')

    print('--- selftest: person leaves -> EMPTY after confirm ---')
    res, t = run(mon, EMPTY_CONFIRM_SEC * 0.4, noise, t0=t)
    assert res[0]['state'] == STATE_OCCUPIED, res[0]
    res, t = run(mon, MICRO_WINDOW_SEC + EMPTY_CONFIRM_SEC + 1.0, noise, t0=t)
    assert res[0]['state'] == STATE_EMPTY, res[0]
    print('OK: asymmetric hysteresis\n')

    print('--- selftest: single-frame noise peak does not latch ---')
    mon2 = ClearanceMonitor(1, fps)
    res, t = run(mon2, MICRO_WINDOW_SEC + 1.0, noise)
    vs = np.zeros(20)
    mon2.update([1, 0, 0, 0], vs, now=t)  # 1프레임만 피크
    assert mon2.zones[0].state != STATE_OCCUPIED or mon2.zones[0].lastTrigger != 'seat occupancy'
    res, t = run(mon2, EMPTY_CONFIRM_SEC + 1.0, noise, t0=t + 0.2)
    assert res[0]['state'] == STATE_EMPTY, res[0]
    print('OK: single-frame peak flicker ignored\n')

    print('--- selftest: invalid data -> UNKNOWN ---')
    res, t = run(mon, 2, lambda t: float('nan'), t0=t)
    assert res[0]['state'] == STATE_UNKNOWN, res[0]
    print('OK: fail-safe on invalid data\n')

    print('--- selftest: peak remap passenger vs rear ---')
    zdef = [
        [20, 11, 8, 10],
        [20, 11, 18, 11],
        [20, 11, 29, 10],
        [8, 8, 28, 14],
    ]
    peaks = np.array([[10, 35], [24, 32], [25, 12], [0, 0]], dtype=float)
    vs = np.zeros(20)
    vs[0] = 1.1
    vs[5] = 2.2
    vs[10] = 3.3
    dec, mapped = remap_peaks_to_seats(zdef, peaks, 3, vs)
    assert dec == [1, 0, 1, 1], dec
    assert abs(mapped[0] - 3.3) < 1e-6
    assert abs(mapped[10] - 2.2) < 1e-6
    assert abs(mapped[15] - 1.1) < 1e-6
    print('OK: passenger/rear peaks remapped\n')

    print('--- selftest: weak rear-C noise peak rejected ---')
    heat = np.ones((64, 48)) * 200.0
    heat[24, 24] = 380.0
    peaks_n = np.array([[24, 24], [0, 0], [0, 0], [0, 0]], dtype=float)
    vs_n = np.zeros(20)
    vs_n[0] = 0.5
    pf = SeatPeakFilter(4)
    dec_n, mapped_n = remap_peaks_to_seats(
        zdef, peaks_n, 1, vs_n, heatmap=heat, peakFilter=pf)
    assert dec_n[1] == 0, dec_n
    assert abs(mapped_n[5]) < 1e-6
    print('OK: weak center noise ignored\n')

    print('--- selftest: strong stable peak accepted ---')
    heat2 = np.ones((64, 48)) * 200.0
    heat2[25, 12] = 1200.0
    peaks_s = np.array([[25, 12], [0, 0], [0, 0], [0, 0]], dtype=float)
    vs_s = np.zeros(20)
    vs_s[0] = 0.9
    pf2 = SeatPeakFilter(4)
    accepted = False
    for _ in range(PEAK_STABLE_FRAMES):
        dec_s, mapped_s = remap_peaks_to_seats(
            zdef, peaks_s, 1, vs_s, heatmap=heat2, peakFilter=pf2)
        if dec_s[0] == 1:
            accepted = True
    assert accepted, dec_s
    assert abs(mapped_s[0] - 0.9) < 1e-6
    print('OK: strong stable peak occupied\n')

    print('--- selftest: front crosstalk does not occupy rear ---')
    zdef2 = [
        [24, 9, 10, 9],
        [24, 9, 20, 8],
        [24, 9, 29, 10],
        [8, 7, 7, 12],
    ]
    heat3 = np.ones((64, 48)) * 200.0
    # 조수석에 강한 반사 + 같은 방위 뒷좌에 약한 누설
    heat3[10:15, 10:16] = 900.0
    heat3[12, 12] = 1100.0
    heat3[26, 12] = 700.0
    peaks_c = np.array([[26, 12], [0, 0], [0, 0], [0, 0]], dtype=float)
    vs_c = np.zeros(20)
    vs_c[0] = 0.4
    pf3 = SeatPeakFilter(4)
    for _ in range(5):
        dec_c, _ = remap_peaks_to_seats(
            zdef2, peaks_c, 1, vs_c, heatmap=heat3, peakFilter=pf3)
    assert dec_c[0] == 0, dec_c
    print('OK: front→rear crosstalk blocked\n')

    print('all selftests passed')


if __name__ == '__main__':
    _selftest()
