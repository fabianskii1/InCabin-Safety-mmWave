# gtrack_clearance_demo / eval_score.py
# ---------------------------------------------------------------------------
# eval_logger.py 가 남긴 CSV(들)를 읽어 '폴딩 안전 중심' 트래킹 성능 지표를 계산.
# 기존 프로젝트 파일 무수정. 외부 의존성 없음(그래프 저장 시에만 matplotlib 선택).
#
# 사용법:
#   py eval_score.py eval_log.csv
#   py eval_score.py eval_log.csv --plots out_dir   # 포스터용 PNG도 저장(matplotlib 필요)
#
# 지표 계층:
#   ⓵ 안전 핵심 : 위험 오허용(점유 중 fold_permit), EMPTY 오판(점유석을 빈 걸로),
#                 점유 검출 재현율(Recall), 정지 착석 검출 유지율
#   ⓶ 신뢰/사용성: 좌석 혼동행렬(Precision/Recall/F1), 오차단률, 반응지연(마커)
#   ⓷ 트래킹 품질: 위치오차 RMSE(X/Y), 고스트 트랙률, 과분할 ID 지표
#
# 주의: warmup 구간(t_rel < warmup_sec)은 시스템이 의도적으로 차단하므로 제외.
#       UNKNOWN 은 '차단(안전)'이라 위험 오판이 아니며, EMPTY 만 위험 오판으로 집계.
# ---------------------------------------------------------------------------
import os
import sys
import csv
import json
import math
import argparse
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import clearance_gtrack as cg   # 존 좌표(중심) 참조용


def zone_centers(zones_set):
    out = {}
    for z in cg.SEAT_SETS.get(zones_set, cg.DEFAULT_SEATS):
        out[z.name] = ((z.xmin + z.xmax) / 2.0, (z.ymin + z.ymax) / 2.0)
    return out


def contiguous_runs(flags):
    """[bool,...] -> 연속 True 구간 개수와 최장 길이(프레임)."""
    events, longest, cur = 0, 0, 0
    for f in flags:
        if f:
            if cur == 0:
                events += 1
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
    return events, longest


def load_rows(paths):
    rows = []
    header_state_cols = None
    for p in paths:
        with open(p, 'r', newline='', encoding='utf-8') as f:
            rd = csv.DictReader(f)
            scols = [c for c in rd.fieldnames if c.endswith('_state')]
            if header_state_cols is None:
                header_state_cols = scols
            for r in rd:
                rows.append(r)
    return rows, (header_state_cols or [])


def group_trials(rows):
    """세션 단위 = 한 트라이얼. key -> 정렬된 row 리스트."""
    g = defaultdict(list)
    for r in rows:
        g[(r['session'], r['scenario'], r['condition'], r['trial'])].append(r)
    for k in g:
        g[k].sort(key=lambda r: float(r['t_rel']))
    return g


def gt_set(row):
    return set(t for t in row['gt_occupied'].split('|') if t)


def steady_rows(trial_rows):
    """warmup 이후 행만."""
    out = []
    for r in trial_rows:
        try:
            if float(r['t_rel']) >= float(r['warmup_sec']):
                out.append(r)
        except ValueError:
            pass
    return out


