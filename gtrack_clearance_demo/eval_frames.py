# gtrack_clearance_demo / eval_frames.py
# ---------------------------------------------------------------------------
# 현재 코드(presence 폴백 + K·F·E)용 성능 평가기.
#
#   [1] eval_score 호환 리포트 : eval_score.py 와 같은 정의·같은 출력 형식.
#       9/9 베이스라인(eval_logger CSV)과 현재 코드의 프레임 로그를 같은 잣대로 채점한다.
#       (셀프테스트: 베이스라인 CSV 에서 eval_score 출력과 글자 하나까지 같아야 함)
#   [2] 구간별 분리 : 위험 오허용·EMPTY 오판·Recall 을 '입장(s ~ 첫 점유)'과 '착석(첫 점유 ~ e)'으로,
#       오차단을 '입장 전 / 퇴장 후 / 빈 차 트라이얼'로 나눈다. eval_score 는 s 부터를 점유로 보므로
#       s 를 문 열기 직전에 누르는 지금 프로토콜에서는 입장 중 폴딩 허용이 위험 오허용에 포함된다.
#   [3] 오차단 원인 (프레임 로그만) : 막고 있던 뒷좌 존의 근거가 트랙 / 점 / 대기(근거 없음) 중 무엇인지.
#
# 입력: frame_logger CSV(현재 코드) 와 eval_logger CSV(베이스라인) 를 섞어 넣을 수 있다.
#   프레임 로그의 시나리오·조건·회차:
#     시나리오 = 메타 gt 로 정함 (없음 -> empty, Rear-L -> rearL_solo, ...)
#     조건     = 메타 cond > 파일명 토큰(static/motion) > 기존 세션 접두어(S·C·D=static, A·B=motion) > --cond
#     회차     = 메타 trial > 파일 이름
#   마커가 두 번 이상 찍힌 트라이얼은 eval_score 규칙대로 '마지막' s/e 를 쓰고, [0] 에 경고를 낸다
#   (analyze_frames 는 첫 s 를 쓴다).
#   --ignore-markers : 마커를 모두 무시하고 트라이얼 전체를 GT 점유로 채점한다. 9/9 베이스라인은
#   시작 후 걸어 들어왔지만 마커가 기록되지 않아 60초 전체가 점유로 채점됐으므로, 같은 절차(입장 +
#   s 마커)로 잰 R세트를 베이스라인과 같은 정의로 비교할 때 쓴다. 마커를 쓰는 채점은 옵션 없이 따로 실행.
#
# 사용:
#   py eval_frames.py ../eval_baseline/eval_log_baseline.csv            # 베이스라인
#   py eval_frames.py "V*.csv"                                          # 현재 코드 실측(라이브 판정)
#   py eval_frames.py "A*.csv" "S*.csv" --replay 2,3,3                  # 기존 로그를 K=2,F=3,E=3 으로 재생해 채점
#   py eval_frames.py "R_*.csv" --ignore-markers                        # 베이스라인과 같은 정의(전체 점유)
#   py eval_frames.py ... --plots out_dir                               # eval_score 와 같은 그래프
#   py eval_frames.py --selftest
# ---------------------------------------------------------------------------
import argparse
import csv
import glob
import io
import json
import math
import os
import re
import sys
from collections import defaultdict, deque

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import clearance_gtrack as cg
import eval_score as es          # 작은 도우미·그래프는 그대로 재사용(정의 일치)
import analyze_frames as af

STATE_FULL = {'E': 'EMPTY', 'O': 'OCCUPIED', 'U': 'UNKNOWN'}
GT_SCENARIO = {None: 'empty', 'Passenger': 'passenger_solo', 'Rear-L': 'rearL_solo',
               'Rear-C': 'rearC_solo', 'Rear-R': 'rearR_solo'}
PREFIX_COND = {'S': 'static', 'C': 'static', 'D': 'static', 'A': 'motion', 'B': 'motion'}


# ===========================================================================
# 입력 -> 공통 행 형식 (eval_logger 행과 같은 키, 값은 타입 변환)
# ===========================================================================
def is_frame_log(path):
    with io.open(path, encoding='utf-8') as f:
        return f.readline().startswith('# ')


