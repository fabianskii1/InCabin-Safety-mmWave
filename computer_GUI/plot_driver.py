import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np

# vitalSigns 존별 5칸: +0 위상, +1 심박파형, +2 호흡파형, +3 심박수, +4 호흡수
BUFFER_LEN = 128
RATE_HISTORY_LEN = 30
CONF_GOOD = 0.25
CONF_FAIR = 0.12
# 운전석 창(zoneDef)에서 허용 여유(bin). 피크 중심이 존 경계에 걸쳐도 잡히도록.
WINDOW_MARGIN = 2
# 운전자가 잠깐 미검출돼도 몇 프레임은 유지 (숨 참기 등 순간 드롭 대비)
DRIVER_HOLD_FRAMES = 20


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
        self.hold = 0
        self.lastHR = 0
        self.lastRR = 0
        self.frameCounter = 0

        plt.subplots_adjust(hspace=0.35, wspace=0.5, top=0.9, bottom=0.08)

    def _in_window(self, r, a):
        return (self.drvR0 <= r <= self.drvR1) and (self.drvA0 <= a <= self.drvA1)

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

        # 검출된 피크들 중 운전석 창 안에 있는 것을 찾음
        driverIdx = -1
        for i in range(min(int(personsDetected), 4)):
            r = zones[i][0]
            a = zones[i][1]
            if (r != 0 or a != 0) and self._in_window(r, a):
                driverIdx = i
                break

        if driverIdx >= 0:
            # 운전석 창 안에서 운전자 검출됨
            self.hold = DRIVER_HOLD_FRAMES
            base = 5 * driverIdx
            self.lastBreath = vitalSigns[base + 2]
            self.lastHeart = vitalSigns[base + 1]
            self.lastHR = int(np.floor(vitalSigns[base + 3]))
            self.lastRR = int(np.floor(vitalSigns[base + 4]))

            self.hrHistory.append(vitalSigns[base + 3])
            self.rrHistory.append(vitalSigns[base + 4])
            self.hrHistory = self.hrHistory[-RATE_HISTORY_LEN:]
            self.rrHistory = self.rrHistory[-RATE_HISTORY_LEN:]

            self.marker.set_data([zones[driverIdx][1]], [zones[driverIdx][0]])
            present = True
        else:
            # 창 안에 운전자 없음 -> hold 동안은 마지막 값 유지
            if self.hold > 0:
                self.hold -= 1
            self.marker.set_data([], [])
            present = self.hold > 0

        # 파형은 항상 이어서 롤링 (미검출 시 마지막 값 유지 = 평탄)
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
            holding = "" if driverIdx >= 0 else "  (holding)"
            title = "DRIVER  HR:{} (s{})  RR:{} (s{})  conf:{:.2f}{}".format(
                self.lastHR, int(round(hrStd)), self.lastRR, int(round(rrStd)),
                combConf, holding)
        else:
            self.hrHistory = []
            self.rrHistory = []
            titleColor = 'gray'
            title = "No driver detected in zone"

        self.axBreath.set_title(title, fontsize=9, color=titleColor)

        self.frameCounter += 1
        if self.frameCounter > BUFFER_LEN - 1:
            self.frameCounter = 0

        plt.draw()
        plt.pause(0.01)