def main():
    ap = argparse.ArgumentParser(description='트래킹 성능 채점(폴딩 안전 중심)')
    ap.add_argument('csv', nargs='+', help='eval_logger.py 가 만든 CSV(들)')
    ap.add_argument('--plots', metavar='DIR', default=None, help='PNG 저장 폴더(matplotlib 필요)')
    args = ap.parse_args()

    rows, state_cols = load_rows(args.csv)
    if not rows:
        raise SystemExit('행이 없음.')
    zones = [c[:-6] for c in state_cols]   # 'X_state' -> 'X'
    trials = group_trials(rows)

    # ---- 누적 집계용 ----
    # 좌석별 혼동행렬(OCCUPIED=positive, GT=해당 좌석 점유), warmup 제외
    tp = defaultdict(int); fp = defaultdict(int); fn = defaultdict(int); tn = defaultdict(int)
    # 안전: 위험 오허용/ EMPTY 오판
    danger_permit_frames = 0; danger_permit_events = 0; danger_permit_longest = 0
    occ_gt_frames = 0                       # GT가 '점유 좌석 있음'인 steady 프레임 수
    empty_miss = defaultdict(int)           # 좌석별 EMPTY 오판 프레임(점유인데 EMPTY)
    empty_miss_events = defaultdict(int)
    # 정지 유지율
    static_latched_frames = defaultdict(int); static_occ_frames = defaultdict(int)
    # 오차단(전 좌석 공석 steady 에서 fold_permit=0)
    empty_all_frames = 0; false_block_frames = 0
    # RMSE
    err_x = []; err_y = []
    # 고스트/ID
    ghost_over_frames = 0; ghost_total_frames = 0; extra_sum = 0
    idsw_report = []
    # 반응지연(마커)
    sit_latencies = []; stand_latencies = []

    per_trial_lines = []

    for key, trows in sorted(trials.items()):
        session, scenario, cond, trial = key
        gt = gt_set(trows[0])
        centers = zone_centers(trows[0]['zones_set'])
        srows = steady_rows(trows)

        # 재실 구간: sit/stand 마커가 있으면 그 사이에만 GT 점유가 유효(입장/퇴장 반영).
        # 마커 없으면 전 구간 착석으로 간주.
        sit_t = None; stand_t = None
        for r in trows:
            if r['marker'] == 'sit':
                sit_t = float(r['t_rel'])
            elif r['marker'] == 'stand':
                stand_t = float(r['t_rel'])
        present_start = sit_t if sit_t is not None else float('-inf')
        present_end = stand_t if stand_t is not None else float('inf')

        def eff_gt(r):
            return gt if (present_start <= float(r['t_rel']) <= present_end) else set()

        # --- 혼동행렬 & EMPTY 오판 (좌석별, per-row 유효 GT) ---
        for z in zones:
            empty_flags = []
            for r in srows:
                gtpos = z in eff_gt(r)
                pred_occ = (r['{}_state'.format(z)] == 'OCCUPIED')
                if gtpos and pred_occ: tp[z] += 1
                elif gtpos and not pred_occ: fn[z] += 1
                elif (not gtpos) and pred_occ: fp[z] += 1
                else: tn[z] += 1
                if gtpos:
                    empty_flags.append(r['{}_state'.format(z)] == 'EMPTY')  # 점유석을 빈걸로=위험
            if empty_flags and any(empty_flags):
                ev, _ = contiguous_runs(empty_flags)
                empty_miss[z] += sum(empty_flags)
                empty_miss_events[z] += ev

        # --- 위험 오허용 / 오차단 (per-row 유효 GT) ---
        permit_flags = []   # 재실중 fold_permit(위험) 프레임 플래그
        for r in srows:
            egt = eff_gt(r)
            permit = int(r['fold_permit']) == 1
            if egt:
                occ_gt_frames += 1
                permit_flags.append(permit)   # 재실 중 허용 = 위험
            else:
                empty_all_frames += 1
                if not permit:
                    false_block_frames += 1    # 실제 공석인데 차단 = 오차단
        if permit_flags:
            danger_permit_frames += sum(permit_flags)
            ev, longest = contiguous_runs(permit_flags)
            danger_permit_events += ev
            danger_permit_longest = max(danger_permit_longest, longest)

        # --- 정지 유지율(cond=static, 재실 중 GT 좌석 래치 후) ---
        if cond == 'static' and gt:
            for z in gt:
                latched = False
                for r in srows:
                    if z not in eff_gt(r):
                        continue
                    st = r['{}_state'.format(z)]
                    if st == 'OCCUPIED':
                        latched = True
                    if latched:
                        static_latched_frames[z] += 1
                        if st == 'OCCUPIED':
                            static_occ_frames[z] += 1

        # --- RMSE(위치오차): 재실 중 GT 좌석 중심 대비 ---
        for r in srows:
            try:
                trks = json.loads(r['tracks_json'])
            except Exception:
                trks = []
            for z in eff_gt(r):
                cx, cy = centers.get(z, (None, None))
                if cx is None:
                    continue
                zdef = next((zz for zz in cg.SEAT_SETS[r['zones_set']] if zz.name == z), None)
                cand = []
                for (_id, x, y) in trks:
                    if zdef and (zdef.xmin <= x <= zdef.xmax and zdef.ymin <= y <= zdef.ymax):
                        cand.append((x, y))
                if cand:
                    x, y = min(cand, key=lambda p: (p[0]-cx)**2 + (p[1]-cy)**2)
                    err_x.append(x - cx); err_y.append(y - cy)

        # --- 고스트/ID 과분할 (재실 중 기대 인원 대비) ---
        ids_all = set()
        max_expected = 0
        for r in srows:
            try:
                trks = json.loads(r['tracks_json'])
            except Exception:
                trks = []
            expected = len(eff_gt(r))
            max_expected = max(max_expected, expected)
            ghost_total_frames += 1
            if len(trks) > expected:
                ghost_over_frames += 1
                extra_sum += (len(trks) - expected)
            if expected > 0:
                for (i, _x, _y) in trks:
                    ids_all.add(i)
        if max_expected > 0:
            idsw_report.append((scenario, cond, trial, max_expected, len(ids_all)))
        if sit_t is not None and gt:
            z = sorted(gt)[0]
            for r in trows:
                if float(r['t_rel']) >= sit_t and r['{}_state'.format(z)] == 'OCCUPIED':
                    sit_latencies.append(float(r['t_rel']) - sit_t)
                    break
        if stand_t is not None and gt:
            z = sorted(gt)[0]
            for r in trows:
                if float(r['t_rel']) >= stand_t and r['{}_state'.format(z)] == 'EMPTY':
                    stand_latencies.append(float(r['t_rel']) - stand_t)
                    break

        per_trial_lines.append('  {:16s} {:6s} #{:<2s} gt=[{:12s}] frames={}'.format(
            scenario, cond, trial, '|'.join(sorted(gt)) or '공석', len(trows)))

    # ===================== 리포트 =====================
    def rate(a, b):
        return (a / b) if b else float('nan')

    def pct(x):
        return '  n/a' if (x != x) else '{:5.1f}%'.format(100 * x)

    W = 64
    print('=' * W)
    print(' 트래킹 성능 평가  (폴딩 끼임 방지 시스템)')
    print('=' * W)
    print(' 트라이얼 {}개 · steady 프레임 기준(warmup 제외)'.format(len(trials)))
    for ln in per_trial_lines:
        print(ln)

    print('\n' + '─' * W)
    print(' ⓵ 안전 핵심 (fail-safe)')
    print('─' * W)
    print('  위험 오허용(점유 중 FOLD 허용)  : {} 프레임 / {} 점유프레임 = {}'.format(
        danger_permit_frames, occ_gt_frames, pct(rate(danger_permit_frames, occ_gt_frames))))
    print('     └ 연속 이벤트 {}건, 최장 {} 프레임  [목표 0]'.format(
        danger_permit_events, danger_permit_longest))
    tot_empty_miss = sum(empty_miss.values())
    print('  EMPTY 오판(점유석을 빈 걸로)     : {} 프레임, 이벤트 {}건  [목표 0]'.format(
        tot_empty_miss, sum(empty_miss_events.values())))
    for z in zones:
        if empty_miss[z]:
            print('     └ {}: {} 프레임 / {} 이벤트'.format(z, empty_miss[z], empty_miss_events[z]))
    # 점유 검출 재현율(전체 좌석 종합)
    TP = sum(tp.values()); FN = sum(fn.values())
    print('  점유 검출 재현율 Recall(종합)    : {}  (TP={} FN={})'.format(
        pct(rate(TP, TP + FN)), TP, FN))
    # 정지 유지율
    sl = sum(static_latched_frames.values()); so = sum(static_occ_frames.values())
    print('  정지 착석 검출 유지율(래치 후)   : {}  ({}/{} 프레임)'.format(
        pct(rate(so, sl)), so, sl))

    print('\n' + '─' * W)
    print(' ⓶ 신뢰·사용성')
    print('─' * W)
    print('  좌석별 혼동행렬 (OCCUPIED=positive)')
    print('    {:10s} {:>5s} {:>5s} {:>5s} {:>5s} | {:>6s} {:>6s} {:>6s}'.format(
        'zone', 'TP', 'FP', 'FN', 'TN', 'Prec', 'Rec', 'F1'))
    for z in zones:
        P = rate(tp[z], tp[z] + fp[z]); R = rate(tp[z], tp[z] + fn[z])
        F1 = rate(2 * P * R, P + R) if (P == P and R == R and (P + R) > 0) else float('nan')
        print('    {:10s} {:5d} {:5d} {:5d} {:5d} | {:>6s} {:>6s} {:>6s}'.format(
            z, tp[z], fp[z], fn[z], tn[z], pct(P).strip(), pct(R).strip(), pct(F1).strip()))
    print('  오차단률(공석인데 FOLD 차단)     : {}  ({}/{} 프레임)'.format(
        pct(rate(false_block_frames, empty_all_frames)), false_block_frames, empty_all_frames))
    if sit_latencies:
        print('  착석→OCCUPIED 지연 : 평균 {:.2f}s / 최대 {:.2f}s (n={})'.format(
            sum(sit_latencies) / len(sit_latencies), max(sit_latencies), len(sit_latencies)))
    if stand_latencies:
        print('  하차→EMPTY 지연    : 평균 {:.2f}s / 최대 {:.2f}s (n={})'.format(
            sum(stand_latencies) / len(stand_latencies), max(stand_latencies), len(stand_latencies)))
    if not sit_latencies and not stand_latencies:
        print('  (반응지연: 마커(s/e) 기록이 없어 생략)')

    print('\n' + '─' * W)
    print(' ⓷ 트래킹 품질 근거')
    print('─' * W)
    if err_x:
        def rmse(v): return math.sqrt(sum(e * e for e in v) / len(v))
        def mae(v): return sum(abs(e) for e in v) / len(v)
        print('  위치오차(측방 X) : MAE {:.3f}m  RMSE {:.3f}m'.format(mae(err_x), rmse(err_x)))
        print('  위치오차(전방 Y) : MAE {:.3f}m  RMSE {:.3f}m  (n={} 샘플)'.format(
            mae(err_y), rmse(err_y), len(err_x)))
    else:
        print('  (위치오차: GT 점유 좌석 내 트랙 샘플 없음)')
    print('  고스트 트랙률(예상 초과 프레임) : {}  (평균 초과 {:.2f}개)'.format(
        pct(rate(ghost_over_frames, ghost_total_frames)),
        rate(extra_sum, ghost_total_frames) if ghost_total_frames else 0.0))
    if idsw_report:
        print('  과분할 ID(고유 ID수 vs 예상 인원):')
        for (sc, cd, tr, exp, seen) in idsw_report:
            flag = '  ⚠ 과분할' if seen > exp else ''
            print('     {:16s} {:6s} #{}: 예상 {} / 관측 {}{}'.format(sc, cd, tr, exp, seen, flag))
    print('=' * W)

    if args.plots:
        try:
            _make_plots(args.plots, zones, tp, fp, fn, tn, rate,
                        danger_permit_frames, occ_gt_frames, sl, so)
        except Exception as e:
            print('[plots] 실패(matplotlib 필요?):', e)