def load_eval_csv(path):
    rows = []
    with open(path, 'r', newline='', encoding='utf-8') as f:
        rd = csv.DictReader(f)
        zones = [c[:-6] for c in rd.fieldnames if c.endswith('_state')]
        for r in rd:
            try:
                trks = [tuple(t) for t in json.loads(r['tracks_json'])]
            except Exception:
                trks = []
            row = {'session': r['session'], 'scenario': r['scenario'], 'condition': r['condition'],
                   'trial': r['trial'], 'gt_occupied': r['gt_occupied'], 'zones_set': r['zones_set'],
                   'warmup_sec': r['warmup_sec'], 't_rel': float(r['t_rel']), 'tracks': trks,
                   'marker': r['marker'], 'fold_permit': int(r['fold_permit']), 'src': None}
            for z in zones:
                row[z + '_state'] = r[z + '_state']
            rows.append(row)
    return rows, zones, None


def frame_labels(path, meta, cond_default):
    name = os.path.splitext(os.path.basename(path))[0]
    gt = meta.get('gt')
    scenario = GT_SCENARIO.get(gt, '{}_solo'.format(str(gt).replace('-', '').lower()))
    cond = meta.get('cond')
    if not cond:
        tok = re.findall(r'(static|motion)', name.lower())
        cond = tok[0] if tok else PREFIX_COND.get(name[:1].upper(), cond_default or 'unknown')
    trial = meta.get('trial')
    if trial is None:
        trial = name                     # 파일 이름을 회차로 (A1, S3 ... 구분이 되게)
    return scenario, cond, str(trial), gt


def load_frame_log(path, cond_default=None, replay_cfg=None):
    """프레임 로그 -> 공통 행. replay_cfg=(K, F, E) 이면 그 구성으로 재생한 판정을 쓴다."""
    meta, rows = af.load(path)
    names = meta['zone_names']
    scenario, cond, trial, gt = frame_labels(path, meta, cond_default)
    session = meta.get('started') or os.path.basename(path)
    ev = None
    if replay_cfg is not None:
        K, F, E = replay_cfg
        ev = af.track_evidence(meta, rows, F)[0] if F is not None else None
        st, fold, _ = af.replay(meta, rows, False, K, None, ev, 'A', E)
        K_used = K
    else:
        st = [{n: r['z'][n]['state'] for n in names} for r in rows]
        fold = [bool(r['fold']) for r in rows]
        K_used = meta.get('presence_confirm', 1)
    # 오차단 원인용 근거: 트랙(필터 후) / 점(K-of-K*mult 창)
    mult = meta.get('presence_window_mult', 4)
    hist = {n: deque(maxlen=K_used * mult) for n in names}
    out = []
    for i, r in enumerate(rows):
        src = {}
        for n in names:
            e = r['z'][n]
            hist[n].append(e['n'] >= meta['min_points'])
            if ev is not None:
                trk = ev[i][n][0]
            elif replay_cfg is not None:
                trk = e['trk']
            else:
                trk = e['trkf'] if e['trkf'] is not None else e['trk']
            src[n] = (bool(trk), sum(hist[n]) >= K_used)
        labels = r['marker'].split('|') if r['marker'] else []
        mk = 'sit' if 'sit' in labels else ('stand' if 'stand' in labels else '')
        row = {'session': session, 'scenario': scenario, 'condition': cond, 'trial': trial,
               'gt_occupied': gt or '', 'zones_set': meta['zones'],
               'warmup_sec': str(meta.get('warmup_block_sec', cg.WARMUP_BLOCK_SEC)),
               't_rel': r['t'], 'tracks': [(t[0], t[1], t[2]) for t in r['tracks']],
               'marker': mk, 'fold_permit': int(bool(fold[i])), 'src': src}
        for n in names:
            row[n + '_state'] = STATE_FULL.get(st[i][n], st[i][n])
        out.append(row)
    return out, names, meta


def expand(paths):
    out = []
    for c in paths:
        hits = sorted(glob.glob(c)) if any(ch in c for ch in '*?[') else [c]
        if not hits:
            print('!! 일치하는 파일 없음: {}'.format(c))
        out += hits
    return out


# ===========================================================================
# [1] eval_score 호환 채점 (eval_score.main 의 채점 루프를 그대로 옮김)
# ===========================================================================
def strip_markers(rows):
    """--ignore-markers: 마커를 지워 eval_score 의 '마커 없음 = 트라이얼 전체 점유' 규칙을 따르게 한다."""
    for r in rows:
        r['marker'] = ''
    return rows


def group_trials(rows):
    g = defaultdict(list)
    for r in rows:
        g[(r['session'], r['scenario'], r['condition'], r['trial'])].append(r)
    for k in g:
        g[k].sort(key=lambda r: float(r['t_rel']))
    return g


