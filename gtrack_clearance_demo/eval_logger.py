# gtrack_clearance_demo / eval_logger.py
# ---------------------------------------------------------------------------
# 트래킹 성능 측정용 로거. 기존 데모 파이프라인(track_parser + clearance_gtrack)을
# 그대로 재사용해 '프레임별 상태'를 CSV로 남긴다. 기존 프로젝트 파일 무수정.
#
# 한 번 실행 = 한 트라이얼. 시나리오/그라운드트루스(실제 점유 좌석)/조건(정지·동작)을
# 인자로 붙여 같은 CSV에 append -> eval_score.py 가 이 라벨과 대조해 지표 계산.
#
# 사용법 (센서 = 3D People Tracking 펌웨어):
#   # 공석 60초
#   py eval_logger.py COM10 COM11 <cfg> --scenario empty     --gt "" --cond static  --trial 1
#   # 뒷좌-우 단독, 가만히 앉음 60초
#   py eval_logger.py COM10 COM11 <cfg> --scenario rearR_solo --gt "Rear-R" --cond static  --trial 1 --no-config
#   # 뒷좌 만석, 자연 착석
#   py eval_logger.py COM10 COM11 <cfg> --scenario rear_full  --gt "Rear-L,Rear-C,Rear-R" --cond motion --trial 1 --no-config
#
# 마커(선택, Windows): 로깅 중 키보드로 착석/하차 순간 표시 -> 반응지연 측정용
#   s = 착석(sit),  e = 하차(stand),  q = 종료
#
# 옵션: --no-config(이미 스트리밍 중), --duration 초(자동종료, 기본 60), --out CSV경로
# ---------------------------------------------------------------------------
import os
import sys
import csv
import json
import time
import argparse

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

try:
    import msvcrt  # Windows 비차단 키 입력(마커용). 없으면 마커 비활성.
except Exception:
    msvcrt = None

_GUI = os.path.join(os.path.dirname(__file__), '..', 'computer_GUI')
sys.path.insert(0, os.path.abspath(_GUI))
import serialhelper                    # noqa: E402  (read-only 재사용)

from track_parser import FrameParser   # noqa: E402
import clearance_gtrack as cg          # noqa: E402


def parse_gt(gt_str, zone_names):
    """--gt "Rear-L,Rear-R" -> {'Rear-L','Rear-R'} (좌석 이름 검증)."""
    gt = set()
    for tok in (gt_str or '').replace('|', ',').split(','):
        tok = tok.strip()
        if not tok:
            continue
        if tok not in zone_names:
            raise SystemExit('[eval-logger] 알 수 없는 좌석 이름: {} (가능: {})'
                             .format(tok, ', '.join(zone_names)))
        gt.add(tok)
    return gt