def _make_plots(out_dir, zones, tp, fp, fn, tn, rate, danger, occ_gt, sl, so):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    os.makedirs(out_dir, exist_ok=True)

    # 좌석별 Precision/Recall/F1 막대
    P = [rate(tp[z], tp[z] + fp[z]) or 0 for z in zones]
    R = [rate(tp[z], tp[z] + fn[z]) or 0 for z in zones]
    F = [(2 * p * r / (p + r)) if (p + r) else 0 for p, r in zip(P, R)]
    import numpy as np
    x = np.arange(len(zones)); w = 0.25
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(x - w, P, w, label='Precision')
    ax.bar(x, R, w, label='Recall')
    ax.bar(x + w, F, w, label='F1')
    ax.set_xticks(x); ax.set_xticklabels(zones, rotation=15)
    ax.set_ylim(0, 1.05); ax.set_ylabel('score'); ax.legend()
    ax.set_title('Seat occupancy classification')
    fig.tight_layout(); fig.savefig(os.path.join(out_dir, 'seat_prf.png'), dpi=140)

    # 안전 요약
    fig2, ax2 = plt.subplots(figsize=(5, 3.2))
    vals = [danger, rate(so, sl) * 100 if sl else 0]
    ax2.bar(['위험 오허용(frames)', '정지 유지율(%)'], vals, color=['#d43', '#2a8'])
    ax2.set_title('Fail-safe summary')
    fig2.tight_layout(); fig2.savefig(os.path.join(out_dir, 'safety.png'), dpi=140)
    print('[plots] 저장: {}/seat_prf.png, safety.png'.format(out_dir))


if __name__ == '__main__':
    main()