def score(rows, zones):
    trials = group_trials(rows)
    S = dict(tp=defaultdict(int), fp=defaultdict(int), fn=defaultdict(int), tn=defaultdict(int),
             danger_permit_frames=0, danger_permit_events=0, danger_permit_longest=0, occ_gt_frames=0,
             empty_miss=defaultdict(int), empty_miss_events=defaultdict(int),
             static_latched_frames=defaultdict(int), static_occ_frames=defaultdict(int),
             empty_all_frames=0, false_block_frames=0, err_x=[], err_y=[],
             ghost_over_frames=0, ghost_total_frames=0, extra_sum=0, idsw_report=[],
             sit_latencies=[], stand_latencies=[], per_trial_lines=[], n_trials=len(trials))
    tp, fp, fn, tn = S['tp'], S['fp'], S['fn'], S['tn']
    for key, trows in sorted(trials.items()):
        session, scenario, cond, trial = key
        gt = es.gt_set(trows[0])
        centers = es.zone_centers(trows[0]['zones_set'])
        srows = es.steady_rows(trows)
        sit_t = None
        stand_t = None
        for r in trows:
            if r['marker'] == 'sit':
                sit_t = float(r['t_rel'])
            elif r['marker'] == 'stand':
                stand_t = float(r['t_rel'])
        present_start = sit_t if sit_t is not None else float('-inf')
        present_end = stand_t if stand_t is not None else float('inf')

        def eff_gt(r):
            return gt if (present_start <= float(r['t_rel']) <= present_end) else set()

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
                    empty_flags.append(r['{}_state'.format(z)] == 'EMPTY')
            if empty_flags and any(empty_flags):
                ev, _ = es.contiguous_runs(empty_flags)
                S['empty_miss'][z] += sum(empty_flags)
                S['empty_miss_events'][z] += ev

        permit_flags = []
        for r in srows:
            rear_occ = any(z.startswith('Rear') for z in eff_gt(r))
            permit = int(r['fold_permit']) == 1
            if rear_occ:
                S['occ_gt_frames'] += 1
                permit_flags.append(permit)
            else:
                S['empty_all_frames'] += 1
                if not permit:
                    S['false_block_frames'] += 1
        if permit_flags:
            S['danger_permit_frames'] += sum(permit_flags)
            ev, longest = es.contiguous_runs(permit_flags)
            S['danger_permit_events'] += ev
            S['danger_permit_longest'] = max(S['danger_permit_longest'], longest)

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
                        S['static_latched_frames'][z] += 1
                        if st == 'OCCUPIED':
                            S['static_occ_frames'][z] += 1

        for r in srows:
            trks = r['tracks']
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
                    S['err_x'].append(x - cx)
                    S['err_y'].append(y - cy)

        ids_all = set()
        max_expected = 0
        for r in srows:
            trks = r['tracks']
            expected = len(eff_gt(r))
            max_expected = max(max_expected, expected)
            S['ghost_total_frames'] += 1
            if len(trks) > expected:
                S['ghost_over_frames'] += 1
                S['extra_sum'] += (len(trks) - expected)
            if expected > 0:
                for (i, _x, _y) in trks:
                    ids_all.add(i)
        if max_expected > 0:
            S['idsw_report'].append((scenario, cond, trial, max_expected, len(ids_all)))
        if sit_t is not None and gt:
            z = sorted(gt)[0]
            for r in trows:
                if float(r['t_rel']) >= sit_t and r['{}_state'.format(z)] == 'OCCUPIED':
                    S['sit_latencies'].append(float(r['t_rel']) - sit_t)
                    break
        if stand_t is not None and gt:
            z = sorted(gt)[0]
            for r in trows:
                if float(r['t_rel']) >= stand_t and r['{}_state'.format(z)] == 'EMPTY':
                    S['stand_latencies'].append(float(r['t_rel']) - stand_t)
                    break

        S['per_trial_lines'].append('  {:16s} {:6s} #{:<2s} gt=[{:12s}] frames={}'.format(
            scenario, cond, trial, '|'.join(sorted(gt)) or '공석', len(trows)))
    return S


def rate(a, b):
    return (a / b) if b else float('nan')


def pct(x):
    return '  n/a' if (x != x) else '{:5.1f}%'.format(100 * x)


