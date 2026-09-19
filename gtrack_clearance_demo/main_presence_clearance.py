# gtrack_clearance_demo / main_presence_clearance.py
# ---------------------------------------------------------------------------
# presence 폴백 좌석 clearance 데모.
#   트랙(1010) + 포인트클라우드(1020) 를 함께 받아, 존 점유 = 트랙 OR presence.
#   -> 정지 승객(static death)을 포인트로 잡아 fail-safe 보강.
# 기존 파일 무수정. serialhelper/track_parser/clearance_gtrack 재사용.
#
# 사용법 (포트·cfg 는 생략하면 아래 기본값):
#   py main_presence_clearance.py --gt Rear-L --log-frames V1.csv      # 탑승 측정
#   py main_presence_clearance.py --duration 120 --log-frames V8.csv   # 빈 차 측정
#   py main_presence_clearance.py COM5 COM6 "../chirp_configs/X.cfg"    # 포트·cfg 를 바꿀 때
#   옵션: --gt, --log-frames [경로], --duration 초, --no-config, --no-plot, --debug
#   판정 설정(K, F, E)은 아래 상수로 고정한다(9/18 채택 구성). 다른 값 비교는 analyze_frames 로.
#
# 로직 검증(하드웨어 무): py pointcloud_parser.py / py presence_monitor.py
# ---------------------------------------------------------------------------
import os
import sys
import time
import argparse
import signal

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

_GUI = os.path.join(os.path.dirname(__file__), '..', 'computer_GUI')
sys.path.insert(0, os.path.abspath(_GUI))
import serialhelper                                # noqa: E402

import clearance_gtrack as cg                       # noqa: E402
from pointcloud_parser import FrameParser2          # noqa: E402
from presence_monitor import PresenceClearanceMonitor, points_in_zone  # noqa: E402
from clearance_publisher import ClearancePublisher  # noqa: E402
from frame_logger import FrameLogger                # noqa: E402

try:
    import msvcrt   # Windows 비차단 키 입력(입장/퇴장 마커). 없으면 3D 창 키만 사용.
except ImportError:
    msvcrt = None

MARK_KEYS = {'s': 'sit', 'e': 'stand', 'm': 'act'}   # s/e 는 eval_logger 와 같은 키, m = 동작 시작
QUIT_KEY = 'q'

# ---- 기본 연결 ----
DEFAULT_USER_PORT = 'COM10'
DEFAULT_DATA_PORT = 'COM11'
DEFAULT_CFG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            '..', 'chirp_configs', 'ISK_incabin_tightgate.cfg'))

# ---- 판정 설정 (2026-09-18 채택: 정지 착석 실측 S1~S6 + 16세션 재생) ----
ZONE_SET = 'placeholder'     # 좌석 존 세트 (존 튜닝은 중단)
PRESENCE_CONFIRM = 2         # K: 점 근거는 최근 K*4 프레임 중 K회 이상일 때 인정
FREEZE_SEC = 3.0             # F: 좌표(mm)가 F초 이상 멈춘 트랙은 트랙 근거에서 제외
EMPTY_CONFIRM_SEC = 3.0      # E: 근거가 끊긴 뒤 E초 조용해야 EMPTY (clearance_gtrack 기본 4.0 을 이 프로세스에서만 바꿈)
PRINT_PERIOD = 1.0           # 콘솔 상태 출력 주기(초)


SUMMARY_ORDER = ['both', 'track', 'presence', 'hold', 'none']


