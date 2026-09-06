# gtrack_clearance_demo / plot_gtrack_clearance_3d.py
# 3D 캐빈 뷰(pyqtgraph.opengl): 좌석 존 3D 박스(점유색) + 트랙 점 + FOLD 배너.
# 좌표: X=측방(m), Y=전방/거리(m), Z=높이(m). 센서=원점, 바닥 그리드.
# 필요: PyOpenGL (pip install PyOpenGL).

import sys
import numpy as np
import pyqtgraph as pg
import pyqtgraph.opengl as gl
from pyqtgraph import Vector
from PyQt5 import QtCore, QtGui, QtWidgets

import clearance_gtrack as cg
from track_parser import SENSOR_HEIGHT

STATE_COLOR = {
    cg.STATE_EMPTY: (60, 190, 110),
    cg.STATE_OCCUPIED: (225, 60, 60),
    cg.STATE_UNKNOWN: (215, 160, 40),
}
ZONE_H = (0.0, 1.35)   # 존 박스 높이 범위(m, 시각화용)
PERSON_SIZE = (0.45, 0.45, 1.6)   # 트랙 사람 박스 크기 (x,y,z, m)


def _qcol(rgb, a=255):
    return QtGui.QColor(rgb[0], rgb[1], rgb[2], a)


class ClearancePlot3D:
    def __init__(self, seats):
        self.seats = seats
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        self.win = QtWidgets.QWidget()
        self.win.setWindowTitle('GTRACK Seat Clearance — 3D')
        self.win.resize(900, 760)
        lay = QtWidgets.QVBoxLayout(self.win)

        self.banner = QtWidgets.QLabel('FOLD ---')
        f = self.banner.font(); f.setPointSize(17); f.setBold(True)
        self.banner.setFont(f); self.banner.setAlignment(QtCore.Qt.AlignCenter)
        lay.addWidget(self.banner)

        self.view = gl.GLViewWidget()
        self.view.setBackgroundColor(pg.mkColor(18, 22, 27))
        lay.addWidget(self.view, stretch=1)
        # 캐빈 중앙을 바라보게 (높이 절반쯤을 중심으로)
        self.view.opts['center'] = Vector(0.0, 1.0, SENSOR_HEIGHT * 0.5)
        self.view.setCameraPosition(distance=4.6, elevation=18, azimuth=-90)

        # 바닥 그리드 (z=0 = 바닥)
        grid = gl.GLGridItem()
        grid.setSize(x=3.0, y=3.0); grid.setSpacing(x=0.5, y=0.5)
        grid.translate(0, 1.2, 0)
        self.view.addItem(grid)

        # 센서 마커 = 마운트 높이(월드 z=SENSOR_HEIGHT), 원점 위쪽
        self.view.addItem(gl.GLScatterPlotItem(
            pos=np.array([[0, 0, SENSOR_HEIGHT]]), size=16, color=(0.35, 0.8, 0.85, 1)))
        self._add_text((0, -0.05, SENSOR_HEIGHT + 0.08), 'SENSOR ({:.1f}m)'.format(SENSOR_HEIGHT),
                       (150, 200, 210))
        # 센서→바닥 수직 기둥(마운트 높이 가늠용)
        self.view.addItem(gl.GLLinePlotItem(
            pos=np.array([[0, 0, 0], [0, 0, SENSOR_HEIGHT]]),
            color=(0.35, 0.6, 0.7, 0.6), width=1.5))

        # 좌석 존 박스 + 라벨
        self.zoneBoxes, self.zoneLabels = [], []
        for z in seats:
            dx, dy, dz = z.xmax - z.xmin, z.ymax - z.ymin, ZONE_H[1] - ZONE_H[0]
            box = gl.GLBoxItem(size=Vector(dx, dy, dz))
            box.translate(z.xmin, z.ymin, ZONE_H[0])
            box.setColor(_qcol((150, 150, 160)))
            self.view.addItem(box); self.zoneBoxes.append(box)
            lab = self._add_text(((z.xmin + z.xmax) / 2, (z.ymin + z.ymax) / 2, ZONE_H[1] + 0.12),
                                 z.name, (225, 225, 225))
            self.zoneLabels.append(lab)

        # 트랙 = 사람 크기 박스 + 라벨 (매 프레임 생성/삭제)
        self.trackBoxes = []
        self.trackTexts = []

        self.win.show(); self.app.processEvents()

    def _add_text(self, pos, text, rgb):
        try:
            t = gl.GLTextItem(pos=np.array(pos, dtype=float), text=text,
                              color=(rgb[0], rgb[1], rgb[2], 255))
            self.view.addItem(t)
            return t
        except Exception:
            return None

    def _clear_tracktexts(self):
        for t in self.trackTexts:
            if t is not None:
                self.view.removeItem(t)
        self.trackTexts = []

    def _clear_trackboxes(self):
        for b in self.trackBoxes:
            self.view.removeItem(b)
        self.trackBoxes = []

    def update(self, tracks, results, foldOk):
        self.banner.setText('FOLD PERMITTED' if foldOk else 'FOLD BLOCKED')
        self.banner.setStyleSheet('color: rgb(50,190,90);' if foldOk else 'color: rgb(225,70,70);')

        rmap = {r['zone']: r for r in results} if results else {}
        for box, z in zip(self.zoneBoxes, self.seats):
            st = rmap.get(z.name, {}).get('state', cg.STATE_UNKNOWN)
            box.setColor(_qcol(STATE_COLOR.get(st, (150, 150, 160))))

        # 트랙 = 사람 크기 박스 (비주얼라이저 스타일)
        self._clear_trackboxes()
        self._clear_tracktexts()
        dx, dy, dz = PERSON_SIZE
        for t in tracks:
            box = gl.GLBoxItem(size=Vector(dx, dy, dz))
            box.translate(t.x - dx / 2, t.y - dy / 2, 0.0)   # 바닥에 세운 사람 박스
            box.setColor(_qcol((90, 170, 255)))
            self.view.addItem(box); self.trackBoxes.append(box)
            self.trackTexts.append(self._add_text(
                (t.x, t.y, dz + 0.1), '#{}'.format(t.id), (240, 240, 240)))

        self.app.processEvents()

    def process_events(self):
        self.app.processEvents()