def print_compat(S, zones):
    """eval_score.main 의 리포트와 같은 형식."""
    tp, fp, fn, tn = S['tp'], S['fp'], S['fn'], S['tn']
    W = 64
    print('=' * W)
    print(' 트래킹 성능 평가  (폴딩 끼임 방지 시스템)')
    print('=' * W)
    print(' 트라이얼 {}개 · steady 프레임 기준(warmup 제외)'.format(S['n_trials']))
    for ln in S['per_trial_lines']:
        print(ln)

    print('\n' + '─' * W)
    print(' ⓵ 안전 핵심 (fail-safe)')
    print('─' * W)
    print('  위험 오허용(뒷좌 점유 중 FOLD 허용): {} 프레임 / {} 뒷좌점유프레임 = {}'.format(
        S['danger_permit_frames'], S['occ_gt_frames'], pct(rate(S['danger_permit_frames'], S['occ_gt_frames']))))
    print('     └ 연속 이벤트 {}건, 최장 {} 프레임  [목표 0]  (조수석은 폴딩 무관, 제외)'.format(
        S['danger_permit_events'], S['danger_permit_longest']))
    tot_empty_miss = sum(S['empty_miss'].values())
    print('  EMPTY 오판(점유석을 빈 좌석으로)  : {} 프레임, 이벤트 {}건  (뒷좌=위험/조수석=검출누락)'.format(
        tot_empty_miss, sum(S['empty_miss_events'].values())))
    for z in zones:
        if S['empty_miss'][z]:
            tag = '위험' if z.startswith('Rear') else '검출누락'
            print('     └ {}: {} 프레임 / {} 이벤트  [{}]'.format(z, S['empty_miss'][z], S['empty_miss_events'][z], tag))
    TP = sum(tp.values())
    FN = sum(fn.values())
    print('  점유 검출 재현율 Recall(종합)    : {}  (TP={} FN={})'.format(
        pct(rate(TP, TP + FN)), TP, FN))
    sl = sum(S['static_latched_frames'].values())
    so = sum(S['static_occ_frames'].values())
    print('  정지 착석 검출 유지율(래치 후)   : {}  ({}/{} 프레임)'.format(
        pct(rate(so, sl)), so, sl))

    print('\n' + '─' * W)
    print(' ⓶ 신뢰·사용성')
    print('─' * W)
    print('  좌석별 혼동행렬 (OCCUPIED=positive)')
    print('    {:10s} {:>5s} {:>5s} {:>5s} {:>5s} | {:>6s} {:>6s} {:>6s}'.format(
        'zone', 'TP', 'FP', 'FN', 'TN', 'Prec', 'Rec', 'F1'))
    for z in zones:
        P = rate(tp[z], tp[z] + fp[z])
        R = rate(tp[z], tp[z] + fn[z])
        F1 = rate(2 * P * R, P + R) if (P == P and R == R and (P + R) > 0) else float('nan')
        print('    {:10s} {:5d} {:5d} {:5d} {:5d} | {:>6s} {:>6s} {:>6s}'.format(
            z, tp[z], fp[z], fn[z], tn[z], pct(P).strip(), pct(R).strip(), pct(F1).strip()))
    print('  오차단률(공석인데 FOLD 차단)     : {}  ({}/{} 프레임)'.format(
        pct(rate(S['false_block_frames'], S['empty_all_frames'])), S['false_block_frames'], S['empty_all_frames']))
    sit_l, stand_l = S['sit_latencies'], S['stand_latencies']
    if sit_l:
        print('  착석→OCCUPIED 지연 : 평균 {:.2f}s / 최대 {:.2f}s (n={})'.format(
            sum(sit_l) / len(sit_l), max(sit_l), len(sit_l)))
    if stand_l:
        print('  하차→EMPTY 지연    : 평균 {:.2f}s / 최대 {:.2f}s (n={})'.format(
            sum(stand_l) / len(stand_l), max(stand_l), len(stand_l)))
    if not sit_l and not stand_l:
        print('  (반응지연: 마커(s/e) 기록이 없어 생략)')

    print('\n' + '─' * W)
    print(' ⓷ 트래킹 품질 근거')
    print('─' * W)
    ex, ey = S['err_x'], S['err_y']
    if ex:
        def rmse(v): return math.sqrt(sum(e * e for e in v) / len(v))
        def mae(v): return sum(abs(e) for e in v) / len(v)
        print('  위치오차(측방 X) : MAE {:.3f}m  RMSE {:.3f}m'.format(mae(ex), rmse(ex)))
        print('  위치오차(전방 Y) : MAE {:.3f}m  RMSE {:.3f}m  (n={} 샘플)'.format(
            mae(ey), rmse(ey), len(ex)))
    else:
        print('  (위치오차: GT 점유 좌석 내 트랙 샘플 없음)')
    print('  고스트 트랙률(예상 초과 프레임) : {}  (평균 초과 {:.2f}개)'.format(
        pct(rate(S['ghost_over_frames'], S['ghost_total_frames'])),
        rate(S['extra_sum'], S['ghost_total_frames']) if S['ghost_total_frames'] else 0.0))
    if S['idsw_report']:
        print('  과분할 ID(고유 ID수 vs 예상 인원):')
        for (sc, cd, tr, exp, seen) in S['idsw_report']:
            flag = '  ⚠ 과분할' if seen > exp else ''
            print('     {:16s} {:6s} #{}: 예상 {} / 관측 {}{}'.format(sc, cd, tr, exp, seen, flag))
    print('=' * W)


