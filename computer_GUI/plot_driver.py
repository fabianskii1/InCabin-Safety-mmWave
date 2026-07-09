import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np

# vitalSigns 존별 5칸: +0 위상, +1 심박파형, +2 호흡파형, +3 심박수, +4 호흡수
BUFFER_LEN = 128
RATE_HISTORY_LEN = 30
CONF_GOOD = 0.25
CONF_FAIR = 0.12
# 존 경계에서 흔들리는 피크도 잡되, 손/핸들까지 번지지 않게 적당히.
WINDOW_MARGIN = 2
# 존 안의 최대값이 전체 배경(중앙값)보다 이 비율 이상 강해야 "사람이 있다"로 인정.
ZONE_PRESENCE_RATIO = 1.5

# --- Dwell(고정) 상태 머신 파라미터 (프레임 주기 ~200ms 기준, 5fps) ---
# 가슴 후보가 이 프레임 수만큼 같은 자리에서 연속 관측되면 LOCK(고정)으로 진입.
ACQUIRE_FRAMES = 3
# LOCK 후 이 프레임 수 동안 위치를 고정하고 호흡/심박만 측정 (손 등 다른 움직임은 전부 무시).
# 10프레임 ~= 2초. 짧게 고정 후 자주 재확인 -> 반응성과 안정성의 절충.
DWELL_FRAMES = 10
# 재확인 시, 고정 위치 주변 이 거리(bin) 안에 강한 반사가 남아 있으면 "아직 가슴이 맞다"로 인정.
VERIFY_TOLERANCE_BINS = 3
# 재확인이 이 프레임 수만큼 연속 실패하면 LOCK 해제 후 다시 탐색(ACQUIRING).
RELEASE_FAIL_FRAMES = 6