def format_summary(srchist):
    """존별 점유 근거 집계를 문자열로. presence 폴백의 정량 기여도가 여기서 나온다."""
    lines = ['===== 존별 점유 근거 집계 (프레임 수 / 비율) =====']
    if not srchist:
        lines.append('  (수신 프레임 없음)')
        return '\n'.join(lines)
    for zone, d in srchist.items():
        tot = sum(d.values()) or 1
        occ = tot - d.get('none', 0)
        parts = ' '.join('{}={} ({:.1f}%)'.format(k, d.get(k, 0), 100.0 * d.get(k, 0) / tot)
                         for k in SUMMARY_ORDER if d.get(k))
        lines.append('  {:<10s} tot={} {}'.format(zone, tot, parts))
        if occ:
            only = d.get('presence', 0)
            lines.append('     - 점유 {} 프레임 중 presence 단독 {} ({:.1f}%), '
                         '히스테리시스 잔류 {} ({:.1f}%)'.format(
                             occ, only, 100.0 * only / occ,
                             d.get('hold', 0), 100.0 * d.get('hold', 0) / occ))
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description='presence 폴백 좌석 clearance 데모')
    ap.add_argument('userPort', nargs='?', default=DEFAULT_USER_PORT,
                    help='설정 포트 (기본 {})'.format(DEFAULT_USER_PORT))
    ap.add_argument('dataPort', nargs='?', default=DEFAULT_DATA_PORT,
                    help='데이터 포트 (기본 {})'.format(DEFAULT_DATA_PORT))
    ap.add_argument('configFile', nargs='?', default=DEFAULT_CFG,
                    help='레이더 cfg (기본 chirp_configs/ISK_incabin_tightgate.cfg)')
    ap.add_argument('--gt', default=None,
                    help='실제 착석 좌석(기록용 메타, 예: Rear-L). 빈 차 측정이면 생략')
    ap.add_argument('--log-frames', nargs='?', const='auto', default=None,
                    help='프레임 단위 CSV 기록(경로 생략 시 frames_<cfg>_<시각>.csv). '
                         '실행 중 s=입장, e=퇴장, m=동작 시작 마커')
    ap.add_argument('--duration', type=float, default=None,
                    help='이 시간(초)이 지나면 자동 종료. 측정 중 노트북을 만질 필요가 없게 함')
    ap.add_argument('--no-config', action='store_true',
                    help='cfg 재전송 생략(센서가 이미 스트리밍 중일 때)')
    ap.add_argument('--no-plot', action='store_true', help='3D 창 끄기(콘솔만)')
    ap.add_argument('--debug', action='store_true',
                    help='트랙 id/좌표·존별 점 수·트랙 지속시간 출력(유령 추적용)')
    args = ap.parse_args()

    # clearance_gtrack.ZoneClearance 는 매 update 때 이 모듈 값을 읽는다. 파일은 수정하지 않고
    # 이 프로세스 안에서만 바꾼다(analyze_frames 재생도 같은 방식).
    cg.EMPTY_CONFIRM_SEC = EMPTY_CONFIRM_SEC

    seats = cg.SEAT_SETS[ZONE_SET]
    print('[presence-clr] 좌석 존 세트: {}'.format(ZONE_SET))

    serialUser = serialhelper.SerialHelper(args.userPort, 115200)
    serialData = serialhelper.SerialHelper(args.dataPort, 921600)
    if args.no_config:
        print('[presence-clr] --no-config: config 재전송 생략')
    else:
        with open(args.configFile, 'r', encoding='utf-8', errors='ignore') as f:
            serialUser.sendConfig(f.readlines())

    plot = None
    if not args.no_plot:
        try:
            from plot_presence_3d import PresencePlot3D
            plot = PresencePlot3D(seats)
        except Exception as e:
            print('[presence-clr] presence 3D 창 실패(콘솔로 계속):', e)

    parser = FrameParser2()
    monitor = PresenceClearanceMonitor(seats, presence_confirm=PRESENCE_CONFIRM, freeze_sec=FREEZE_SEC)
    publisher = ClearancePublisher()
    print('[presence-clr] 판정 설정: K={} (창 {}프레임), 얼어붙은 트랙 F={:g}s, EMPTY 확정 E={:g}s'.format(
        monitor.presence_confirm, monitor.presence_window, monitor.freeze_sec, cg.EMPTY_CONFIRM_SEC))

    flog = None
    if args.log_frames:
        import datetime
        import adaptive_clearance as ac
        import presence_monitor as pmod
        stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        path = args.log_frames
        if path == 'auto':
            path = 'frames_{}_{}.csv'.format(
                os.path.splitext(os.path.basename(args.configFile))[0], stamp)
        meta = {
            'started': stamp,
            'cfg': args.configFile,
            'zones': ZONE_SET,
            'zone_names': [z.name for z in seats],
            'gt': args.gt,
            'min_points': monitor.min_points,
            'presence_confirm': monitor.presence_confirm,
            'presence_window_mult': pmod.PRESENCE_WINDOW_MULT,
            'adaptive': monitor.adaptive,
            'weak_release': ac.EMPTY_CONFIRM_WEAK,           # adaptive 미사용. 분석기 재생 인자 형식 유지용
            'weak_hold': ac.OCC_HOLD_WEAK,
            'strong_track_sec': ac.STRONG_TRACK_SEC,
            'strong_sustain_sec': ac.STRONG_SUSTAIN_SEC,
            'occ_hold_sec': cg.OCC_HOLD_SEC,
            'empty_confirm_sec': cg.EMPTY_CONFIRM_SEC,
            'freeze_sec': monitor.freeze_sec,
            'warmup_block_sec': cg.WARMUP_BLOCK_SEC,
            'fold_zone_prefix': pmod.FOLD_ZONE_PREFIX,
        }
        flog = FrameLogger(path, [z.name for z in seats], meta)
        print('[presence-clr] 프레임 로그: {}  (s=입장, e=퇴장, m=동작 시작)'.format(os.path.abspath(path)))

    startT = time.time()
    lastPrint = 0.0
    lastTracks, lastPoints = [], []
    tlv_reported = False
    seen = {}          # track id -> [최초 등장 t, 최근 t, 최초 (x,y)]
    prev_ids = set()
    srchist = {}       # zone -> {source: 프레임수} (근거별 기여도 집계)

    # Ctrl+C 를 예외 대신 '종료 요청 플래그'로 받는다.
    # 기본 동작(KeyboardInterrupt)은 대부분 3D 창 그리기(paintGL) 도중에 발생하는데,
    # pyqtgraph 가 그리기 중 예외를 모두 잡아 출력만 하고 계속 진행해 Ctrl+C 가 삼켜졌다.
    stop = {'req': False, 'why': ''}

    def _on_sigint(signum, frame):
        if stop['req']:                       # 이미 요청됐는데 또 누르면 예외로도 시도(루프가 멈춰 있을 때 대비)
            raise KeyboardInterrupt
        stop['req'], stop['why'] = True, 'Ctrl+C'
    signal.signal(signal.SIGINT, _on_sigint)

    print('[presence-clr] 시작. 종료: q 키 / Ctrl+C / 3D 창 닫기{}'.format(
        ' / {:.0f}초 후 자동'.format(args.duration) if args.duration else ''))
    try:
        while not stop['req']:
            data = serialData.readall()
            dbg = serialUser.readall()
            if dbg:
                sys.stdout.write(str(dbg, 'utf-8', 'replace'))
            frames = parser.feed(data) if data else []
            fnums = list(parser.frame_nums) if data else []
            now = time.time() - startT

            # 입장/퇴장 마커 (터미널 또는 3D 창 키)
            keys = []
            if msvcrt is not None:
                while msvcrt.kbhit():
                    keys.append(msvcrt.getwch().lower())
            if plot is not None and getattr(plot, 'keys', None):
                keys += plot.keys
                plot.keys = []
            if args.duration and now >= args.duration:
                stop['req'], stop['why'] = True, '{:.0f}초 경과'.format(args.duration)
            if plot is not None and not plot.win.isVisible():
                stop['req'], stop['why'] = True, '3D 창 닫힘'
            for ch in keys:
                if ch == QUIT_KEY:
                    stop['req'], stop['why'] = True, 'q 키'
                if ch in MARK_KEYS:
                    print('[mark] {} @ t={:.2f}s'.format(MARK_KEYS[ch], now))
                    if flog is not None:
                        flog.mark(MARK_KEYS[ch])

            results = None
            for fi, (tracks, points) in enumerate(frames):
                lastTracks, lastPoints = tracks, points
                results = monitor.update(tracks, points, now)
                if flog is not None:
                    flog.log_frame(now, fnums[fi] if fi < len(fnums) else None,
                                   tracks, points, results, monitor.fold_permitted(now))
                for r in results:
                    d = srchist.setdefault(r['zone'], {})
                    k = r.get('source', '?')
                    d[k] = d.get(k, 0) + 1
                publisher.publish(results, monitor.fold_permitted(now))
                if plot is not None:
                    plot.update(tracks, points, results, monitor.fold_permitted(now), now)
            if results is None:
                # 새 프레임 없음: 직전 근거로 시간만 진행 (presence 창에는 추가 안 함)
                results = monitor.update(lastTracks, lastPoints, now, new_frame=False)
                if flog is not None:
                    flog.note_stale(now)
                publisher.publish(results, monitor.fold_permitted(now))
                if plot is not None:
                    plot.update(lastTracks, lastPoints, results, monitor.fold_permitted(now), now)

            # 첫 프레임 수신 후 TLV 종류 1회 보고 (1020 존재 확인)
            if not tlv_reported and parser.tlv_seen:
                print('[presence-clr] 수신 TLV 타입: {} {}'.format(
                    sorted(parser.tlv_seen),
                    '(1020 포인트클라우드 OK)' if 1020 in parser.tlv_seen
                    else '(경고: 1020 없음 → presence 폴백 불가, cfg 확인)'))
                tlv_reported = True

            if now - lastPrint >= PRINT_PERIOD:
                lastPrint = now
                foldOk = monitor.fold_permitted(now)
                banner = 'FOLD PERMITTED' if foldOk else 'FOLD BLOCKED'
                zs = ' | '.join('{}:{}/{}'.format(r['zone'][:6], r['state'][:3], r.get('source', '?')[:4])
                                for r in results)
                print('[clr] t={:5.1f}s  {}  {}'.format(now, banner, zs))
                print('      tracks={} points={}'.format(len(lastTracks), len(lastPoints)))
                if args.debug:
                    cur = set()
                    for t in lastTracks:
                        cur.add(t.id)
                        if t.id not in seen:
                            seen[t.id] = [now, now, (t.x, t.y)]
                            print('      [NEW ] #{} born t={:.1f}s at ({:.2f},{:.2f})'.format(
                                t.id, now, t.x, t.y))
                        seen[t.id][1] = now
                        b = seen[t.id]
                        print('      [trk ] #{} age={:5.1f}s pos=({:+.2f},{:+.2f}) '
                              'drift=({:+.2f},{:+.2f}) z={:+.2f}'.format(
                                  t.id, now - b[0], t.x, t.y,
                                  t.x - b[2][0], t.y - b[2][1], t.z))
                    for gone in prev_ids - cur:
                        if gone in seen:
                            print('      [DIED] #{} lived {:.1f}s'.format(
                                gone, seen[gone][1] - seen[gone][0]))
                    prev_ids = cur
                    zn = ' '.join('{}:{}(s{})'.format(
                        r['zone'][:6], r.get('npts', 0), r.get('pres_score', 0))
                        for r in results)
                    print('      [pts ] 존별 n(디바운스점수) = {}'.format(zn))
                    fz = ' '.join('{}:{}'.format(r['zone'][:6], r['frozen_excluded'])
                                  for r in results if r.get('frozen_excluded'))
                    if fz:
                        print('      [frz ] 좌표가 F초 넘게 멈춰 근거에서 뺀 트랙 수 = {}'.format(fz))
                    # SNR 은 점이 있는 존만 (다중경로 유령 vs 직접 반사 판별 근거)
                    sn = ' '.join('{}:med{:.0f}/max{:.0f}'.format(
                        r['zone'][:6], r.get('snr_med', 0), r.get('snr_max', 0))
                        for r in results if r.get('npts', 0) > 0)
                    if sn:
                        print('      [snr ] {}'.format(sn))
        print('\n[presence-clr] 종료 요청: {}'.format(stop['why']))
    except KeyboardInterrupt:
        print('\n' + '[presence-clr] 종료.')
    finally:
        if flog is not None:
            flog.close()
            print('[presence-clr] 프레임 로그 {}행 저장: {}'.format(flog.rows, os.path.abspath(flog.path)))
        try:
            print('')
            print(format_summary(srchist))
        except Exception as e:
            print('[presence-clr] 집계 출력 실패:', e)


if __name__ == '__main__':
    main()
