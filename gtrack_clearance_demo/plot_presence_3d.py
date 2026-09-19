# gtrack_clearance_demo / plot_presence_3d.py
# ---------------------------------------------------------------------------
# presence 폴백 전용 3D 뷰. plot_gtrack_clearance_3d.ClearancePlot3D(무수정)을 상속해
#   + 포인트클라우드 산점(존 안=주황, 존 밖=회청)
#   + 존별 상태/점유근거(track|presence|both)/존 내 점 수/최대 점 수
#   + presence 단독으로 점유를 유지한 누적 시간(pres-hold)
# 를 덧붙인다. static death 보완이 실제로 동작하는지 눈으로 확인하는 용도.
#
# 필요: PyOpenGL. 하드웨어 없이 확인: py plot_presence_3d.py
# ---------------------------------------------------------------------------
import numpy as np
import pyqtgraph.opengl as gl
from PyQt5 import QtCore, QtWidgets

import clearance_gtrack as cg
import presence_monitor as pm
from plot_gtrack_clearance_3d import ClearancePlot3D

COL_IN = (1.00, 0.62, 0.15, 0.95)     # 존 안 + 높이대 통과 포인트
COL_OUT = (0.45, 0.55, 0.65, 0.35)    # 그 외 포인트
SRC_COLOR = {'track': '#5aaaff', 'presence': '#ffa027', 'both': '#7de08a', 'none': '#8a95a0'}


class PresencePlot3D(ClearancePlot3D):
    def __init__(self, seats, min_points=pm.PRESENCE_MIN_POINTS):
        super().__init__(seats)
        self.min_points = min_points
        self.win.setWindowTitle('GTRACK Seat Clearance — presence fallback (3D)')

        # 포인트클라우드 산점 (매 프레임 setData)
        self.scatter = gl.GLScatterPlotItem(pos=np.zeros((0, 3)), size=5.0)
        self.view.addItem(self.scatter)

        # 존별 상태 패널
        self.stats = QtWidgets.QLabel('')
        f = self.stats.font()
        f.setFamily('Consolas')
        f.setPointSize(10)
        self.stats.setFont(f)
        self.stats.setTextFormat(QtCore.Qt.RichText)
        self.stats.setAlignment(QtCore.Qt.AlignLeft)
        self.win.layout().insertWidget(1, self.stats)

        self._maxn = {z.name: 0 for z in seats}
        self._preshold = {z.name: 0.0 for z in seats}
        self._lastNow = None
        # 3D 창에 포커스가 있을 때 누른 키도 main 이 마커로 읽을 수 있게 모아둔다
        self.keys = []
        for w in (self.win, self.view):          # 3D 뷰가 포커스를 가져가도 키를 받는다
            orig = w.keyPressEvent

            def _hook(ev, _orig=orig):
                self.keys.append(ev.text().lower())
                _orig(ev)                        # 기존 동작(카메라 조작 등) 유지
            w.keyPressEvent = _hook

    # ---- 포인트가 어느 존에도 속하고 높이대를 통과하는지 ----
    def _in_any_zone(self, p):
        if not (pm.Z_MIN <= p.z <= pm.Z_MAX):
            return False
        for z in self.seats:
            if z.xmin <= p.x <= z.xmax and z.ymin <= p.y <= z.ymax:
                return True
        return False

    def update(self, tracks, points, results, foldOk, now=None):
        # 존 박스 색 + 트랙 박스 + 배너는 부모가 처리
        super().update(tracks, results, foldOk)

        # ---- 포인트클라우드 ----
        if points:
            pos = np.array([[p.x, p.y, p.z] for p in points], dtype=float)
            col = np.array([COL_IN if self._in_any_zone(p) else COL_OUT for p in points],
                           dtype=float)
            self.scatter.setData(pos=pos, color=col, size=5.0)
        else:
            self.scatter.setData(pos=np.zeros((0, 3)))

        # ---- 존별 통계 ----
        dt = 0.0
        if now is not None and self._lastNow is not None:
            dt = max(0.0, now - self._lastNow)
        if now is not None:
            self._lastNow = now

        rows = []
        for r in (results or []):
            zn = r['zone']
            n = r.get('npts', 0)
            src = r.get('source', 'none')
            self._maxn[zn] = max(self._maxn.get(zn, 0), n)
            if src == 'presence':
                self._preshold[zn] += dt
            st = r['state']
            stcol = {cg.STATE_EMPTY: '#3cbe6e', cg.STATE_OCCUPIED: '#e13c3c',
                     cg.STATE_UNKNOWN: '#d7a028'}.get(st, '#888')
            rows.append(
                "{:<10s} <b><span style='color:{}'>{:<8s}</span></b>"
                " src=<b><span style='color:{}'>{:<8s}</span></b>"
                " n={:>3d} (max {:>3d})  pres-hold {:>5.1f}s".format(
                    zn, stcol, st, SRC_COLOR.get(src, '#888'), src,
                    n, self._maxn.get(zn, 0), self._preshold.get(zn, 0.0)))
        head = "points={:<4d} tracks={:<3d} min_points={}".format(
            len(points or []), len(tracks or []), self.min_points)
        self.stats.setText('<pre style="margin:2px 6px">' + head + '\n' + '\n'.join(rows) + '</pre>')

        self.app.processEvents()


# ===========================================================================
if __name__ == '__main__':
    # 하드웨어 없이: 뒷좌-좌에 포인트만 주고 presence 로 OCCUPIED 되는지 눈으로 확인
    import time
    from pointcloud_parser import Point

    seats = cg.DEFAULT_SEATS
    plot = PresencePlot3D(seats)
    mon = pm.PresenceClearanceMonitor(seats)
    z = [s for s in seats if s.name == 'Rear-L'][0]
    cx, cy = (z.xmin + z.xmax) / 2, (z.ymin + z.ymax) / 2

    t0 = time.time()
    while True:
        now = time.time() - t0
        if now > 20:
            break
        # 8초 이후엔 트랙 없이 포인트만 (static death 상황 재현)
        pts = [Point(cx + 0.05 * i, cy - 0.05 * i, 0.9, 0.0, 100) for i in range(5)]
        tracks = []
        if now < 8.0:
            from track_parser import Track
            tracks = [Track(1, cx, cy, 0.9)]
        res = mon.update(tracks, pts, now)
        plot.update(tracks, pts, res, mon.fold_permitted(now), now)
        time.sleep(0.05)
    print('selftest view done')
