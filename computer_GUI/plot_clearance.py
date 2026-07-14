# 시트 폴딩 clearance 판정 시각화.
# 왼쪽: Range-Azimuth 히트맵 + 존 경계 (상태별 색), 오른쪽: 존별 위상 파형 + 판정 상태.
# 상단: 전체 폴딩 허용/차단 배너.

import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np

import clearance

ZONE_TITLES = ['Zone 0', 'Zone 1', 'Zone 2', 'Zone 3']
BUFFER_LEN = 128

# 상태별 표시 색
STATE_COLORS = {
    clearance.STATE_EMPTY: 'green',
    clearance.STATE_OCCUPIED: 'red',
    clearance.STATE_UNKNOWN: 'darkorange',
}


class PlotHelper:
    def __init__(self, zoneDef=None, numZones=0) -> None:
        self.zoneDef = zoneDef if zoneDef is not None else []
        self.numZones = int(numZones)

        fig = plt.gcf()
        fig.suptitle('SEAT FOLD: ---', fontsize=14, fontweight='bold')

        # 히트맵 (왼쪽)
        self.axHeatmap = plt.subplot2grid((4, 8), (0, 0), colspan=3, rowspan=4)
        self.axHeatmap.set_title('Range-Azimuth + zones')
        self.graphHeatmap = self.axHeatmap.imshow(
            np.zeros((64, 48)), vmin=0, vmax=1200, aspect='auto')

        # 존별 위상 파형 (오른쪽, 세로로 4개)
        self.axPhase = []
        self.graphPhase = []
        self.phaseData = []
        for i in range(4):
            ax = plt.subplot2grid((4, 8), (i, 3), colspan=5)
            ax.set_title(ZONE_TITLES[i], fontsize=8)
            buf = np.zeros(BUFFER_LEN)
            plt.ion()
            g, = ax.plot(buf, 'b')
            ax.set_ylim([-1, 1])
            self.axPhase.append(ax)
            self.graphPhase.append(g)
            self.phaseData.append(buf)

        plt.subplots_adjust(hspace=0.6, wspace=0.8, top=0.88, bottom=0.06)

        # 존 경계 사각형 - 상태에 따라 색이 바뀜
        # zoneDef 각 항목: [rangeStart, rangeLen, angleStart, angleLen]
        self.zoneRects = []
        for i in range(min(self.numZones, 4)):
            z = self.zoneDef[i]
            rect = patches.Rectangle((z[2], z[0]), z[3], z[1],
                                     linewidth=2, edgecolor='darkorange',
                                     facecolor='none')
            self.axHeatmap.add_patch(rect)
            self.zoneRects.append(rect)

        self.frameCounter = 0

    def update(self, heatmapData, vitalSigns, results, foldOk):
        """results: clearance.ClearanceMonitor.update() 반환 리스트, foldOk: 전 존 EMPTY 여부."""
        self.graphHeatmap.set_data(heatmapData)

        # 상단 배너: 폴딩 허용/차단
        banner = 'SEAT FOLD: PERMITTED' if foldOk else 'SEAT FOLD: BLOCKED'
        plt.gcf().suptitle(banner, fontsize=14, fontweight='bold',
                           color='green' if foldOk else 'red')

        for i in range(4):
            # 위상 파형은 항상 롤링 갱신 (판정 근거를 눈으로 확인할 수 있도록)
            phase = vitalSigns[i * 5 + 0] if vitalSigns is not None else 0.0
            self.phaseData[i][self.frameCounter] = phase if np.isfinite(phase) else 0.0
            self.graphPhase[i].set_ydata(self.phaseData[i])
            # 위상 스케일이 존/장면마다 달라 자동 스케일링
            lim = max(0.5, np.max(np.abs(self.phaseData[i])) * 1.2)
            self.axPhase[i].set_ylim([-lim, lim])

            if i < len(results):
                r = results[i]
                color = STATE_COLORS[r['state']]
                if r['state'] == clearance.STATE_EMPTY:
                    detail = 'fold OK'
                elif r['state'] == clearance.STATE_OCCUPIED:
                    detail = r['trigger']
                else:
                    detail = 'confirming... {:.0f}/{:.0f}s'.format(
                        r['quietSec'], clearance.EMPTY_CONFIRM_SEC)
                title = '{}  [{}]  {}  band:{:.2f}'.format(
                    ZONE_TITLES[i], r['state'], detail, r['bandRatio'])
                if i < len(self.zoneRects):
                    self.zoneRects[i].set_edgecolor(color)
            else:
                color = 'gray'
                title = '{} (unused)'.format(ZONE_TITLES[i])

            self.axPhase[i].set_title(title, fontsize=8, color=color)

        self.frameCounter += 1
        if self.frameCounter > BUFFER_LEN - 1:
            self.frameCounter = 0

        plt.draw()
        plt.pause(0.01)