# ===========================================================================
# [2] 구간별 분리  /  [3] 오차단 원인
# ===========================================================================
def phases(rows, zones):
    """eval 호환 채점과 같은 행·같은 마커 규칙으로, 합계가 [1] 과 일치하도록 나눈다."""
    P = defaultdict(float)
    for key, trows in sorted(group_trials(rows).items()):
        gt = es.gt_set(trows[0])
        srows = es.steady_rows(trows)
        sit_t = stand_t = None
        for r in trows:
            if r['marker'] == 'sit':
                sit_t = float(r['t_rel'])
            elif r['marker'] == 'stand':
                stand_t = float(r['t_rel'])
        ps = sit_t if sit_t is not None else float('-inf')
        pe = stand_t if stand_t is not None else float('inf')
        first = {}
        for z in gt:
            first[z] = next((float(r['t_rel']) for r in trows
                             if float(r['t_rel']) >= ps and r['{}_state'.format(z)] == 'OCCUPIED'), None)
        dts = [srows[i + 1]['t_rel'] - srows[i]['t_rel'] for i in range(len(srows) - 1)] + [0.0]
        for r, dt in zip(srows, dts):
            t = float(r['t_rel'])
            present = gt if ps <= t <= pe else set()
            permit = int(r['fold_permit']) == 1
            if any(z.startswith('Rear') for z in present):
                entry = any(z.startswith('Rear') and (first[z] is None or t < first[z]) for z in present)
                ph = '입장' if entry else '착석'
                P['occ_' + ph] += 1
                if permit:
                    P['danger_' + ph] += 1
                    P['danger_s_' + ph] += dt
            else:
                ph = '빈 차' if not gt else ('입장 전' if t < ps else '퇴장 후')
                P['empty_' + ph] += 1
                if not permit:
                    P['block_' + ph] += 1
                    P['block_s_' + ph] += dt
                    if r['src'] is not None:
                        blk = [z for z in zones if z.startswith('Rear') and r['{}_state'.format(z)] != 'EMPTY']
                        c = ('트랙' if any(r['src'][z][0] for z in blk) else
                             '점' if any(r['src'][z][1] for z in blk) else '대기(근거 없음)')
                        P['cause_' + ph + '_' + c] += dt
            for z in present:
                ph = '입장' if (first[z] is None or t < first[z]) else '착석'
                occ = r['{}_state'.format(z)] == 'OCCUPIED'
                P['tp_' + ph] += occ
                P['fn_' + ph] += (not occ)
                P['emiss_' + ph] += r['{}_state'.format(z)] == 'EMPTY'
    return P


def print_phases(P, has_src):
    W = 64
    print('\n' + '─' * W)
    print(' [2] 구간별 분리 (합계는 위 리포트와 같음)')
    print('─' * W)
    print('  입장 = s 마커 ~ 해당 좌석 첫 OCCUPIED, 착석 = 첫 OCCUPIED ~ e 마커')
    for ph in ('입장', '착석'):
        print('  {:4s} 위험 오허용 {:6d} / {:6d} 프레임 = {}  ({:.1f}초)   Recall {}  EMPTY 오판 {} 프레임'.format(
            ph, int(P['danger_' + ph]), int(P['occ_' + ph]), pct(rate(P['danger_' + ph], P['occ_' + ph])),
            P['danger_s_' + ph], pct(rate(P['tp_' + ph], P['tp_' + ph] + P['fn_' + ph])), int(P['emiss_' + ph])))
    for ph in ('입장 전', '퇴장 후', '빈 차'):
        print('  {:5s} 오차단 {:6d} / {:6d} 프레임 = {}  ({:.1f}초)'.format(
            ph, int(P['block_' + ph]), int(P['empty_' + ph]), pct(rate(P['block_' + ph], P['empty_' + ph])),
            P['block_s_' + ph]))
    if has_src:
        print('\n' + '─' * W)
        print(' [3] 오차단 원인 (프레임 로그, 막고 있던 뒷좌 존의 근거, 초)')
        print('─' * W)
        for ph in ('입장 전', '퇴장 후', '빈 차'):
            parts = ['{} {:.1f}'.format(c, P['cause_' + ph + '_' + c])
                     for c in ('트랙', '점', '대기(근거 없음)') if P['cause_' + ph + '_' + c] > 0]
            print('  {:5s}: {}'.format(ph, ', '.join(parts) or '-'))
        print('  트랙 = 필터 후 트랙 근거, 점 = K-of-(K*4) 점 근거, 대기 = 근거가 끊긴 뒤 E 초 기다리는 중')