def main():
    ap = argparse.ArgumentParser(description='트래킹 성능 측정 로거(프레임별 CSV)')
    ap.add_argument('userPort')
    ap.add_argument('dataPort')
    ap.add_argument('configFile', help='3D People Tracking cfg')
    ap.add_argument('--no-config', action='store_true')
    ap.add_argument('--zones', choices=list(cg.SEAT_SETS.keys()), default='vehicle')
    ap.add_argument('--scenario', required=True, help='시나리오 이름(예: rearR_solo)')
    ap.add_argument('--gt', default='', help='실제 점유 좌석(쉼표구분, 공석이면 빈값)')
    ap.add_argument('--cond', choices=['static', 'motion'], default='motion',
                    help='정지(static) / 자연동작(motion)')
    ap.add_argument('--trial', type=int, default=1)
    ap.add_argument('--duration', type=float, default=60.0, help='자동 종료 초(0=무제한)')
    ap.add_argument('--out', default='eval_log.csv', help='append 대상 CSV')
    args = ap.parse_args()

    seats = cg.SEAT_SETS[args.zones]
    zone_names = [z.name for z in seats]
    gt = parse_gt(args.gt, zone_names)

    serialUser = serialhelper.SerialHelper(args.userPort, 115200)
    serialData = serialhelper.SerialHelper(args.dataPort, 921600)
    if args.no_config:
        print('[eval-logger] --no-config: config 재전송 생략')
    else:
        with open(args.configFile, 'r', encoding='utf-8', errors='ignore') as f:
            serialUser.sendConfig(f.readlines())

    parser = FrameParser()
    monitor = cg.ClearanceMonitor(seats)

    out_path = os.path.abspath(args.out)
    new_file = not os.path.exists(out_path) or os.path.getsize(out_path) == 0
    fcsv = open(out_path, 'a', newline='', encoding='utf-8')
    writer = csv.writer(fcsv)
    state_cols = ['{}_state'.format(n) for n in zone_names]
    if new_file:
        writer.writerow(
            ['session', 'scenario', 'condition', 'trial', 'gt_occupied', 'zones_set',
             'warmup_sec', 'frame', 't_rel', 'n_tracks', 'tracks_json', 'marker',
             'fold_permit'] + state_cols)

    session = time.strftime('%Y%m%d_%H%M%S')
    gt_field = '|'.join(sorted(gt))
    meta = [session, args.scenario, args.cond, args.trial, gt_field, args.zones,
            cg.WARMUP_BLOCK_SEC]

    startT = time.time()
    lastPrint = 0.0
    lastIdleLog = 0.0
    frameIdx = 0
    lastTracks = []

    def write_row(now, tracks, results, foldOk, marker=''):
        nonlocal frameIdx
        frameIdx += 1
        st = {r['zone']: r['state'] for r in results}
        tj = json.dumps([[t.id, round(t.x, 3), round(t.y, 3)] for t in tracks],
                        separators=(',', ':'))
        writer.writerow(meta + [frameIdx, round(now, 3), len(tracks), tj, marker,
                                int(bool(foldOk))] + [st.get(n, '') for n in zone_names])

    print('[eval-logger] 시작: scenario={} cond={} gt=[{}] trial={} -> {}'
          .format(args.scenario, args.cond, gt_field or '공석', args.trial, out_path))
    print('[eval-logger] 마커: s=착석 e=하차 q=종료 (Windows 키입력)'
          if msvcrt else '[eval-logger] (마커 비활성: msvcrt 없음)')
    print('[eval-logger] Ctrl+C 또는 q 로 종료.')

    try:
        while True:
            marker = ''
            if msvcrt and msvcrt.kbhit():
                ch = msvcrt.getwch().lower()
                if ch == 'q':
                    break
                elif ch == 's':
                    marker = 'sit'
                elif ch == 'e':
                    marker = 'stand'
                if marker:
                    print('[eval-logger] MARK {} @ t={:.1f}s'.format(marker, time.time() - startT))

            data = serialData.readall()
            dbg = serialUser.readall()
            if dbg:
                sys.stdout.write(str(dbg, 'utf-8', 'replace'))
            frames = parser.feed(data) if data else []
            now = time.time() - startT

            if frames:
                for tracks in frames:
                    lastTracks = tracks
                    now = time.time() - startT
                    results = monitor.update(tracks, now)
                    foldOk = monitor.fold_permitted(now)
                    write_row(now, tracks, results, foldOk, marker)
                    marker = ''   # 마커는 한 행에만
            else:
                # 프레임이 없어도 시간 진행(EMPTY 확정) + 타임라인 유지, 10Hz 스로틀
                if now - lastIdleLog >= 0.1:
                    lastIdleLog = now
                    results = monitor.update(lastTracks, now)
                    foldOk = monitor.fold_permitted(now)
                    write_row(now, lastTracks, results, foldOk, marker)

            if now - lastPrint >= 1.0:
                lastPrint = now
                foldOk = monitor.fold_permitted(now)
                banner = 'FOLD_OK' if foldOk else 'BLOCK'
                zs = ' '.join('{}:{}'.format(r['zone'], r['state'][:3])
                              for r in monitor.update(lastTracks, now))
                print('[log] t={:5.1f}s {:7s} {}  (rows={})'.format(now, banner, zs, frameIdx))

            if args.duration and now >= args.duration:
                print('[eval-logger] duration {}s 도달 -> 종료'.format(args.duration))
                break
    except KeyboardInterrupt:
        print('\n[eval-logger] 중단.')
    finally:
        fcsv.flush()
        fcsv.close()
        print('[eval-logger] 저장 완료: {} (총 {} 행 기록)'.format(out_path, frameIdx))


if __name__ == '__main__':
    main()
