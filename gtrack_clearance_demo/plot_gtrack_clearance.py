# gtrack_clearance_demo / plot_gtrack_clearance.py
# 톱다운 캐빈 맵: 좌석 존(점유색) + 트랙 점 + FOLD 배너. pyqtgraph, --plot 로 사용.
# 좌표: X=측방(m), Y=전방/거리(m). 센서=원점. aspect 고정.

import sys
import numpy as np
import pyqtgraph as pg
from PyQt5 import QtCore, QtWidgets

import clearance_gtrack as cg

STATE_COLOR = {
    cg.STATE_EMPTY: (60, 190, 110),
    cg.STATE_OCCUPIED: (220, 60, 60),
    cg.STATE_UNKNOWN: (210, 160, 40),
}
GRID = (110, 116, 124)
YMAX = 2.2
XLIM = 1.6


class ClearancePlot:
    def __init__(self, seats):
        self.seats = seats
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        pg.setConfigOptions(antialias=True)
        self.win = QtWidgets.QWidget()
        self.win.setWindowTitle('GTRACK Seat Clearance (demo)')
        self.win.resize(680, 780)
        lay = QtWidgets.QVBoxLayout(self.win)

        self.banner = QtWidgets.QLabel('FOLD ---')
        f = self.banner.font(); f.setPointSize(16); f.setBold(True)
        self.banner.setFont(f); self.banner.setAlignment(QtCore.Qt.AlignCenter)
        lay.addWidget(self.banner)

        glw = pg.GraphicsLayoutWidget()
        self.p = glw.addPlot()
        self.p.setAspectLocked(True)
        self.p.setXRange(-XLIM, XLIM); self.p.setYRange(-0.15, YMAX)
        self.p.setLabel('bottom', '측방 X (m)'); self.p.setLabel('left', '거리 Y (m)')
        self.p.setMouseEnabled(x=False, y=False); self.p.hideButtons()

        # 거리 링
        th = np.radians(np.linspace(-75, 75, 60))
        for R in (0.5, 1.0, 1.5, 2.0):
            self.p.plot(R * np.sin(th), R * np.cos(th),
                        pen=pg.mkPen(GRID, width=1, style=QtCore.Qt.DotLine))
        # 센서
        self.p.plot([0], [0], pen=None, symbol='t1', symbolSize=15,
                    symbolBrush=(90, 200, 210))

        # 좌석 존 사각형 + 라벨
        self.zoneRects, self.zoneLabels = [], []
        for z in seats:
            rect = QtWidgets.QGraphicsRectItem(z.xmin, z.ymin,
                                               z.xmax - z.xmin, z.ymax - z.ymin)
            rect.setPen(pg.mkPen((150, 150, 160), width=2))
            rect.setBrush(pg.mkBrush(150, 150, 160, 40))
            self.p.addItem(rect); self.zoneRects.append(rect)
            t = pg.TextItem(z.name, color=(210, 210, 210), anchor=(0.5, 0.5))
            t.setPos((z.xmin + z.xmax) / 2, (z.ymin + z.ymax) / 2)
            self.p.addItem(t); self.zoneLabels.append(t)

        self.scatter = pg.ScatterPlotItem(size=20, pen=pg.mkPen('w', width=1.5),
                                          brush=pg.mkBrush(90, 160, 255))
        self.p.addItem(self.scatter)
        self.trackLabels = []
        lay.addWidget(glw, stretch=1)
        self.win.show(); self.app.processEvents()

    def _clear_tracklabels(self):
        for t in self.trackLabels:
            self.p.removeItem(t)
        self.trackLabels = []

    def update(self, tracks, results, foldOk):
        self.banner.setText('FOLD PERMITTED' if foldOk else 'FOLD BLOCKED')
        self.banner.setStyleSheet('color: rgb(40,180,80);' if foldOk else 'color: rgb(220,60,60);')

        rmap = {r['zone']: r for r in results} if results else {}
        for rect, z in zip(self.zoneRects, self.seats):
            st = rmap.get(z.name, {}).get('state', cg.STATE_UNKNOWN)
            c = STATE_COLOR.get(st, (150, 150, 160))
            rect.setPen(pg.mkPen(c, width=2))
            rect.setBrush(pg.mkBrush(c[0], c[1], c[2], 55))

        self._clear_tracklabels()
        spots = []
        for t in tracks:
            spots.append({'pos': (t.x, t.y)})
            lab = pg.TextItem('#{}'.format(t.id), color=(240, 240, 240), anchor=(0.5, 1.4))
            lab.setPos(t.x, t.y)
            self.p.addItem(lab); self.trackLabels.append(lab)
        self.scatter.setData(spots)
        self.app.processEvents()

    def process_events(self):
        self.app.processEvents()
