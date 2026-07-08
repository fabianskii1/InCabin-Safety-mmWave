import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np

# vitalSigns 배열에서 각 존이 차지하는 시작 인덱스 (존별 5칸, 펌웨어 전송 순서와 일치)
#   base+0: 위상(unwrapped), base+1: 심박 파형, base+2: 호흡 파형, base+3: 심박수, base+4: 호흡수
ZONE_BASE_INDEX = [0, 5, 10, 15]
ZONE_TITLES = ['Red zone', 'Yellow zone', 'Green zone', 'Blue zone']
RECT_COLORS = ['r', 'y', 'g', 'b']

BUFFER_LEN = 128
# 점유 판정이 프레임마다 깜빡여도 몇 프레임 동안은 "사람 있음"으로 유지 (깜빡임 방지)
OCCUPANCY_HOLD_FRAMES = 15
# HR/RR 안정도(표준편차)를 계산할 최근 값 개수
RATE_HISTORY_LEN = 30
# 파형 주기성(스펙트럼 집중도) 신뢰도 임계값 (경험적, 튜닝 가능)
CONF_GOOD = 0.25
CONF_FAIR = 0.12


class PlotHelper:
    def __init__(self, zoneDef=None, numZones=0) -> None:
        self.zoneDef = zoneDef if zoneDef is not None else []
        self.numZones = int(numZones)

        # 히트맵 (왼쪽)
        self.axHeatmap = plt.subplot2grid((4, 8), (0, 0), colspan=2, rowspan=4)
        self.axHeatmap.set_title('Range-Azimuth + zones')

        # 각 존의 breath / heart 그래프 위치
        breathPos = [(0, 2), (0, 5), (2, 2), (2, 5)]
        heartPos = [(1, 2), (1, 5), (3, 2), (3, 5)]

        self.axBreath = []
        self.axHeart = []
        self.graphBreath = []
        self.graphHeart = []
        self.breathData = []
        self.heartData = []

        for i in range(4):
            axB = plt.subplot2grid((4, 8), breathPos[i], colspan=3)
            axH = plt.subplot2grid((4, 8), heartPos[i], colspan=3)
            axB.set_ylim([-1, 1])
            axH.set_ylim([-1, 1])
            axB.set_title(ZONE_TITLES[i], fontsize=8)

            breath = np.zeros(BUFFER_LEN)
            heart = np.zeros(BUFFER_LEN)

            plt.ion()
            gB, = axB.plot(breath, 'b')
            gH, = axH.plot(heart, 'r')

            self.axBreath.append(axB)
            self.axHeart.append(axH)
            self.graphBreath.append(gB)
            self.graphHeart.append(gH)
            self.breathData.append(breath)
            self.heartData.append(heart)

        # 서브플롯 간격을 넓혀 제목 겹침 방지
        plt.subplots_adjust(hspace=0.6, wspace=0.6, top=0.93, bottom=0.06)

        # 히트맵 초기화
        self.graphHeatmap = self.axHeatmap.imshow(
            np.zeros((64, 48)), vmin=0, vmax=1200, aspect='auto')

        # 설정된 존 경계(고정) 사각형 - 구역이 공간상 어떻게 나뉘는지 표시
        # zoneDef 각 항목: [rangeStart, rangeLen, angleStart, angleLen]
        self.zoneRects = []
        for i in range(min(self.numZones, 4)):
            z = self.zoneDef[i]
            rangeStart, rangeLen, angleStart, angleLen = z[0], z[1], z[2], z[3]
            rect = patches.Rectangle((angleStart, rangeStart), angleLen, rangeLen,
                                     linewidth=1, edgecolor=RECT_COLORS[i],
                                     facecolor='none', linestyle='--')
            self.axHeatmap.add_patch(rect)
            self.zoneRects.append(rect)

        # 감지된 타깃 위치 마커 (점유된 존만 표시)
        self.targetMarkers = []
        for i in range(4):
            m, = self.axHeatmap.plot([], [], marker='x', color=RECT_COLORS[i],
                                     markersize=9, markeredgewidth=2, linestyle='None')
            self.targetMarkers.append(m)

        # 존별 점유 유지 카운터 및 rate 이력
        self.occupiedHold = [0, 0, 0, 0]
        self.hrHistory = [[] for _ in range(4)]
        self.rrHistory = [[] for _ in range(4)]

        self.frameCounter = 0

    def _ordered(self, buf):
        # 링 버퍼를 오래된->최신 순서로 정렬
        fc = self.frameCounter
        return np.concatenate((buf[fc + 1:], buf[:fc + 1]))

    def _spectral_confidence(self, buf):
        # 파형이 얼마나 단일 주파수(주기적)에 집중돼 있는지 = 신뢰도 프록시 (0~1)
        x = self._ordered(buf).astype(float)
        x = x - x.mean()
        n = len(x)
        if n < 8 or not np.any(x):
            return 0.0
        mag = np.abs(np.fft.rfft(x * np.hanning(n)))
        if mag.size <= 1:
            return 0.0
        mag = mag[1:]  # DC 제거
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

    def update(self, heatmapData, vitalSigns, zones, decision):
        # decision: 존별 점유 여부(0=비어있음, 0이 아니면 사람 있음)
        self.graphHeatmap.set_data(heatmapData)

        for i in range(4):
            base = ZONE_BASE_INDEX[i]

            # 점유 판정에 hold 를 적용해 깜빡임을 완화
            if i < len(decision) and decision[i] != 0:
                self.occupiedHold[i] = OCCUPANCY_HOLD_FRAMES
            elif self.occupiedHold[i] > 0:
                self.occupiedHold[i] -= 1
            occupied = self.occupiedHold[i] > 0

            # 파형은 항상 이어서(롤링) 갱신 -> 버퍼를 지우지 않아 리셋되지 않음
            # 펌웨어 전송 순서: +1 심박파형, +2 호흡파형, +3 심박수, +4 호흡수
            self.breathData[i][self.frameCounter] = vitalSigns[base + 2]
            self.heartData[i][self.frameCounter] = vitalSigns[base + 1]
            heartrate = vitalSigns[base + 3]
            breathingrate = vitalSigns[base + 4]

            self.graphBreath[i].set_ydata(self.breathData[i])
            self.graphHeart[i].set_ydata(self.heartData[i])

            if occupied:
                # HR/RR 안정도 (최근 값들의 표준편차)
                self.hrHistory[i].append(heartrate)
                self.rrHistory[i].append(breathingrate)
                self.hrHistory[i] = self.hrHistory[i][-RATE_HISTORY_LEN:]
                self.rrHistory[i] = self.rrHistory[i][-RATE_HISTORY_LEN:]
                hrStd = np.std(self.hrHistory[i]) if len(self.hrHistory[i]) > 1 else 0.0
                rrStd = np.std(self.rrHistory[i]) if len(self.rrHistory[i]) > 1 else 0.0

                # 파형 주기성 기반 신뢰도
                heartConf = self._spectral_confidence(self.heartData[i])
                breathConf = self._spectral_confidence(self.breathData[i])
                combConf = min(heartConf, breathConf)
                titleColor = self._conf_color(combConf)

                title = "{}  HR:{} (s{})  RR:{} (s{})  conf:{:.2f}".format(
                    ZONE_TITLES[i], int(np.floor(heartrate)), int(round(hrStd)),
                    int(np.floor(breathingrate)), int(round(rrStd)), combConf)

                # 히트맵: 존 경계 강조 + 타깃 위치 마커
                if i < len(self.zoneRects):
                    self.zoneRects[i].set_linewidth(2.5)
                    self.zoneRects[i].set_linestyle('-')
                c = zones[i]
                self.targetMarkers[i].set_data([c[1]], [c[0]])
            else:
                # 비어있는 존: 이력 초기화, 마커/강조 해제
                self.hrHistory[i] = []
                self.rrHistory[i] = []
                titleColor = 'gray'
                title = "{} (empty)".format(ZONE_TITLES[i])
                if i < len(self.zoneRects):
                    self.zoneRects[i].set_linewidth(1)
                    self.zoneRects[i].set_linestyle('--')
                self.targetMarkers[i].set_data([], [])

            self.axBreath[i].set_title(title, fontsize=8, color=titleColor)

        self.frameCounter += 1
        if self.frameCounter > BUFFER_LEN - 1:
            self.frameCounter = 0

        plt.draw()
        plt.pause(0.01)