# ===========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(description='현재 코드용 성능 평가(eval_score 호환 + 구간·원인 분리)')
    ap.add_argument('csv', nargs='*', help='프레임 로그 또는 eval_logger CSV (섞어도 됨)')
    ap.add_argument('--replay', default=None,
                    help='프레임 로그를 이 구성으로 재생해 채점: K,F,E (F 에 none 가능). 예: 2,3,3')
    ap.add_argument('--cond', default=None, choices=['static', 'motion'],
                    help='조건을 정할 수 없는 프레임 로그의 기본 조건')
    ap.add_argument('--plots', metavar='DIR', default=None, help='eval_score 와 같은 PNG 저장')
    ap.add_argument('--ignore-markers', action='store_true',
                    help='마커를 무시하고 트라이얼 전체를 GT 점유로 채점 (9/9 베이스라인과 같은 정의)')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    paths = expand(a.csv)
    if not paths:
        ap.error('csv 경로가 필요합니다')
    replay_cfg = None
    if a.replay:
        k, f, e = [x.strip() for x in a.replay.split(',')]
        replay_cfg = (int(k), None if f.lower() in ('none', '-') else float(f), float(e))

    rows, zones, srcs = [], None, []
    for p in paths:
        if is_frame_log(p):
            r, z, meta = load_frame_log(p, a.cond, replay_cfg)
            srcs.append('{} [프레임 로그, {}]'.format(os.path.basename(p),
                        '재생 K={},F={},E={}'.format(*a.replay.split(',')) if replay_cfg else
                        '라이브 판정 K={},F={},E={}'.format(meta.get('presence_confirm'), meta.get('freeze_sec'),
                                                           meta.get('empty_confirm_sec'))))
        else:
            r, z, _ = load_eval_csv(p)
            srcs.append('{} [eval_logger CSV]'.format(os.path.basename(p)))
        rows += r
        zones = zones or z
    print('[0] 입력')
    for s in srcs:
        print('    ' + s)
    if a.ignore_markers:
        strip_markers(rows)
        print('    --ignore-markers: 마커 무시, 트라이얼 전체를 GT 점유로 채점 (베이스라인과 같은 정의)')
    for key, trows in sorted(group_trials(rows).items()):
        ns = sum(1 for r in trows if r['marker'] == 'sit')
        ne = sum(1 for r in trows if r['marker'] == 'stand')
        if ns > 1 or ne > 1:
            print('    !! {} #{}: s 마커 {}개, e 마커 {}개 -> eval_score 규칙대로 마지막 것을 사용'
                  '(analyze_frames 는 첫 s 사용). 잘못 눌린 것이면 다시 측정'.format(key[1], key[3], ns, ne))
    S = score(rows, zones)
    print_compat(S, zones)
    print_phases(phases(rows, zones), any(r['src'] is not None for r in rows))
    if a.plots:
        try:
            es._make_plots(a.plots, zones, S['tp'], S['fp'], S['fn'], S['tn'], rate,
                           S['danger_permit_frames'], S['occ_gt_frames'],
                           sum(S['static_latched_frames'].values()), sum(S['static_occ_frames'].values()))
        except Exception as e:
            print('[plots] 실패(matplotlib 필요?):', e)
    return 0


# ===========================================================================
def _run_capture(fn, *args):
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args)
    return buf.getvalue()


def _eval_score_output(path):
    import subprocess
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    here = os.path.dirname(os.path.abspath(__file__))
    out = subprocess.run([sys.executable, os.path.join(here, 'eval_score.py'), path],
                         capture_output=True, env=env, cwd=here)
    return out.stdout.decode('utf-8').replace('\r\n', '\n')


def _compat_only(path, **kw):
    rows, zones = (load_frame_log(path, **kw)[:2] if is_frame_log(path) else load_eval_csv(path)[:2])
    S = score(rows, zones)
    return _run_capture(print_compat, S, zones), rows, zones, S


