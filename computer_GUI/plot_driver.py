"""
룸미러(천장) 장착 mmWave → 운전자 가슴 소프트 추적 + 호흡/심박 추출

방식:
  1) soft tracking : 존 안 히트맵/피크를 EMA로 천천히 따라감 (하드 락 없음)
  2) 8~16초 슬라이딩 윈도우 FFT/자기상관으로 RR·HR 추정 (기본 12초)
  3) 조수석(보어사이트 반대/중앙) 각도 피크는 무시
"""
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np

# TLV vitalSigns per peak (firmware oddemo_scratch_output[50..]):
# [0] unwrapped phase
# [1] heart waveform
# [2] breath waveform
# [3] heart rate (bpm)
# [4] breathing rate (bpm)
# ※ 원본 plot.py는 [1]/[2] 이름이 뒤바뀌어 있음. 펌웨어 기준이 맞음.

BUFFER_LEN = 128
FRAME_DT = 0.2                  # frameCfg 200ms → 5 fps
ZONE_PRESENCE_RATIO = 1.5

# --- Soft tracking ---
ACQUIRE_FRAMES = 3
TRACK_ALPHA = 0.30
MAX_JUMP_BINS = 2
SEARCH_RADIUS = 3
LOST_FRAMES = 20
DRIVER_ANGLE_MIN = 28

# --- Rate window (8~16s, default 12s) ---
RATE_WINDOW = 60
RATE_MIN = 40
BREATH_HZ = (0.12, 0.50)
HEART_HZ = (0.8, 2.5)
RR_BPM_RANGE = (8, 24)
RR_HIST = 7
MAX_RR_STEP = 2
MAX_HR_STEP = 5
SPIKE_MAD = 3.5
# 무호흡 감지 균형: 오탐(호흡 중 HOLD)과 미탐(무호흡 못 잡음) 절충
SHORT_RMS_FRAMES = 6            # ~1.2초
SPEC_FRAMES = 25                # ~5초
BREATH_ACTIVE_RMS = 0.035
BREATH_DELTA_MIN = 0.018
BREATH_DROP_RATIO = 0.40        # 베이스라인 대비 40% 이하일 때만 붕괴
BREATH_PTP_MIN = 0.07
BREATH_BAND_DROP = 0.40
BREATH_HOLD_FRAMES = 5          # ~1.0초 quiet → HOLD
BREATH_RESUME_FRAMES = 2        # ~0.4초 움직임이면 바로 복귀
APNEA_ALERT_SEC = 3.0
BASELINE_ALPHA = 0.12
MIN_BASELINE = 0.03


