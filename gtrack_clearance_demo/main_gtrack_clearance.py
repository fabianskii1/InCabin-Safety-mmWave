# gtrack_clearance_demo / main_gtrack_clearance.py
# ---------------------------------------------------------------------------
# 3D People Tracking 펌웨어 위에 좌석 clearance 판정을 얹는 데모.
# 펌웨어가 준 '트랙 좌표(Target List TLV 308)'를 파싱 -> 좌석 존 점유 -> clearance.
# 기존 프로젝트 파일 무수정. serialhelper 만 read-only 재사용.
#
# 사용법 (센서 = 3D People Tracking 펌웨어 플래시 상태):
#   py main_gtrack_clearance.py <userCOM> <dataCOM> \
#       "E:/radar_toolbox_4_00_00_05/source/ti/examples/Industrial_and_Personal_Electronics/People_Tracking/3D_People_Tracking/chirp_configs/ISK_incabin_multi.cfg"
#   옵션: --no-config (이미 스트리밍 중), --plot (톱다운 뷰)
#
# 하드웨어 없이 로직 검증:  py track_parser.py  /  py clearance_gtrack.py
# ---------------------------------------------------------------------------
import os
import sys
import time
import argparse

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

_GUI = os.path.join(os.path.dirname(__file__), '..', 'computer_GUI')
sys.path.insert(0, os.path.abspath(_GUI))
import serialhelper                    # noqa: E402  (read-only 재사용)

from track_parser import FrameParser   # noqa: E402
import clearance_gtrack as cg          # noqa: E402


def main():
    ap = argparse.ArgumentParser(description='GTRACK 좌표 기반 좌석 clearance 데모')
    ap.add_argument('userPort')
    ap.add_argument('dataPort')
    ap.add_argument('configFile', help='3D People Tracking cfg (예: ISK_incabin_multi.cfg)')
    ap.add_argument('--no-config', action='store_true')
    ap.add_argument('--plot', action='store_true')
    ap.add_argument('--zones', choices=list(cg.SEAT_SETS.keys()), default='vehicle',
                    help='좌석 존 세트: vehicle(실차 튜닝) / placeholder(적용 전 원본)')
    ap.add_argument('--print-period', type=float, default=1.0)
    args = ap.parse_args()

    seats = cg.SEAT_SETS[args.zones]
    print('[gtrack-clearance] 좌석 존 세트: {}'.format(args.zones))

    serialUser = serialhelper.SerialHelper(args.userPort, 115200)
    serialData = serialhelper.SerialHelper(args.dataPort, 921600)
    if args.no_config:
        print('[gtrack-clearance] --no-config: config 재전송 생략')
    else:
        with open(args.configFile, 'r', encoding='utf-8', errors='ignore') as f:
            serialUser.sendConfig(f.readlines())

    plot = None
    if args.plot:
        try:
            import plot_gtrack_clearance_3d as p3d
            plot = p3d.ClearancePlot3D(seats)
        except Exception as e:
            print('[gtrack-clearance] 3D --plot 실패, 2D로 폴백:', e)
            try:
                import plot_gtrack_clearance
                plot = plot_gtrack_clearance.ClearancePlot(seats)
            except Exception as e2:
                print('[gtrack-clearance] 2D도 실패(콘솔로 계속):', e2)

    parser = FrameParser()
    monitor = cg.ClearanceMonitor(seats)
    startT = time.time()
    lastPrint = 0.0
    lastTracks = []

    print('[gtrack-clearance] 시작. Ctrl+C 종료.')
    try:
        while True:
            data = serialData.readall()
            dbg = serialUser.readall()
            if dbg:
                sys.stdout.write(str(dbg, 'utf-8', 'replace'))
            frames = parser.feed(data) if data else []
            now = time.time() - startT
            results = None
            for tracks in frames:
                lastTracks = tracks
                results = monitor.update(tracks, now)
                if plot is not None:
                    plot.update(tracks, results, monitor.fold_permitted(now))
            if results is None:
                results = monitor.update(lastTracks, now)   # 프레임 없어도 시간 진행(하차 확정)
                if plot is not None:
                    plot.update(lastTracks, results, monitor.fold_permitted(now))

            if now - lastPrint >= args.print_period:
                lastPrint = now
                foldOk = monitor.fold_permitted(now)
                zs = ' | '.join('{}:{}'.format(r['zone'], r['state'][:3]) for r in results)
                banner = 'FOLD PERMITTED' if foldOk else 'FOLD BLOCKED'
                coords = '  '.join('#{}(x={:+.2f},y={:.2f})'.format(t.id, t.x, t.y)
                                   for t in lastTracks)
                print('[clr] t={:5.1f}s  {}  {}'.format(now, banner, zs))
                if coords:
                    print('      tracks: ' + coords)   # 존 캘리브레이션용 좌표
    except KeyboardInterrupt:
        print('\n[gtrack-clearance] 종료.')


if __name__ == '__main__':
    main()