def _selftest():
    import random
    import tempfile
    from frame_logger import FrameLogger
    from presence_monitor import PresenceClearanceMonitor
    from track_parser import Track
    from pointcloud_parser import Point
    ok_all = True
    here = os.path.dirname(os.path.abspath(__file__))

    # 1) 베이스라인 CSV: eval_score 출력과 글자 하나까지 같아야 함
    base = os.path.join(here, '..', 'eval_baseline', 'eval_log_baseline.csv')
    if os.path.exists(base):
        ref = _eval_score_output(base)
        mine, _, _, _ = _compat_only(base)
        c1 = ref.strip() == mine.strip()
        print('OK' if c1 else 'FAIL', ': 베이스라인 23트라이얼 eval_score 출력과 완전 일치 ({}줄)'.format(
            len(ref.strip().splitlines())))
        if not c1:
            for i, (x, y) in enumerate(zip(ref.splitlines(), mine.splitlines())):
                if x != y:
                    print('   첫 차이 {}행\n   eval_score: {}\n   eval_frames: {}'.format(i, x, y))
                    break
        ok_all &= c1
    else:
        print('SKIP: 베이스라인 CSV 없음 ({})'.format(base))

    # 2) 합성 프레임 로그: 같은 내용을 eval_logger 형식으로 쓰면 eval_score 결과가 같아야 함
    random.seed(11)
    seats = cg.SEATS_PLACEHOLDER
    names = [z.name for z in seats]
    zl = [z for z in seats if z.name == 'Rear-L'][0]
    lx, ly = (zl.xmin + zl.xmax) / 2, (zl.ymin + zl.ymax) / 2
    path = os.path.join(tempfile.gettempdir(), 'eval_frames_selftest.csv')
    mon = PresenceClearanceMonitor(seats, presence_confirm=2, freeze_sec=3.0)
    old_e = cg.EMPTY_CONFIRM_SEC
    cg.EMPTY_CONFIRM_SEC = 3.0
    FR = 0.055
    try:
        meta = {'zone_names': names, 'zones': 'placeholder', 'gt': 'Rear-L', 'min_points': mon.min_points,
                'presence_confirm': 2, 'presence_window_mult': 4, 'adaptive': False, 'weak_release': 1.0,
                'weak_hold': 0.3, 'strong_track_sec': 0.5, 'strong_sustain_sec': 3.0,
                'empty_confirm_sec': 3.0, 'freeze_sec': 3.0, 'warmup_block_sec': cg.WARMUP_BLOCK_SEC,
                'fold_zone_prefix': 'Rear', 'started': '20260918_120000', 'cond': 'static', 'trial': 1}
        log = FrameLogger(path, names, meta)
        now, fn, nf = 0.0, 1, 0.0
        lt, lp = [], []
        while now < 45.0:
            if now >= nf:
                tr, pts = [], []
                if 11.0 <= nf < 30.0:                          # s=8s(문 열기), 11s 부터 좌석에 근거
                    tr = [Track(5, lx + random.uniform(-0.03, 0.03), ly, 0.9)]
                    if random.random() < 0.5:
                        pts = [Point(lx, ly, 0.9, 0.4, 20) for _ in range(4)]
                elif nf >= 30.0:
                    tr = [Track(9, lx, ly, 0.9)]                # 퇴장 후 유령
                lt, lp = tr, pts
                res = mon.update(tr, pts, now)
                if abs(nf - 8.0) < FR / 2:
                    log.mark('sit')
                    log.mark('act')                            # 'sit|act' 로 겹친 마커
                if abs(nf - 30.0) < FR / 2:
                    log.mark('stand')
                log.log_frame(now, fn, tr, pts, res, mon.fold_permitted(now))
                fn += 1
                nf += FR
            else:
                mon.update(lt, lp, now, new_frame=False)
                log.note_stale(now)
            now += random.uniform(0.004, 0.03)
        log.close()
    finally:
        cg.EMPTY_CONFIRM_SEC = old_e
    mine, rows, zones, S = _compat_only(path)
    evp = os.path.join(tempfile.gettempdir(), 'eval_frames_selftest_eval.csv')
    with open(evp, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['session', 'scenario', 'condition', 'trial', 'gt_occupied', 'zones_set', 'warmup_sec',
                    'frame', 't_rel', 'n_tracks', 'tracks_json', 'marker', 'fold_permit'] +
                   ['{}_state'.format(z) for z in zones])
        for i, r in enumerate(rows):
            w.writerow([r['session'], r['scenario'], r['condition'], r['trial'], r['gt_occupied'], r['zones_set'],
                        r['warmup_sec'], i + 1, r['t_rel'], len(r['tracks']),
                        json.dumps([list(t) for t in r['tracks']]), r['marker'], r['fold_permit']] +
                       [r['{}_state'.format(z)] for z in zones])
    ref = _eval_score_output(evp)
    c2 = ref.strip() == mine.strip()
    print('OK' if c2 else 'FAIL', ': 합성 프레임 로그 채점 = 같은 내용의 eval_logger 형식을 eval_score 로 채점한 결과')
    ok_all &= c2
    c3 = len(S['sit_latencies']) == 1 and 2.9 <= S['sit_latencies'][0] <= 3.2
    print('OK' if c3 else 'FAIL', ': 겹친 마커 sit|act 인식, 착석 지연 {:.2f}s (심은 값 약 3s)'.format(
        S['sit_latencies'][0] if S['sit_latencies'] else -1))
    ok_all &= c3

    # 3) 구간 분리 합계 = 호환 리포트 합계, 입장 구간 위험 오허용이 따로 잡힘
    P = phases(rows, zones)
    c4 = (int(P['danger_입장'] + P['danger_착석']) == S['danger_permit_frames'] and
          int(P['occ_입장'] + P['occ_착석']) == S['occ_gt_frames'] and
          int(P['block_입장 전'] + P['block_퇴장 후'] + P['block_빈 차']) == S['false_block_frames'] and
          P['danger_입장'] > 0 and P['danger_착석'] == 0)
    print('OK' if c4 else 'FAIL', ': 구간 분리 합계 일치, 위험 오허용은 입장 구간에만 ({:.0f} 프레임)'.format(
        P['danger_입장']))
    ok_all &= c4
    c5 = P['cause_퇴장 후_트랙'] > 0 and P['cause_퇴장 후_대기(근거 없음)'] > 0
    print('OK' if c5 else 'FAIL', ': 퇴장 후 오차단 원인 분리 (유령 트랙 {:.1f}s / 대기 {:.1f}s)'.format(
        P['cause_퇴장 후_트랙'], P['cause_퇴장 후_대기(근거 없음)']))
    ok_all &= c5

    # 4) 라이브와 같은 구성으로 재생하면 라이브 채점과 같음
    mine_r, _, _, _ = _compat_only(path, replay_cfg=(2, 3.0, 3.0))
    c6 = mine_r == mine
    print('OK' if c6 else 'FAIL', ': --replay 2,3,3 채점 = 라이브 판정 채점')
    ok_all &= c6

    # 5) --ignore-markers: 마커를 지운 채점 = 마커 없는 같은 내용을 eval_score 로 채점한 결과,
    #    그리고 입장 구간(s 이전)까지 점유로 세므로 점유 GT 프레임이 늘어남
    _, rows_i, _, _ = _compat_only(path)
    strip_markers(rows_i)
    S_i = score(rows_i, zones)
    mine_i = _run_capture(print_compat, S_i, zones)
    with open(evp, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['session', 'scenario', 'condition', 'trial', 'gt_occupied', 'zones_set', 'warmup_sec',
                    'frame', 't_rel', 'n_tracks', 'tracks_json', 'marker', 'fold_permit'] +
                   ['{}_state'.format(z) for z in zones])
        for i, r in enumerate(rows_i):
            w.writerow([r['session'], r['scenario'], r['condition'], r['trial'], r['gt_occupied'], r['zones_set'],
                        r['warmup_sec'], i + 1, r['t_rel'], len(r['tracks']),
                        json.dumps([list(t) for t in r['tracks']]), '', r['fold_permit']] +
                       [r['{}_state'.format(z)] for z in zones])
    ref_i = _eval_score_output(evp)
    c7 = (ref_i.strip() == mine_i.strip() and S_i['occ_gt_frames'] > S['occ_gt_frames'] and
          not S_i['sit_latencies'] and any(r['marker'] for r in rows))
    print('OK' if c7 else 'FAIL', ': --ignore-markers 채점 = 마커 없는 eval_logger 형식을 eval_score 로 채점한 결과 '
          '(점유 GT {} -> {} 프레임, 원본 행의 마커는 그대로)'.format(S['occ_gt_frames'], S_i['occ_gt_frames']))
    ok_all &= c7

    os.remove(path)
    os.remove(evp)
    print('')
    print('all eval_frames selftests passed' if ok_all else '!! SELFTEST FAILED')
    return 0 if ok_all else 1


if __name__ == '__main__':
    sys.exit(main())
