# 시트 폴딩 clearance 판정 시각화 (pyqtgraph 실시간)
# 왼쪽: Range-Azimuth 히트맵 + 좌석 존, 오른쪽: 존별 위상 + 상태

import sys

import numpy as np
import pyqtgraph as pg
from PyQt5 import QtCore, QtGui, QtWidgets

import clearance

ZONE_TITLES = ['Rear-L', 'Rear-C', 'Rear-R', 'Passenger']
BUFFER_LEN = 128

STATE_COLORS = {
    clearance.STATE_EMPTY: (40, 180, 80),
    clearance.STATE_OCCUPIED: (220, 50, 50),
    clearance.STATE_UNKNOWN: (230, 140, 30),
}


def _qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    return app


class PlotHelper:
    def __init__(self, zoneDef=None, numZones=0) -> None:
        self.zoneDef = zoneDef if zoneDef is not None else []
        self.numZones = int(numZones)

        self.app = _qapp()
        pg.setConfigOptions(imageAxisOrder='row-major', antialias=True)

        self.win = QtWidgets.QWidget()
        self.win.setWindowTitle('Seat-fold clearance (pyqtgraph)')
        self.win.resize(1100, 640)
        root = QtWidgets.QVBoxLayout(self.win)

        self.lblBanner = QtWidgets.QLabel('SEAT FOLD: ---')
        self.lblBanner.setAlignment(QtCore.Qt.AlignCenter)
        bf = self.lblBanner.font()
        bf.setPointSize(18)
        bf.setBold(True)
        self.lblBanner.setFont(bf)
        root.addWidget(self.lblBanner)

        body = QtWidgets.QHBoxLayout()
        root.addLayout(body, stretch=1)

        # ---- 히트맵 ----
        glw = pg.GraphicsLayoutWidget()
        self.heatPlot = glw.addPlot(title='Range-Azimuth + seats')
        self.heatPlot.setLabel('bottom', 'angle bin')
        self.heatPlot.setLabel('left', 'range bin')
        self.heatPlot.invertY(True)
        self.img = pg.ImageItem(levels=(0, 1200))
        self.heatPlot.addItem(self.img)

        self.zoneRects = []
        self.zoneLabels = []
        for i in range(min(self.numZones, 4)):
            z = self.zoneDef[i]
            rect = QtWidgets.QGraphicsRectItem(
                float(z[2]), float(z[0]), float(z[3]), float(z[1]))
            rect.setPen(pg.mkPen((230, 140, 30), width=2))
            rect.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
            self.heatPlot.addItem(rect)
            self.zoneRects.append(rect)

            text = pg.TextItem(ZONE_TITLES[i], color=(240, 240, 240), anchor=(0.5, 0.5))
            text.setPos(z[2] + z[3] * 0.5, z[0] + z[1] * 0.5)
            self.heatPlot.addItem(text)
            self.zoneLabels.append(text)

        self.peakMarkers = self.heatPlot.plot(
            [], [], pen=None, symbol='x',
            symbolPen=pg.mkPen('w', width=2),
            symbolBrush=None, symbolSize=14)
        body.addWidget(glw, stretch=3)

        # ---- 존별 위상 ----
        phaseBox = pg.GraphicsLayoutWidget()
        self.phasePlots = []
        self.phaseCurves = []
        self.phaseTitles = []
        self.phaseData = []
        x = np.arange(BUFFER_LEN)
        for i in range(4):
            p = phaseBox.addPlot(row=i, col=0, title=ZONE_TITLES[i])
            p.setYRange(-1, 1)
            p.showAxis('bottom', i == 3)
            p.setMouseEnabled(x=False, y=False)
            buf = np.zeros(BUFFER_LEN)
            curve = p.plot(x, buf, pen=pg.mkPen((80, 160, 255), width=1.5))
            self.phasePlots.append(p)
            self.phaseCurves.append(curve)
            self.phaseData.append(buf)
            self.phaseTitles.append(p)
        body.addWidget(phaseBox, stretch=5)

        self.frameCounter = 0
        self.win.show()
        self.process_events()

    def process_events(self):
        self.app.processEvents()

    def update(self, heatmapData, vitalSigns, results, foldOk,
               updatedZones=None, personsDetected=0):
        if heatmapData is not None:
            self.img.setImage(np.asarray(heatmapData), autoLevels=False)

        if foldOk:
            self.lblBanner.setText('SEAT FOLD: PERMITTED')
            self.lblBanner.setStyleSheet('color: rgb(40,180,80);')
        else:
            self.lblBanner.setText('SEAT FOLD: BLOCKED')
            self.lblBanner.setStyleSheet('color: rgb(220,50,50);')

        if updatedZones is not None and personsDetected > 0:
            n = min(int(personsDetected), 4)
            az = [float(updatedZones[i][1]) for i in range(n)]
            rs = [float(updatedZones[i][0]) for i in range(n)]
            self.peakMarkers.setData(az, rs)
        else:
            self.peakMarkers.setData([], [])

        x = np.arange(BUFFER_LEN)
        for i in range(4):
            phase = vitalSigns[i * 5 + 0] if vitalSigns is not None else 0.0
            self.phaseData[i][self.frameCounter] = phase if np.isfinite(phase) else 0.0
            self.phaseCurves[i].setData(x, self.phaseData[i])
            lim = max(0.5, float(np.max(np.abs(self.phaseData[i]))) * 1.2)
            self.phasePlots[i].setYRange(-lim, lim)

            if i < len(results):
                r = results[i]
                rgb = STATE_COLORS[r['state']]
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
                    self.zoneRects[i].setPen(pg.mkPen(rgb, width=2))
            else:
                rgb = (140, 140, 140)
                title = '{} (unused)'.format(ZONE_TITLES[i])

            self.phasePlots[i].setTitle(title, color=rgb)

        self.frameCounter += 1
        if self.frameCounter > BUFFER_LEN - 1:
            self.frameCounter = 0

        self.process_events()