class PlotHelper:
    def __init__(self, zoneDef=None, numZones=0) -> None:
        # 운전석 창을 zoneDef[0]에서 계산: [rangeStart, rangeLen, angleStart, angleLen]
        if zoneDef and len(zoneDef) > 0:
            z = zoneDef[0]
            self.rangeStart, self.rangeLen = z[0], z[1]
            self.angleStart, self.angleLen = z[2], z[3]
        else:
            self.rangeStart, self.rangeLen = 17, 7
            self.angleStart, self.angleLen = 11, 7

        self.drvR0 = self.rangeStart - WINDOW_MARGIN
        self.drvR1 = self.rangeStart + self.rangeLen + WINDOW_MARGIN
        self.drvA0 = self.angleStart - WINDOW_MARGIN
        self.drvA1 = self.angleStart + self.angleLen + WINDOW_MARGIN

        # 레이아웃: 왼쪽 히트맵, 오른쪽 위 호흡, 오른쪽 아래 심박
        self.axHeatmap = plt.subplot2grid((2, 3), (0, 0), rowspan=2)
        self.axBreath = plt.subplot2grid((2, 3), (0, 1), colspan=2)
        self.axHeart = plt.subplot2grid((2, 3), (1, 1), colspan=2)
        self.axBreath.set_ylim([-1, 1])
        self.axHeart.set_ylim([-1, 1])
        self.axHeart.set_title('Heart waveform (red)', fontsize=9)

        plt.ion()
        self.breathData = np.zeros(BUFFER_LEN)
        self.heartData = np.zeros(BUFFER_LEN)
        self.graphBreath, = self.axBreath.plot(self.breathData, 'b')
        self.graphHeart, = self.axHeart.plot(self.heartData, 'r')

        self.axHeatmap.set_title('Driver window')
        self.graphHeatmap = self.axHeatmap.imshow(
            np.zeros((64, 48)), vmin=0, vmax=1200, aspect='auto')

        # 운전석 창(고정) 표시
        self.rect = patches.Rectangle((self.angleStart, self.rangeStart),
                                      self.angleLen, self.rangeLen,
                                      linewidth=2, edgecolor='r', facecolor='none')
        self.axHeatmap.add_patch(self.rect)
        # 운전석 창 안에서 채택된 피크 위치 마커
        self.marker, = self.axHeatmap.plot([], [], 'rx', markersize=11,
                                           markeredgewidth=2, linestyle='None')

        self.hrHistory = []
        self.rrHistory = []
        self.lastBreath = 0.0
        self.lastHeart = 0.0
        self.lastHR = 0
        self.lastRR = 0
        self.frameCounter = 0

        # --- Dwell 상태 머신 ---
        # state: 'ACQUIRING'(가슴 탐색 중) / 'LOCKED'(고정하고 호흡 측정 중)
        self.state = 'ACQUIRING'
        self.lockPos = None          # 고정된 가슴 위치 (range, azimuth)
        self.dwellCounter = 0        # 현재 고정 구간에서 남은 프레임 수
        self.acquirePos = None       # 탐색 중 후보로 누적되는 위치
        self.acquireCount = 0        # 그 후보가 연속 관측된 프레임 수
        self.verifyFailCount = 0     # 고정 위치 재확인 연속 실패 횟수

        plt.subplots_adjust(hspace=0.35, wspace=0.5, top=0.9, bottom=0.08)

    def _in_window(self, r, a):
        return (self.drvR0 <= r <= self.drvR1) and (self.drvA0 <= a <= self.drvA1)

    def _select_driver_peak(self, zones, personsDetected):
        # 창 안에 들어오는 모든 피크를 후보로 모음 (노이즈/보조 반사로 여러 개 걸릴 수 있음)
        candidates = [i for i in range(min(int(personsDetected), 4))
                      if (zones[i][0] != 0 or zones[i][1] != 0) and self._in_window(zones[i][0], zones[i][1])]
        if not candidates:
            return -1
        if len(candidates) == 1:
            return candidates[0]
        # 후보가 여럿이면 현재 기준 위치(고정 위치 > 탐색 후보 > 창 중심)에 가장 가까운 피크를 선택.
        ref = self.lockPos or self.acquirePos
        if ref is not None:
            refR, refA = ref
        else:
            refR = self.rangeStart + self.rangeLen / 2.0
            refA = self.angleStart + self.angleLen / 2.0
        return min(candidates, key=lambda i: (zones[i][0] - refR) ** 2 + (zones[i][1] - refA) ** 2)

    def _nearest_fw_peak(self, zones, personsDetected, ref, maxDist=None, windowOnly=False):
        # ref(range,azimuth)에 가장 가까운 펌웨어 피크 인덱스 반환.
        # maxDist가 주어지면 그 거리(bin, 체비셰프) 안의 피크만 인정.
        # windowOnly=True면 존+경계(margin) 창 안의 피크만 인정 -> 존 경계까지는 측정 허용.
        best, bestDist = -1, None
        for i in range(min(int(personsDetected), 4)):
            if zones[i][0] == 0 and zones[i][1] == 0:
                continue
            if maxDist is not None and (abs(zones[i][0] - ref[0]) > maxDist
                                        or abs(zones[i][1] - ref[1]) > maxDist):
                continue
            if windowOnly and not self._in_window(zones[i][0], zones[i][1]):
                continue
            d = (zones[i][0] - ref[0]) ** 2 + (zones[i][1] - ref[1]) ** 2
            if bestDist is None or d < bestDist:
                best, bestDist = i, d
        return best

    def _heatmap_zone_peak(self, heatmapData):
        # 매 프레임 오는 원본 히트맵에서, 실제 존(빨간 박스) 안쪽만 스캔해 최댓값 위치를 찾는다.
        # margin이 아닌 코어 존만 봐서, 선택된 점(=마커)이 항상 박스 안에 오도록 한다.
        nR, nA = heatmapData.shape
        r0 = max(0, self.rangeStart)
        r1 = min(nR - 1, self.rangeStart + self.rangeLen)
        a0 = max(0, self.angleStart)
        a1 = min(nA - 1, self.angleStart + self.angleLen)
        if r0 > r1 or a0 > a1:
            return None
        sub = heatmapData[r0:r1 + 1, a0:a1 + 1]
        if sub.size == 0:
            return None
        peakVal = float(sub.max())
        background = float(np.median(heatmapData))
        if background <= 0 or peakVal < background * ZONE_PRESENCE_RATIO:
            return None
        rOff, aOff = np.unravel_index(int(sub.argmax()), sub.shape)
        return (r0 + int(rOff), a0 + int(aOff), peakVal)

    def _observe(self, heatmapData, zones, personsDetected):
        # 이번 프레임의 가슴 후보 위치를 하나 관측. 마커가 항상 존(박스) 안에 오도록,
        # 박스 안 히트맵 최강점을 우선한다. 없으면 존 안 펌웨어 피크로 대체. 둘 다 없으면 None.
        rawPeak = self._heatmap_zone_peak(heatmapData)
        if rawPeak is not None:
            return (rawPeak[0], rawPeak[1])
        idx = self._select_driver_peak(zones, personsDetected)
        if idx >= 0:
            return (zones[idx][0], zones[idx][1])
        return None

    def _lock_supported(self, heatmapData, zones, personsDetected, pos):
        # 고정 위치 pos 주변에 아직 강한 반사가 남아 있는지 재확인.
        # 펌웨어 피크가 tol 안에 있거나, 히트맵 국소 박스의 최댓값이 배경 대비 충분히 크면 유지.
        tol = VERIFY_TOLERANCE_BINS
        for i in range(min(int(personsDetected), 4)):
            if zones[i][0] == 0 and zones[i][1] == 0:
                continue
            if abs(zones[i][0] - pos[0]) <= tol and abs(zones[i][1] - pos[1]) <= tol:
                return True
        nR, nA = heatmapData.shape
        r0, r1 = max(0, pos[0] - tol), min(nR - 1, pos[0] + tol)
        a0, a1 = max(0, pos[1] - tol), min(nA - 1, pos[1] + tol)
        if r0 > r1 or a0 > a1:
            return False
        box = heatmapData[r0:r1 + 1, a0:a1 + 1]
        background = float(np.median(heatmapData))
        return background > 0 and float(box.max()) >= background * ZONE_PRESENCE_RATIO

    def _ordered(self, buf):
        fc = self.frameCounter
        return np.concatenate((buf[fc + 1:], buf[:fc + 1]))

    def _spectral_confidence(self, buf):
        x = self._ordered(buf).astype(float)
        x = x - x.mean()
        n = len(x)
        if n < 8 or not np.any(x):
            return 0.0
        mag = np.abs(np.fft.rfft(x * np.hanning(n)))
        if mag.size <= 1:
            return 0.0
        mag = mag[1:]
        total = mag.sum()
        if total <= 0:
            return 0.0
        return float(mag.max() / total)

    def _conf_color(self, conf):
        if conf >= CONF_GOOD:
            return 'green'
        if conf >= CONF_FAIR:
            return 'darkorange'
        return 'red'

    def update(self, heatmapData, vitalSigns, zones, personsDetected):
        # zones: 각 피크의 [range, azimuth] (TLV PEAK_POSITIONS), personsDetected: 검출 피크 수
        self.graphHeatmap.set_data(heatmapData)

        # 이번 프레임의 가슴 후보 위치를 하나 관측
        obsPos = self._observe(heatmapData, zones, personsDetected)

        present = False        # 이번 프레임에 호흡/심박을 측정(고정)하고 있는가
        status = ""

        if self.state == 'ACQUIRING':
            # 가슴 후보를 찾는 중. 같은 자리에서 ACQUIRE_FRAMES번 연속 관측되면 LOCK.
            if obsPos is not None:
                if self.acquirePos is not None and \
                        abs(obsPos[0] - self.acquirePos[0]) <= VERIFY_TOLERANCE_BINS and \
                        abs(obsPos[1] - self.acquirePos[1]) <= VERIFY_TOLERANCE_BINS:
                    self.acquireCount += 1
                else:
                    self.acquireCount = 1
                self.acquirePos = obsPos
                self.marker.set_color('orange')
                self.marker.set_data([obsPos[1]], [obsPos[0]])
                if self.acquireCount >= ACQUIRE_FRAMES:
                    self.state = 'LOCKED'
                    self.lockPos = obsPos
                    self.dwellCounter = DWELL_FRAMES
                    self.verifyFailCount = 0
                    self.acquirePos = None
                    self.acquireCount = 0
            else:
                self.acquirePos = None
                self.acquireCount = 0
                self.marker.set_data([], [])
            status = "acquiring"

        if self.state == 'LOCKED':
            # 고정된 위치에서 호흡/심박 측정. 손 등 다른 움직임은 무시하고 위치를 옮기지 않는다.
            # vital sign은 "고정된 가슴 위치 근처" 피크에서만 읽는다 (손 피크가 가까워져도 무시).
            present = True
            # 존(경계 margin 포함) 안이면 어디든, lockPos에 가장 가까운 펌웨어 피크에서 vital 읽기.
            # -> 히트맵 마커와 펌웨어 피크가 몇 bin 어긋나도 존 안이면 측정 유지.
            vsIdx = self._nearest_fw_peak(zones, personsDetected, self.lockPos,
                                          windowOnly=True)
            if vsIdx >= 0:
                base = 5 * vsIdx
                self.lastBreath = vitalSigns[base + 2]
                self.lastHeart = vitalSigns[base + 1]
                self.lastHR = int(np.floor(vitalSigns[base + 3]))
                self.lastRR = int(np.floor(vitalSigns[base + 4]))
                self.hrHistory.append(vitalSigns[base + 3])
                self.rrHistory.append(vitalSigns[base + 4])
                self.hrHistory = self.hrHistory[-RATE_HISTORY_LEN:]
                self.rrHistory = self.rrHistory[-RATE_HISTORY_LEN:]
            # vsIdx<0이면 가슴 근처에 펌웨어 피크가 없는 순간 -> 마지막 값 유지 (파형 연속성 보존)
            self.marker.set_color('red')
            self.marker.set_data([self.lockPos[1]], [self.lockPos[0]])

            self.dwellCounter -= 1
            if self.dwellCounter > 0:
                status = "locked {:.1f}s".format(self.dwellCounter * 0.2)
            else:
                # dwell 종료 -> 존(박스) 안에서 지금 가장 강한 지점으로 위치를 다시 잡는다.
                # 존 안에 움직임/사람이 있으면 항상 그쪽으로 재조정되므로 마커가 박스 밖에 갇히지 않음.
                zonePeak = self._heatmap_zone_peak(heatmapData)
                if zonePeak is not None:
                    self.lockPos = (zonePeak[0], zonePeak[1])
                    self.dwellCounter = DWELL_FRAMES
                    self.verifyFailCount = 0
                    status = "re-locked"
                elif self._lock_supported(heatmapData, zones, personsDetected, self.lockPos):
                    # 존 코어엔 약하지만 경계 근처에 지지가 남아 있으면 유지
                    self.dwellCounter = DWELL_FRAMES
                    self.verifyFailCount = 0
                    status = "re-locked"
                else:
                    # 지지 신호 없음 -> 몇 프레임 유예 후 해제
                    self.verifyFailCount += 1
                    if self.verifyFailCount >= RELEASE_FAIL_FRAMES:
                        self.state = 'ACQUIRING'
                        self.lockPos = None
                        self.verifyFailCount = 0
                        present = False
                        self.marker.set_data([], [])
                        status = "lost, re-acquiring"
                    else:
                        self.dwellCounter = 1  # 다음 프레임에 다시 재확인
                        status = "verifying"

        # 파형은 항상 이어서 롤링 (미측정 시 0으로 평탄)
        self.breathData[self.frameCounter] = self.lastBreath if present else 0.0
        self.heartData[self.frameCounter] = self.lastHeart if present else 0.0
        self.graphBreath.set_ydata(self.breathData)
        self.graphHeart.set_ydata(self.heartData)

        if present:
            hrStd = np.std(self.hrHistory) if len(self.hrHistory) > 1 else 0.0
            rrStd = np.std(self.rrHistory) if len(self.rrHistory) > 1 else 0.0
            heartConf = self._spectral_confidence(self.heartData)
            breathConf = self._spectral_confidence(self.breathData)
            combConf = min(heartConf, breathConf)
            titleColor = self._conf_color(combConf)
            title = "DRIVER  HR:{} (s{})  RR:{} (s{})  conf:{:.2f}  [{}]".format(
                self.lastHR, int(round(hrStd)), self.lastRR, int(round(rrStd)),
                combConf, status)
        else:
            self.hrHistory = []
            self.rrHistory = []
            titleColor = 'gray'
            title = "Searching for driver chest... [{}]".format(status)

        self.axBreath.set_title(title, fontsize=9, color=titleColor)

        self.frameCounter += 1
        if self.frameCounter > BUFFER_LEN - 1:
            self.frameCounter = 0

        plt.draw()
        plt.pause(0.01)