class PlotHelper:
    def __init__(self, zoneDef=None, numZones=0) -> None:
        if zoneDef and len(zoneDef) > 0:
            z = zoneDef[0]
            self.r0, self.rLen = int(z[0]), int(z[1])
            self.a0, self.aLen = int(z[2]), int(z[3])
        else:
            # rearview default: ~0.53-0.89m, driver-right
            self.r0, self.rLen = 10, 7
            self.a0, self.aLen = 32, 5

        print('Rearview driver zone: range {}-{}, angle {}-{}'.format(
            self.r0, self.r0 + self.rLen - 1,
            self.a0, self.a0 + self.aLen - 1))

        self.axHeat = plt.subplot2grid((2, 3), (0, 0), rowspan=2)
        self.axBreath = plt.subplot2grid((2, 3), (0, 1), colspan=2)
        self.axHeart = plt.subplot2grid((2, 3), (1, 1), colspan=2)
        self.axBreath.set_ylim([-1, 1])
        self.axHeart.set_ylim([-1, 1])
        self.axHeart.set_title('Heart waveform', fontsize=9)

        plt.ion()
        self.breathBuf = np.zeros(BUFFER_LEN)
        self.heartBuf = np.zeros(BUFFER_LEN)
        self.gBreath, = self.axBreath.plot(self.breathBuf, 'b')
        self.gHeart, = self.axHeart.plot(self.heartBuf, 'r')

        self.axHeat.set_title('Driver window (rearview)')
        self.gHeat = self.axHeat.imshow(
            np.zeros((64, 48)), vmin=0, vmax=1200, aspect='auto')
        self.rect = patches.Rectangle(
            (self.a0, self.r0), self.aLen, self.rLen,
            linewidth=2, edgecolor='r', facecolor='none')
        self.axHeat.add_patch(self.rect)
        self.marker, = self.axHeat.plot(
            [], [], 'rx', markersize=11, markeredgewidth=2, linestyle='None')

        self.fc = 0
        self.lastBreath = 0.0
        self.lastHeart = 0.0
        self.lastRR = 0
        self.lastHR = 0
        self.prevBreath = None

        # soft track
        self.state = 'ACQUIRE'          # ACQUIRE / TRACK
        self.track = None               # (r, a) float
        self.acq = None
        self.acqCount = 0
        self.miss = 0

        # rate windows
        self.rrWin = []
        self.hrWin = []
        self.rrHist = []
        self.hrHist = []
        self.breathRmsWin = []
        self.breathSpecWin = []
        self.holdCount = 0
        self.resumeCount = 0
        self.breathing = True
        self.apneaSec = 0.0
        self.emergency = False
        self.breathBaseline = None      # 정상 호흡 RMS
        self.bandBaseline = None        # 정상 호흡대역 절대 에너지
        self.lastBreathBand = 0.0
        self.lastBandPower = 0.0

        plt.subplots_adjust(hspace=0.35, wspace=0.5, top=0.9, bottom=0.08)

    # ---------- geometry ----------
    def _in_zone(self, r, a):
        return (self.r0 <= r <= self.r0 + self.rLen
                and self.a0 <= a <= self.a0 + self.aLen
                and a >= DRIVER_ANGLE_MIN)

    def _driver_side(self, a):
        return a >= DRIVER_ANGLE_MIN

    # ---------- observation ----------
    def _zone_peak(self, heat, center=None, radius=None):
        nR, nA = heat.shape
        r0 = max(0, self.r0)
        r1 = min(nR - 1, self.r0 + self.rLen)
        a0 = max(0, self.a0)
        a1 = min(nA - 1, self.a0 + self.aLen)
        if center is not None and radius is not None:
            r0 = max(r0, int(round(center[0])) - radius)
            r1 = min(r1, int(round(center[0])) + radius)
            a0 = max(a0, int(round(center[1])) - radius)
            a1 = min(a1, int(round(center[1])) + radius)
        if r0 > r1 or a0 > a1:
            return None
        sub = heat[r0:r1 + 1, a0:a1 + 1]
        if sub.size == 0:
            return None
        bg = float(np.median(heat))
        peak = float(sub.max())
        if bg <= 0 or peak < bg * ZONE_PRESENCE_RATIO:
            return None
        ro, ao = np.unravel_index(int(sub.argmax()), sub.shape)
        r, a = r0 + int(ro), a0 + int(ao)
        if not self._driver_side(a):
            return None
        return (float(r), float(a))

    def _fw_near(self, zones, nPerson, ref, maxDist=None, zoneOnly=True):
        best, bestD = -1, None
        for i in range(min(int(nPerson), 4)):
            r, a = float(zones[i][0]), float(zones[i][1])
            if r == 0 and a == 0:
                continue
            if zoneOnly and not self._in_zone(r, a):
                continue
            if not zoneOnly and not self._driver_side(a):
                continue
            if maxDist is not None and (abs(r - ref[0]) > maxDist or abs(a - ref[1]) > maxDist):
                continue
            d = (r - ref[0]) ** 2 + (a - ref[1]) ** 2
            if bestD is None or d < bestD:
                best, bestD = i, d
        return best

    def _observe(self, heat, zones, nPerson):
        center = self.track
        radius = SEARCH_RADIUS if self.track is not None else None
        p = self._zone_peak(heat, center, radius)
        if p is not None:
            return p
        ref = self.track or (
            self.r0 + self.rLen / 2.0,
            self.a0 + self.aLen / 2.0,
        )
        idx = self._fw_near(zones, nPerson, ref, maxDist=None, zoneOnly=True)
        if idx >= 0:
            return (float(zones[idx][0]), float(zones[idx][1]))
        return None

    def _soft_update(self, obs):
        if self.track is None:
            self.track = obs
            return True
        jump = max(abs(obs[0] - self.track[0]), abs(obs[1] - self.track[1]))
        if jump > MAX_JUMP_BINS:
            return False
        a = TRACK_ALPHA
        self.track = (
            (1 - a) * self.track[0] + a * obs[0],
            (1 - a) * self.track[1] + a * obs[1],
        )
        return True

    def _vital_idx(self, zones, nPerson):
        if self.track is None:
            return 0
        for zoneOnly, dist in ((True, 4), (True, None), (False, 5), (False, None)):
            idx = self._fw_near(zones, nPerson, self.track, maxDist=dist, zoneOnly=zoneOnly)
            if idx >= 0:
                return idx
        return 0  # vitalSignsCfg range-gate 기본 채널

    # ---------- rate estimation ----------
    def _is_spike(self, x, win):
        if len(win) < 8:
            return False
        recent = np.asarray(win[-20:], dtype=float)
        med = float(np.median(recent))
        mad = float(np.median(np.abs(recent - med))) + 1e-6
        return abs(x - med) > SPIKE_MAD * mad

    def _breath_band_stats(self, samples):
        # (비율, 절대 에너지). 비율만 쓰면 무호흡 노이즈에서도 비율이 높게 나올 수 있음.
        n = len(samples)
        if n < 16:
            return None, None
        x = np.asarray(samples, dtype=float)
        x = x - x.mean()
        if np.std(x) < 1e-6:
            return 0.0, 0.0
        mag = np.abs(np.fft.rfft(x * np.hanning(n)))
        freqs = np.fft.rfftfreq(n, d=FRAME_DT)
        if mag.size <= 1:
            return 0.0, 0.0
        total = float(mag[1:].sum())
        band = (freqs >= BREATH_HZ[0]) & (freqs <= BREATH_HZ[1])
        power = float(mag[band].sum())
        ratio = (power / total) if total > 0 else 0.0
        return ratio, power

    def _update_breath_activity(self, breath):
        # 무호흡 우선: quiet는 OR 조건(하나라도 만족하면 후보),
        # 호흡 복귀(active)는 AND로 엄격하게.
        self.breathRmsWin.append(breath)
        if len(self.breathRmsWin) > SHORT_RMS_FRAMES:
            self.breathRmsWin = self.breathRmsWin[-SHORT_RMS_FRAMES:]
        self.breathSpecWin.append(breath)
        if len(self.breathSpecWin) > SPEC_FRAMES:
            self.breathSpecWin = self.breathSpecWin[-SPEC_FRAMES:]

        x = np.asarray(self.breathRmsWin, dtype=float)
        if len(x) >= 3:
            rms = float(np.sqrt(np.mean((x - x.mean()) ** 2)))
            ptp = float(x.max() - x.min())
        else:
            rms = abs(float(breath))
            ptp = abs(float(breath))

        delta = 0.0 if self.prevBreath is None else abs(breath - self.prevBreath)
        ratio, power = self._breath_band_stats(self.breathSpecWin)
        self.lastBreathBand = 0.0 if ratio is None else ratio
        self.lastBandPower = 0.0 if power is None else power

        # 호흡 중이고 파형이 뚜렷할 때만 베이스라인 갱신
        strong_now = (rms >= BREATH_ACTIVE_RMS and ptp >= BREATH_PTP_MIN
                      and (ratio is None or ratio >= 0.12))
        if self.breathing and strong_now:
            if self.breathBaseline is None:
                self.breathBaseline = max(rms, MIN_BASELINE)
            else:
                self.breathBaseline = ((1 - BASELINE_ALPHA) * self.breathBaseline
                                       + BASELINE_ALPHA * rms)
                self.breathBaseline = max(self.breathBaseline, MIN_BASELINE)
            if power is not None and power > 0:
                if self.bandBaseline is None:
                    self.bandBaseline = power
                else:
                    self.bandBaseline = ((1 - BASELINE_ALPHA) * self.bandBaseline
                                         + BASELINE_ALPHA * power)

        base = self.breathBaseline
        bandBase = self.bandBaseline

        # --- quiet: 진폭 붕괴가 확실할 때만 (OR만 쓰면 호흡 중 오탐) ---
        abs_quiet = (rms < BREATH_ACTIVE_RMS) and (delta < BREATH_DELTA_MIN) and (ptp < BREATH_PTP_MIN)
        collapsed = (base is not None) and (rms < base * BREATH_DROP_RATIO) and (ptp < BREATH_PTP_MIN)
        weak_band = (bandBase is not None and power is not None
                     and power < bandBase * BREATH_BAND_DROP
                     and rms < (base * 0.6 if base is not None else BREATH_ACTIVE_RMS * 1.5))
        # 절대 quiet 이거나, (붕괴 + 약밴드) — 호흡 사이 짧은 휴식만으로는 잘 안 걸림
        quiet = abs_quiet or (collapsed and weak_band) or (collapsed and delta < BREATH_DELTA_MIN)

        # --- active: 조금만 움직여도 호흡으로 복귀 (오탐 HOLD 빨리 해제) ---
        active = (rms >= BREATH_ACTIVE_RMS) or (ptp >= BREATH_PTP_MIN) or (delta >= BREATH_DELTA_MIN)
        if base is not None and rms >= base * 0.5:
            active = True

        if self.breathing:
            if quiet:
                self.holdCount += 1
                self.resumeCount = 0
                if self.holdCount >= BREATH_HOLD_FRAMES:
                    self.breathing = False
                    self.holdCount = 0
            else:
                # 호흡 흔들림이 있으면 카운터 즉시 리셋 → 중간중간 HOLD 오탐 감소
                self.holdCount = 0
        else:
            if active:
                self.resumeCount += 1
                self.holdCount = 0
                if self.resumeCount >= BREATH_RESUME_FRAMES:
                    self.breathing = True
                    self.resumeCount = 0
                    self.apneaSec = 0.0
                    self.emergency = False
            else:
                self.resumeCount = 0
                self.apneaSec += FRAME_DT
                if self.apneaSec >= APNEA_ALERT_SEC:
                    self.emergency = True

        return rms

    def _fft_bpm(self, samples, band):
        n = len(samples)
        if n < RATE_MIN:
            return None
        x = np.asarray(samples, dtype=float)
        x = x - x.mean()
        if np.std(x) < 1e-6:
            return None
        mag = np.abs(np.fft.rfft(x * np.hanning(n)))
        freqs = np.fft.rfftfreq(n, d=FRAME_DT)
        m = (freqs >= band[0]) & (freqs <= band[1])
        if not np.any(m):
            return None
        bandMag, bandF = mag[m], freqs[m]
        order = np.argsort(bandMag)[::-1]
        peaks = [(float(bandF[i]) * 60.0, float(bandF[i]), float(bandMag[i]))
                 for i in order[:4]]
        bpm, hz, score = peaks[0]
        # 2고조파면 기본파 선호
        for b2, h2, s2 in peaks[1:]:
            if 1.6 * h2 < hz < 2.4 * h2 and s2 > 0.35 * score:
                bpm = b2
                break
            if (RR_BPM_RANGE[0] <= b2 <= RR_BPM_RANGE[1]
                    and not (RR_BPM_RANGE[0] <= bpm <= RR_BPM_RANGE[1])
                    and s2 > 0.4 * score):
                bpm = b2
                break
        return bpm

    def _ac_bpm(self, samples, band):
        n = len(samples)
        if n < RATE_MIN:
            return None
        x = np.asarray(samples, dtype=float) - np.mean(samples)
        if np.std(x) < 1e-6:
            return None
        ac = np.correlate(x, x, mode='full')
        ac = ac[len(ac) // 2:]
        if ac[0] > 0:
            ac = ac / ac[0]
        lo = max(1, int(round(1.0 / band[1] / FRAME_DT)))
        hi = min(len(ac) - 1, int(round(1.0 / band[0] / FRAME_DT)))
        if hi <= lo:
            return None
        seg = ac[lo:hi + 1]
        if seg.max() < 0.15:
            return None
        lag = lo + int(np.argmax(seg))
        return 60.0 / (lag * FRAME_DT)

    def _estimate_rr(self):
        fft = self._fft_bpm(self.rrWin, BREATH_HZ)
        ac = self._ac_bpm(self.rrWin, BREATH_HZ)
        if fft is None and ac is None:
            return None
        if fft is None:
            return ac
        if ac is None:
            return fft
        if abs(fft - ac) <= 4:
            return 0.5 * (fft + ac)
        return min(fft, ac)  # 고조파 튐 억제

    def _estimate_hr(self):
        return self._fft_bpm(self.hrWin, HEART_HZ)

    def _stabilize(self, hist, raw, step):
        if raw is None:
            return None
        v = int(round(raw))
        hist.append(v)
        if len(hist) > RR_HIST:
            del hist[:-RR_HIST]
        med = int(round(float(np.median(hist))))
        if len(hist) >= 2:
            prev = hist[-2]
            if abs(med - prev) > step:
                med = prev + step if med > prev else prev - step
                hist[-1] = med
        return med

    def _reset_track(self):
        self.state = 'ACQUIRE'
        self.track = None
        self.acq = None
        self.acqCount = 0
        self.miss = 0
        self.rrWin = []
        self.hrWin = []
        self.rrHist = []
        self.hrHist = []
        self.breathRmsWin = []
        self.breathSpecWin = []
        self.holdCount = 0
        self.resumeCount = 0
        self.breathing = True
        self.apneaSec = 0.0
        self.emergency = False
        self.breathBaseline = None
        self.bandBaseline = None
        self.lastBreathBand = 0.0
        self.lastBandPower = 0.0
        self.prevBreath = None
        self.marker.set_data([], [])

    # ---------- main update ----------
    def update(self, heatmapData, vitalSigns, zones, personsDetected):
        self.gHeat.set_data(heatmapData)
        obs = self._observe(heatmapData, zones, personsDetected)
        present = False
        status = ''

        if self.state == 'ACQUIRE':
            if obs is not None:
                if (self.acq is not None
                        and abs(obs[0] - self.acq[0]) <= MAX_JUMP_BINS
                        and abs(obs[1] - self.acq[1]) <= MAX_JUMP_BINS):
                    self.acqCount += 1
                    a = TRACK_ALPHA
                    self.acq = (
                        (1 - a) * self.acq[0] + a * obs[0],
                        (1 - a) * self.acq[1] + a * obs[1],
                    )
                else:
                    self.acqCount = 1
                    self.acq = obs
                self.marker.set_color('orange')
                self.marker.set_data([self.acq[1]], [self.acq[0]])
                if self.acqCount >= ACQUIRE_FRAMES:
                    self.state = 'TRACK'
                    self.track = self.acq
                    self.acq = None
                    self.acqCount = 0
                    self.miss = 0
                    self.rrWin = []
                    self.hrWin = []
                    self.rrHist = []
                    self.hrHist = []
                    self.breathRmsWin = []
                    self.breathSpecWin = []
                    self.holdCount = 0
                    self.resumeCount = 0
                    self.breathing = True
                    self.apneaSec = 0.0
                    self.emergency = False
                    self.breathBaseline = None
                    self.bandBaseline = None
                    self.lastBreathBand = 0.0
                    self.lastBandPower = 0.0
                    self.prevBreath = None
            else:
                self.acq = None
                self.acqCount = 0
                self.marker.set_data([], [])
            status = 'acquiring'

        if self.state == 'TRACK':
            present = True
            if obs is not None and self._soft_update(obs):
                self.miss = 0
            else:
                self.miss += 1
                if self.miss >= LOST_FRAMES:
                    self._reset_track()
                    present = False
                    status = 'lost'

            if present:
                self.marker.set_color('red')
                self.marker.set_data([self.track[1]], [self.track[0]])

                vi = self._vital_idx(zones, personsDetected)
                base = 5 * vi
                # 펌웨어: [1]=heart, [2]=breath (plot.py 이름과 반대)
                heart = float(vitalSigns[base + 1])
                breath = float(vitalSigns[base + 2])
                fwHR = int(np.floor(vitalSigns[base + 3]))
                fwRR = int(np.floor(vitalSigns[base + 4]))

                # 무호흡 판정은 prevBreath 갱신 전에 (프레임 변화량 사용)
                rms = self._update_breath_activity(breath)

                if self.prevBreath is not None:
                    d = breath - self.prevBreath
                    if abs(d) > 0.35:
                        breath = self.prevBreath + 0.35 * np.sign(d)
                self.prevBreath = breath

                # 심박 파형은 항상 표시
                self.lastHeart = heart
                # 숨 참으면 호흡 파형 평탄 처리
                if self.breathing:
                    self.lastBreath = breath
                else:
                    self.lastBreath = 0.0

                # RR 윈도우: 실제 호흡 중일 때만 적재
                if self.breathing and not self._is_spike(breath, self.rrWin):
                    self.rrWin.append(breath)
                    if len(self.rrWin) > RATE_WINDOW:
                        self.rrWin = self.rrWin[-RATE_WINDOW:]
                # HR 윈도우는 심박 파형 계속 적재
                if not self._is_spike(heart, self.hrWin):
                    self.hrWin.append(heart)
                    if len(self.hrWin) > RATE_WINDOW:
                        self.hrWin = self.hrWin[-RATE_WINDOW:]

                if self.breathing:
                    rr = self._stabilize(self.rrHist, self._estimate_rr(), MAX_RR_STEP)
                    if rr is not None:
                        self.lastRR = rr
                    elif fwRR > 0 and len(self.rrWin) < RATE_MIN:
                        self.lastRR = fwRR
                else:
                    self.lastRR = 0
                    if len(self.rrWin) > 0:
                        self.rrWin = self.rrWin[1:]

                hr = self._stabilize(self.hrHist, self._estimate_hr(), MAX_HR_STEP)
                if hr is not None:
                    self.lastHR = hr
                elif fwHR > 0 and len(self.hrWin) < RATE_MIN:
                    self.lastHR = fwHR

                if self.emergency:
                    status = '!! NO BREATH {:.1f}s !!'.format(self.apneaSec)
                elif not self.breathing:
                    status = 'HOLD {:.1f}s rms:{:.3f} band:{:.2f}'.format(
                        self.apneaSec, rms, self.lastBreathBand)
                else:
                    status = 'soft {:.0f}/{:.0f}s rms:{:.3f} band:{:.2f}'.format(
                        len(self.rrWin) * FRAME_DT, RATE_WINDOW * FRAME_DT,
                        rms, self.lastBreathBand)

        self.breathBuf[self.fc] = self.lastBreath if present else 0.0
        self.heartBuf[self.fc] = self.lastHeart if present else 0.0
        self.gBreath.set_ydata(self.breathBuf)
        self.gHeart.set_ydata(self.heartBuf)

        if present:
            if self.emergency:
                title = 'EMERGENCY  NO BREATH {:.1f}s  HR:{}  [{}]'.format(
                    self.apneaSec, self.lastHR, status)
                color = 'red'
            elif not self.breathing:
                title = 'DRIVER  HR:{}  RR:0  [{}]'.format(self.lastHR, status)
                color = 'darkorange'
            else:
                title = 'DRIVER  HR:{}  RR:{}  [{}]'.format(
                    self.lastHR, self.lastRR, status)
                color = 'green' if len(self.rrWin) >= RATE_MIN else 'darkorange'
        else:
            title = 'Searching driver chest... [{}]'.format(status)
            color = 'gray'

        self.axBreath.set_title(title, fontsize=9, color=color)
        self.fc = (self.fc + 1) % BUFFER_LEN
        plt.draw()
        plt.pause(0.01)
