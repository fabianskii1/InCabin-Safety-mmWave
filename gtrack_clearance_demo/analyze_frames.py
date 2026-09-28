# gtrack_clearance_demo / analyze_frames.py
# ---------------------------------------------------------------------------
# frame_logger.py 가 남긴 프레임 CSV 를 분석해 해제 시간·디바운스를 '재측정 없이' 고른다.
#
#   1) 재생 검증 : 로그에 적힌 라이브 설정으로 상태머신을 재생해 기록된 상태와 일치하는지 확인.
#                 일치하지 않으면 아래 비교 결과를 믿으면 안 된다.
#   2) 끊김 분포 : 실제 착석 좌석(GT)에서 근거(트랙 또는 점 >= min_points)가 끊긴 길이.
#                 WEAK 해제 시간은 '탑승자 끊김 최댓값 + 여유' 보다 길어야 한다.
#   3) 후보 비교 : 기존(일괄 4.0s) / adaptive x WEAK 해제 R x 디바운스 K 조합을 재생해
#                 안전(착석 중 EMPTY, 착석 중 폴딩 허용)과 비용(오점유, 오차단)을 표로 비교.
#   [2d] 정지 구간: 두 m 마커 사이(호흡만 하는 정지 착석)의 실제 갱신 공백, 얼어붙은 트랙만 남은
#                 시간, 일반/fineMotion 프레임별 점, 존 안 트랙 생성·소멸.
#
# 해제 방식(--schemes):
#   A = 현행. 얼어붙은 트랙은 F초까지 근거 -> 그때부터 E초 조용해야 EMPTY (점 없으면 F+E)
#   B = fresh_clearance. 조용한 시간을 마지막 실제 갱신(점 근거 or 좌표가 바뀐 트랙)부터 셈
#       (점 없으면 max(F, E))
#
# 사용:
#   py analyze_frames.py frames_xxx.csv                       # 마커(s/e)와 메타의 gt 사용
#   py analyze_frames.py frames_xxx.csv --releases 1,1.5,2,2.5,3 --confirms 1,2,3
#   py analyze_frames.py frames_*.csv                         # 여러 세션 합산 비교
#   py analyze_frames.py frames_empty.csv --gt none            # 탑승자 없는 세션(모든 점유 = 오점유)
#   py analyze_frames.py "S*.csv" --schemes A,B --releases "" --confirms 1,2 --freezes none,1,5 --empty-confirms 3,4
#   py analyze_frames.py --selftest                           # 하드웨어 없이 검증
# ---------------------------------------------------------------------------
import argparse
import csv
import io
import json
import math
import os
import sys
from collections import deque

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import clearance_gtrack as cg
from adaptive_clearance import AdaptiveZoneClearance
from fresh_clearance import FreshZoneClearance

CODE = {cg.STATE_EMPTY: 'E', cg.STATE_OCCUPIED: 'O', cg.STATE_UNKNOWN: 'U'}
LATE_MARK_SEC = 3.0   # s 마커 늦음 경고용: 마커 전 이 시간 안에 좌석이 이미 점유됐는지만 본다(지표에는 반영 안 함)
# fineMotion 프레임: DSS frameCounter >= 누적 프레임 수 && frameCounter % 주기 == 0 (radarProcess.c:473-474).
# cfg 'fineMotionCfg -1 1 1.0 10 2' -> 주기 10, 누적 ceil(1.0s/55ms)=19 -> 48 의 약수로 내려 16.
# 헤더 frameNumber(fn)와 DSS frameCounter 는 다른 변수라, fn 으로 가르는 것은 실측 패턴(입장 중
# fn%10==0 프레임만 점이 5~10배 적음)에 근거한 구분이다. 속도 0 점 열(_n0)이 있으면 함께 본다.
FM_CYCLE = 10
FM_MIN_FRAMES = 16


def is_fm_frame(fn):
    return fn is not None and fn >= FM_MIN_FRAMES and fn % FM_CYCLE == 0


# 손상 트랙: UART 수신이 깨진 프레임에서 파서가 만들어 낸 값(예: A2 t=92.28 id 1589318287,
# y=-1.57e31). 실측 A1~A3 의 8건 모두 바로 다음 프레임 번호가 빠져 있었다. 판별 기준은 라이브
# 필터와 같아야 하므로 presence_monitor 의 것을 그대로 쓴다.
from presence_monitor import valid_track_values  # noqa: E402


def valid_track(t):
    return valid_track_values(*t)


# ===========================================================================
# 로드
# ===========================================================================
def load(path):
    meta = {}
    body = []
    with io.open(path, encoding='utf-8') as f:
        for line in f:
            if line.startswith('# ') and '=' in line and not body:
                k, v = line[2:].rstrip('\r\n').split('=', 1)
                meta[k] = json.loads(v)
            else:
                body.append(line)
    rows = []
    for r in csv.DictReader(body):
        row = {'t': float(r['t']),
               'fn': int(r['fn']) if r['fn'] not in ('', None) else None,
               'stale_n': int(r['stale_n']),
               'stale_t0': float(r['stale_t0']) if r['stale_t0'] else None,
               'stale_t1': float(r['stale_t1']) if r['stale_t1'] else None,
               'marker': r['marker'] or '',
               'fold': int(r['fold']),
               'tracks': [tuple(t) for t in json.loads(r['tracks'])] if r.get('tracks') else [],
               'n_points': _opt_int(r.get('n_points')),
               'n_points0': _opt_int(r.get('n_points0')),      # 2026-09-18 이전 로그엔 없음 -> None
               'z': {}}
        for zn in meta['zone_names']:
            trkf = r.get(zn + '_trkf')
            row['z'][zn] = {'trk': int(r[zn + '_trk']) == 1,
                            'trkf': None if trkf in (None, '') else int(trkf) == 1,   # 9/18 이전 로그엔 없음
                            'tid': int(r[zn + '_tid']),
                            'n': int(r[zn + '_n']),
                            'n0': _opt_int(r.get(zn + '_n0')),
                            'snrmax': float(r[zn + '_snrmax']),
                            'state': r[zn + '_state']}
        rows.append(row)
    return meta, rows


def _opt_int(v):
    return None if v in (None, '') else int(v)


# ===========================================================================
# 얼어붙은 트랙
# ===========================================================================
def frozen_tracks(meta, rows):
    """행별 [(tid, 들어있는 존 이름 또는 None, 고정 지속 초, 이번 행 갱신 여부)] 와 존 좌표 사전.
    '고정' = 기록된 좌표(x,y,z)가 직전 프레임과 완전히 같음. GTRACK 은 연관된 점이 없으면
    예측값을 그대로 쓰고, 정지 표적이면 속도·가속도를 0 으로 고정해 좌표가 변하지 않는다
    (gtrack_unit_update.c 253-283, 441-445).
    '갱신' = 이번 행에서 좌표가 바뀌었거나 처음 나타난 트랙. 로그 좌표가 mm 단위로 반올림돼
    있어 1 mm 미만 갱신은 고정으로 보인다.
    손상 트랙(valid_track 아님)은 건너뛴다. 손상 프레임에서 잠깐 빠졌던 트랙이 같은 좌표로
    돌아오면 갱신이 아니므로, 사라진 트랙의 마지막 좌표를 지우지 않고 기억한다(2026-09-18).
    존 좌표는 메타의 zones(존 세트 이름)로 clearance_gtrack.SEAT_SETS 에서 가져온다."""
    zset = cg.SEAT_SETS.get(meta.get('zones'))
    if not zset:
        return None, None
    zones = {z.name: z for z in zset}
    names = meta['zone_names']
    if any(n not in zones for n in names):
        return None, None
    last = {}
    out = []
    for r in rows:
        t = r['t']
        cur = []
        for tr in r['tracks']:
            if not valid_track(tr):
                continue
            tid, x, y, z = tr
            pos = (x, y, z)
            changed = not (tid in last and last[tid][0] == pos)
            since = t if changed else last[tid][1]
            last[tid] = (pos, since)
            zn = next((n for n in names if zones[n].xmin <= x <= zones[n].xmax
                       and zones[n].ymin <= y <= zones[n].ymax), None)
            cur.append((tid, zn, t - since, changed))
        out.append(cur)
    return out, zones


def corrupt_frames(rows):
    """손상 트랙이 들어 있는 행 수."""
    return sum(1 for r in rows if any(not valid_track(t) for t in r['tracks']))


def track_evidence(meta, rows, freeze_sec):
    """존별 트랙 근거를 트랙 좌표로 다시 계산. freeze_sec 이상 고정된 트랙은 근거에서 제외.
    반환: (행별 {zone: (track_in, tid, fresh)}, 기록과의 불일치 수) 또는 (None, None).
    fresh = 존 안 트랙 중 이번 행에 좌표가 갱신된 것이 있음 (B안의 '실제 갱신').
    한 트랙이 두 존에 겹칠 수 있으므로 존마다 따로 포함 여부를 본다(라이브와 동일)."""
    fr, zones = frozen_tracks(meta, rows)
    if fr is None:
        return None, None
    names = meta['zone_names']
    out, mism = [], 0
    for r, cur in zip(rows, fr):
        dur = {tid: d for tid, _, d, _ in cur}
        chg = {tid: c for tid, _, _, c in cur}
        ev = {}
        for n in names:
            zn = zones[n]
            inz = [tid for tid, x, y, _ in (tr for tr in r['tracks'] if valid_track(tr))
                   if zn.xmin <= x <= zn.xmax and zn.ymin <= y <= zn.ymax]
            mism += (bool(inz) != r['z'][n]['trk'])
            fresh = any(chg[tid] for tid in inz)
            if freeze_sec is not None:
                inz = [tid for tid in inz if dur[tid] < freeze_sec]
            ev[n] = (bool(inz), inz[0] if inz else None, fresh)
        out.append(ev)
    return out, mism


# ===========================================================================
# 재생
# ===========================================================================
def replay(meta, rows, adaptive, K, R, trk_ev=None, scheme='A', E=None):
    """라이브 main 루프를 그대로 흉내낸다. 반환: (행별 {zone: 'E'/'O'/'U'}, 행별 fold, startT)
    trk_ev: track_evidence() 결과를 주면 기록된 트랙 근거 대신 사용(얼어붙은 트랙 필터 평가용).
    scheme: 'A' = 현행 상태머신(clearance_gtrack / adaptive). E 를 주면 재생하는 동안만
                  clearance_gtrack.EMPTY_CONFIRM_SEC 값을 바꾼다(파일은 그대로, adaptive 에는 적용 안 함).
            'B' = fresh_clearance.FreshZoneClearance. 좌표 갱신 여부가 필요해 trk_ev 필수."""
    if scheme == 'B' and trk_ev is None:
        raise ValueError('B안 재생에는 track_evidence() 결과가 필요합니다')
    old_e = cg.EMPTY_CONFIRM_SEC
    if scheme == 'A' and E is not None and not adaptive:
        cg.EMPTY_CONFIRM_SEC = E
    try:
        return _replay(meta, rows, adaptive, K, R, trk_ev, scheme, E)
    finally:
        cg.EMPTY_CONFIRM_SEC = old_e


def _replay(meta, rows, adaptive, K, R, trk_ev, scheme, E):
    names = meta['zone_names']
    zones = [cg.Zone(n, 0, 0, 0, 0) for n in names]
    if scheme == 'B':
        machines = [FreshZoneClearance(z, empty_confirm=E) for z in zones]
    elif adaptive:
        machines = [AdaptiveZoneClearance(z, weak_release=R,
                                          weak_hold=meta.get('weak_hold'),
                                          strong_track_sec=meta.get('strong_track_sec'),
                                          strong_sustain_sec=meta.get('strong_sustain_sec'))
                    for z in zones]
    else:
        machines = [cg.ZoneClearance(z) for z in zones]
    mult = meta.get('presence_window_mult', 4)
    hists = {n: deque(maxlen=K * mult) for n in names}
    min_pts = meta['min_points']
    warm = meta.get('warmup_block_sec', cg.WARMUP_BLOCK_SEC)
    prefix = meta.get('fold_zone_prefix', 'Rear')
    fold_idx = [i for i, n in enumerate(names) if n.startswith(prefix)] or list(range(len(names)))
    prev = {n: (False, None, False, False) for n in names}   # (occupied, tid, track_in, fresh)
    startT = None

    def step(m, p, now):
        occ, tid, track_in, fresh = p
        if scheme == 'B':
            m.update(occ, fresh, now, tid)
        elif adaptive:
            m.update(occ, tid, now, track_in)
        else:
            m.update(occ, tid, now)

    states, folds = [], []
    for ri, row in enumerate(rows):
        # 두 프레임 사이 재호출: 근거는 직전 프레임 그대로, 시간만 진행
        if row['stale_n'] > 0:
            if startT is None:
                startT = row['stale_t0']
                for m, n in zip(machines, names):
                    step(m, prev[n], row['stale_t0'])
            for m, n in zip(machines, names):
                step(m, prev[n], row['stale_t1'])
        if startT is None:
            startT = row['t']
        for m, n in zip(machines, names):
            e = row['z'][n]
            track_in = e['trk']
            tid = e['tid'] if e['tid'] >= 0 else None
            trk_fresh = False
            if trk_ev is not None:
                track_in, tid, trk_fresh = trk_ev[ri][n]
            hists[n].append(e['n'] >= min_pts)
            pres_in = sum(hists[n]) >= K
            p = (track_in or pres_in, tid, track_in, pres_in or trk_fresh)
            step(m, p, row['t'])
            prev[n] = p
        st = {n: CODE[m.state] for m, n in zip(machines, names)}
        fold = (row['t'] - startT) >= warm and all(machines[i].state == cg.STATE_EMPTY
                                                     for i in fold_idx)
        states.append(st)
        folds.append(fold)
    return states, folds, startT


# ===========================================================================
# 지표
# ===========================================================================
def durations(rows):
    d = [rows[i + 1]['t'] - rows[i]['t'] for i in range(len(rows) - 1)]
    d.append(0.0)
    return d


def metrics(meta, rows, states, folds, startT, gt, enter, exit_):
    names = meta['zone_names']
    prefix = meta.get('fold_zone_prefix', 'Rear')
    warm = meta.get('warmup_block_sec', cg.WARMUP_BLOCK_SEC)
    dts = durations(rows)
    out = {}

    rear = [n for n in names if n.startswith(prefix)]
    # 안전: GT 좌석
    first_occ = None
    first_idx = None
    if gt and enter is not None:
        for i, r in enumerate(rows):
            if r['t'] >= enter and states[i][gt] == 'O':
                first_occ = r['t']
                first_idx = i
                break
        out['latency'] = None if first_occ is None else first_occ - enter
    seat_end = exit_ if exit_ is not None else rows[-1]['t']
    seat_empty_t, empty_ev, danger = 0.0, 0, 0.0
    if first_occ is not None:
        was_occ = True
        # 시각이 아니라 행 번호로 시작한다: 같은 시각에 프레임 여러 개가 묶여 도착하면
        # 첫 점유 행보다 앞선 같은 시각의 행이 착석 중 EMPTY 로 잘못 잡힌다(2026-09-17 A1).
        for i, r in enumerate(rows):
            if i >= first_idx and r['t'] < seat_end:
                occ = states[i][gt] == 'O'
                if not occ:
                    seat_empty_t += dts[i]
                    if was_occ:
                        empty_ev += 1
                if folds[i]:
                    danger += dts[i]
                was_occ = occ
    out['seated_empty_ev'] = empty_ev
    out['seated_empty_t'] = seat_empty_t
    out['seated_fold_t'] = danger

    # 퇴장 후 해제
    rel_t = None
    if gt and exit_ is not None:
        for i, r in enumerate(rows):
            if r['t'] >= exit_ and states[i][gt] == 'E':
                rel_t = r['t']
                break
        if rel_t is None:
            # 측정이 끝날 때까지 해제되지 않음: 끝까지의 시간을 하한값으로 보고한다
            out['release'] = rows[-1]['t'] - exit_
            out['release_open'] = True
        else:
            out['release'] = rel_t - exit_

    # 비용: 오점유(비 GT 뒷좌 + GT 의 입장 전/해제 후), 오차단(뒷좌가 실제로 빈 구간에 폴딩 차단)
    false_occ = 0.0
    false_block = 0.0
    empty_t = 0.0
    for i, r in enumerate(rows):
        t = r['t']
        for n in rear:
            if states[i][n] != 'O':
                continue
            if n != gt:
                false_occ += dts[i]
            else:
                before = enter is not None and t < enter
                after = rel_t is not None and t >= rel_t
                if before or after:
                    false_occ += dts[i]
        truly_empty = (not gt) or (enter is not None and t < enter) or \
                      (exit_ is not None and t >= exit_)
        if truly_empty and (t - startT) >= warm:
            empty_t += dts[i]
            if not folds[i]:
                false_block += dts[i]
    out['false_occ_t'] = false_occ
    out['false_block_t'] = false_block
    out['empty_t'] = empty_t
    pas = [n for n in names if not n.startswith(prefix)]
    out['nonfold_occ_t'] = sum(dts[i] for i in range(len(rows))
                               for n in pas if states[i][n] == 'O')
    return out


def live_replay(meta, rows):
    """로그 메타에 적힌 라이브 설정(K, adaptive/R, 얼어붙은 트랙 F, EMPTY 확정 E)으로 재생.
    반환: (states, folds, startT, 필터 일치 (같은 칸, 전체 칸) 또는 None).
    필터 일치 = 분석기가 좌표로 다시 계산한 필터 후 트랙 근거가 라이브 기록(_trkf)과 같은 비율."""
    F = meta.get('freeze_sec')
    adp = bool(meta['adaptive'])
    E = None if adp else meta.get('empty_confirm_sec')
    names = meta['zone_names']
    ev, par = None, None
    if F is not None:
        ev, _ = track_evidence(meta, rows, F)
        if ev is not None and all(r['z'][n]['trkf'] is not None for r in rows for n in names):
            same = sum(1 for i, r in enumerate(rows) for n in names if ev[i][n][0] == r['z'][n]['trkf'])
            par = (same, len(rows) * len(names))
    st, fd, s0 = replay(meta, rows, adp, meta['presence_confirm'], meta['weak_release'], ev, 'A', E)
    return st, fd, s0, par


def evaluate(sessions, scheme, adaptive, K, R, F, E, ev_cache):
    """후보 하나를 전 세션에 재생해 합산. ev_cache[(세션번호, F)] = track_evidence 결과.
    퇴장해제는 e 마커가 실제로 있는 세션만 모은다(끝까지 앉아 있던 세션의 '해제 0초'가
    평균을 끌어내리던 문제, 2026-09-18 수정)."""
    agg = {'lat': None, 'ev': 0, 'et': 0.0, 'fold': 0.0, 'unsafe': 0, 'rel': [], 'rel_open': [],
           'focc': 0.0, 'fblk': 0.0, 'empty': 0.0}
    for si, s in enumerate(sessions):
        meta, rows = s['meta'], s['rows']
        ev = ev_cache.get((si, F)) if (F is not None or scheme == 'B') else None
        st, fd, s0 = replay(meta, rows, adaptive, K, R if R is not None else meta['weak_release'],
                            ev, scheme, E)
        m = metrics(meta, rows, st, fd, s0, s['gt'], s['enter'],
                    s['exit'] if s['gt'] else None)
        if s['gt']:
            if m.get('latency') is not None:
                agg['lat'] = m['latency'] if agg['lat'] is None else max(agg['lat'], m['latency'])
            agg['ev'] += m['seated_empty_ev']
            agg['et'] += m['seated_empty_t']
            agg['fold'] += m['seated_fold_t']
            agg['unsafe'] += int(m['seated_empty_ev'] > 0 or m['seated_fold_t'] > 0)
            if s.get('has_exit') and m.get('release') is not None:
                agg['rel'].append(m['release'])
                agg['rel_open'].append(bool(m.get('release_open')))
        agg['focc'] += m['false_occ_t']
        agg['fblk'] += m['false_block_t']
        agg['empty'] += m['empty_t']
    return agg


# ===========================================================================
# 끊김 분포
# ===========================================================================
def pct(vals, q):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = max(0, min(len(s) - 1, int(math.ceil(q * len(s))) - 1))
    return s[k]


def gap_analysis(meta, rows, gt, enter, exit_, frame_ms, entry_sec):
    min_pts = meta['min_points']
    use_fn = all(r['fn'] is not None for r in rows)
    idx = [i for i, r in enumerate(rows) if enter <= r['t'] <= exit_]
    evid = [i for i in idx if rows[i]['z'][gt]['trk'] or rows[i]['z'][gt]['n'] >= min_pts]
    trk = [i for i in idx if rows[i]['z'][gt]['trk']]

    def gaps_of(seq):
        g = []
        for a, b in zip(seq, seq[1:]):
            if use_fn:
                length = (rows[b]['fn'] - rows[a]['fn']) * frame_ms / 1000.0
                missing = rows[b]['fn'] - rows[a]['fn'] >= 2
            else:
                length = rows[b]['t'] - rows[a]['t']
                missing = length > frame_ms * 1.5 / 1000.0
            if missing:
                g.append((length, rows[a]['t'], rows[b]['t']))
        return g

    drops = 0
    if use_fn:
        for a, b in zip(rows, rows[1:]):
            if b['fn'] - a['fn'] > 1:
                drops += b['fn'] - a['fn'] - 1
    return {
        'n_frames': len(idx),
        'n_evid': len(evid),
        'first_evid': rows[evid[0]]['t'] - enter if evid else None,
        'gaps': gaps_of(evid),
        'trk_gaps': gaps_of(trk),
        'use_fn': use_fn,
        'drops': drops,
        'entry_end': enter + entry_sec,
    }


# ===========================================================================
# 정지 구간 (두 m 마커 사이)
# ===========================================================================
def still_analysis(meta, rows, gt, t0, t1, K, ev0, fr):
    """정지 착석 구간 [t0, t1] 의 탑승자 좌석 진단.
    ev0 = track_evidence(meta, rows, None) 결과, fr = frozen_tracks(meta, rows) 결과.

    실제 갱신 = 점 근거(최근 K*mult 프레임 중 K회) or 존 안 트랙 좌표 갱신.
    공백 = 실제 갱신 사이 시간. 구간 시작/끝과 첫/마지막 갱신 사이도 공백으로 센다.
    얼어붙은 트랙이 메운 시간 = 공백 시작부터 존 안 트랙(좌표 고정)이 끊김 없이 있던 시간.
      공백 전체를 메웠으면 공백 길이와 같다. B안에서 통합(F<=E)이면 E 가 공백보다 커야 하고,
      분리(F>E)면 이 시간만큼은 F 가 대신 메울 수 있다."""
    mult = meta.get('presence_window_mult', 4)
    min_pts = meta['min_points']
    hist = deque(maxlen=K * mult)
    inside = []                                   # (t, 실제갱신, 존안트랙있음, 행번호)
    for i, r in enumerate(rows):
        hist.append(r['z'][gt]['n'] >= min_pts)   # 창은 구간 밖에서부터 이어진다(라이브와 동일)
        if t0 <= r['t'] <= t1:
            trk_any, _, trk_fresh = ev0[i][gt]
            inside.append((r['t'], sum(hist) >= K or trk_fresh, trk_any, i))

    def gap(a, b, ka, kb):
        mid = inside[ka + 1:kb]
        end, full = a, bool(mid)
        for t, _, trk, _ in mid:
            if trk:
                end = t
            else:
                full = False
                break
        return (b - a, a, (b - a) if full else (end - a))

    gaps = []
    prev_t, prev_k = t0, -1
    for k, (t, ev, _, _) in enumerate(inside):
        if ev:
            gaps.append(gap(prev_t, t, prev_k, k))
            prev_t, prev_k = t, k
    gaps.append(gap(prev_t, t1, prev_k, len(inside)))
    worst_gap = max(gaps, key=lambda g: g[0])
    worst_cover = max(gaps, key=lambda g: g[2])

    # 일반 / fineMotion 프레임별 점
    idx = [x[3] for x in inside]
    fstat = {'nf': [0, 0, 0, 0, 0, 0], 'fm': [0, 0, 0, 0, 0, 0]}
    # [프레임, 점>=min 프레임, 존 점, 존 속도0 점, 전체 점, 전체 속도0 점]
    has_n0 = bool(idx) and all(rows[i]['z'][gt]['n0'] is not None for i in idx)
    has_p0 = bool(idx) and all(rows[i]['n_points0'] is not None and rows[i]['n_points'] is not None
                               for i in idx)
    for i in idx:
        r = rows[i]
        if r['fn'] is None:
            continue
        s = fstat['fm' if is_fm_frame(r['fn']) else 'nf']
        s[0] += 1
        s[1] += r['z'][gt]['n'] >= min_pts
        s[2] += r['z'][gt]['n']
        if has_n0:
            s[3] += r['z'][gt]['n0']
        if has_p0:
            s[4] += r['n_points']
            s[5] += r['n_points0']

    # 존 안 트랙 생성·소멸
    dts = durations(rows)
    zone_t = sum(dts[i] for i in idx if any(zn == gt for _, zn, _, _ in fr[i]))
    zone_ids = sorted(set(tid for i in idx for tid, zn, _, _ in fr[i] if zn == gt))
    start_ids = set(tid for tid, _, _, _ in fr[idx[0]]) if idx else set()
    end_ids = set(tid for tid, _, _, _ in fr[idx[-1]]) if idx else set()
    frozen = [(d, rows[i]['t'] - d) for i in idx for _, zn, d, _ in fr[i] if zn == gt]
    return {
        'dur': t1 - t0,
        'gap': worst_gap,                 # (길이, 시작 시각, 얼어붙은 트랙이 메운 시간)
        'cover': worst_cover,
        'gaps': gaps,
        'frames': fstat, 'has_n0': has_n0, 'has_p0': has_p0,
        'zone_track_ratio': zone_t / (t1 - t0) if t1 > t0 else 0.0,
        'zone_ids': zone_ids,
        'born': [tid for tid in zone_ids if tid not in start_ids],
        'gone': [tid for tid in zone_ids if tid not in end_ids],
        'frozen_max': max(frozen) if frozen else (0.0, None),
    }


def still_windows(s):
    """두 m 마커 사이를 정지 구간으로. m 이 정확히 2개가 아니면 None."""
    acts = [r['t'] for r in s['rows'] if 'act' in r['marker']]
    if len(acts) != 2:
        return None, len(acts)
    return (acts[0], acts[1]), 2


# ===========================================================================
# 출력
# ===========================================================================
def fmt(v, unit='s'):
    return '-' if v is None else '{:.2f}{}'.format(v, unit)


def episodes(meta, rows, zone):
    """라이브 기록 기준 점유 에피소드: [(시작, 끝, 트랙근거프레임, 점근거프레임, SNRmax)]"""
    eps, cur = [], None
    for i, r in enumerate(rows):
        o = r['z'][zone]['state'] == 'O'
        if o and cur is None:
            cur = [r['t'], r['t'], 0, 0, 0.0]
        if o:
            cur[1] = rows[i + 1]['t'] if i + 1 < len(rows) else r['t']
            cur[2] += r['z'][zone]['trk']
            cur[3] += r['z'][zone]['n'] >= meta['min_points']
            cur[4] = max(cur[4], r['z'][zone]['snrmax'])
        elif cur is not None:
            eps.append(tuple(cur))
            cur = None
    if cur:
        eps.append(tuple(cur))
    return eps


def resolve_session(path, a):
    """파일 하나를 읽어 세션 정보(dict)로. 문제가 있으면 (None, 사유)."""
    meta, rows = load(path)
    names = meta['zone_names']
    gt = a.gt if a.gt is not None else meta.get('gt')
    if isinstance(gt, str) and gt.lower() in ('none', '-', ''):
        gt = None
    if gt is not None and gt not in names:
        return None, 'GT 좌석 {} 이 존 목록 {} 에 없음'.format(gt, names)
    marks_sit = [r['t'] for r in rows if 'sit' in r['marker']]
    marks_stand = [r['t'] for r in rows if 'stand' in r['marker']]
    enter = marks_sit[0] if marks_sit else None
    exit_ = marks_stand[-1] if marks_stand else None
    if gt is None:
        if marks_sit or marks_stand:
            return None, 'GT 가 없는데 s/e 마커가 있음 -> --gt <좌석> 지정 필요'
        kind = '빈차'
    else:
        if enter is None:
            return None, ('GT={} 인데 입장(s) 마커가 없음 -> 빈 차 세션이면 --gt none, '
                          '아니면 다시 측정'.format(gt))
        if exit_ is None:
            exit_ = rows[-1]['t']          # 끝까지 앉아 있던 세션
        kind = '탑승'
    return {'path': path, 'name': os.path.basename(path), 'meta': meta, 'rows': rows,
            'gt': gt, 'enter': enter, 'exit': exit_, 'kind': kind,
            'has_exit': bool(marks_stand)}, None


def main(argv=None):
    ap = argparse.ArgumentParser(description='프레임 로그 기반 해제 시간/디바운스 캘리브레이션')
    ap.add_argument('csv', nargs='*', help='프레임 CSV (여러 개면 합산 비교)')
    ap.add_argument('--gt', default=None,
                    help='실제 착석 좌석. 기본: 각 파일 메타의 gt. none = 탑승자 없는 세션')
    ap.add_argument('--releases', default='1,1.5,2,2.5,3', help='WEAK 해제 후보(초)')
    ap.add_argument('--confirms', default='1,2,3', help='presence 디바운스 K 후보')
    ap.add_argument('--freezes', default='none',
                    help='얼어붙은 트랙 필터 후보(초). none=끔. 예: none,3,5,8,12')
    ap.add_argument('--schemes', default='A',
                    help='해제 방식 후보. A=현행(F 뒤에 E), B=마지막 실제 갱신부터 E. 예: A,B')
    ap.add_argument('--empty-confirms', default='4',
                    help='EMPTY 확정 대기 E 후보(초). 기존/B안에 적용(adaptive 는 자체 값). 예: 3,4')
    ap.add_argument('--frame-ms', type=float, default=55.0)
    ap.add_argument('--entry-sec', type=float, default=8.0, help='입장 구간으로 볼 길이(초)')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)

    if a.selftest:
        return _selftest()
    if not a.csv:
        ap.error('csv 경로가 필요합니다')
    # PowerShell/cmd 는 와일드카드를 펼치지 않으므로 직접 펼친다
    import glob
    paths = []
    for c in a.csv:
        hits = sorted(glob.glob(c)) if any(ch in c for ch in '*?[') else [c]
        if not hits:
            print('!! 일치하는 파일 없음: {}'.format(c))
        paths += hits
    a.csv = paths
    if not a.csv:
        return 2

    # ---------------- 세션 로드 ----------------
    sessions = []
    for path in a.csv:
        s, why = resolve_session(path, a)
        if s is None:
            print('!! 건너뜀 {}: {}'.format(os.path.basename(path), why))
        else:
            sessions.append(s)
    if not sessions:
        print('분석할 세션이 없습니다.')
        return 2

    # ---------------- [1] 세션별 요약 + 재생 검증 ----------------
    print('[1] 세션 요약 / 재생 검증')
    all_ok = True
    for s in sessions:
        meta, rows = s['meta'], s['rows']
        names = meta['zone_names']
        st, fd, s0, par = live_replay(meta, rows)
        total = len(rows) * len(names)
        ok = sum(1 for i, r in enumerate(rows) for n in names if st[i][n] == r['z'][n]['state'])
        fok = sum(1 for i, r in enumerate(rows) if int(fd[i]) == r['fold'])
        good = ok == total and fok == len(rows)
        all_ok &= good
        who = '{} {}~{}'.format(s['gt'], fmt(s['enter']), fmt(s['exit'])) if s['gt'] else '-'
        F = meta.get('freeze_sec')
        print('    {} {:<44s} {:>6.1f}s  GT {:<22s} 라이브(adaptive={} R={} K={} F={} E={})  재생 {:.2f}%/{:.2f}%{}'.format(
            'O' if good else 'X', s['name'], rows[-1]['t'] - rows[0]['t'], who,
            meta['adaptive'], meta['weak_release'], meta['presence_confirm'],
            '-' if F is None else '{:g}'.format(F), meta.get('empty_confirm_sec', cg.EMPTY_CONFIRM_SEC),
            100.0 * ok / total, 100.0 * fok / len(rows),
            '  필터 일치 {:.2f}%'.format(100.0 * par[0] / par[1]) if par else ''))
        if not good:
            for i, r in enumerate(rows):
                bad = [n for n in names if st[i][n] != r['z'][n]['state']]
                if bad or int(fd[i]) != r['fold']:
                    print('      첫 불일치 t={:.3f} 존={}'.format(r['t'], bad))
                    break
    if not all_ok:
        print('    !! 재생이 라이브와 다른 세션이 있어 아래 비교는 참고용입니다.')

    # ---------------- [2] 탑승 세션: 근거 끊김 ----------------
    occ_sessions = [s for s in sessions if s['kind'] == '탑승']
    all_gaps = []
    if occ_sessions:
        print('')
        print('[2] 탑승자 좌석 근거 끊김 (s 마커 ~ e 마커)')
        for s in occ_sessions:
            meta, rows, gt = s['meta'], s['rows'], s['gt']
            pre = [r['t'] for r in rows if s['enter'] - LATE_MARK_SEC <= r['t'] < s['enter']
                   and r['z'][gt]['state'] == 'O']
            g = gap_analysis(meta, rows, gt, s['enter'], s['exit'], a.frame_ms, a.entry_sec)
            lens = [x[0] for x in g['gaps']]
            ent = [x[0] for x in g['gaps'] if x[1] < g['entry_end']]
            sea = [x[0] for x in g['gaps'] if x[1] >= g['entry_end']]
            for x in g['gaps']:
                all_gaps.append((x[0], s['name'], x[1], '입장' if x[1] < g['entry_end'] else '착석'))
            print('    {:<44s} {}  근거 {}/{}  최대 {}  (입장 {} / 착석 {})  트랙만 최대 {}{}{}'.format(
                s['name'], gt, g['n_evid'], g['n_frames'],
                fmt(max(lens) if lens else 0.0), fmt(max(ent) if ent else None),
                fmt(max(sea) if sea else None),
                fmt(max(x[0] for x in g['trk_gaps']) if g['trk_gaps'] else None),
                '  프레임누락 {}'.format(g['drops']) if g['drops'] else '',
                '  [s 마커 전 {:.2f}초부터 이미 점유 -> s 가 늦었다면 다시 측정]'.format(
                    s['enter'] - pre[0]) if pre else ''))
            last_trk = [r['t'] for r in rows if s['enter'] <= r['t'] <= s['exit'] and r['z'][gt]['trk']]
            if last_trk and s['exit'] - last_trk[-1] > 3.0:
                print('      !! e 기준 {:.2f}s 인데 탑승자 좌석 트랙은 {:.2f}s 에 끝남 ({:.1f}초 전). '
                      'e 마커가 늦었다면 이 세션은 다시 측정하세요.'.format(
                          s['exit'], last_trk[-1], s['exit'] - last_trk[-1]))
        if all_gaps:
            lens = [x[0] for x in all_gaps]
            print('    ---')
            print('    전체 {}회  최대 {:.2f}s  p95 {:.2f}s  p99 {:.2f}s'.format(
                len(lens), max(lens), pct(lens, 0.95), pct(lens, 0.99)))
            print('    긴 끊김 상위 5:')
            for length, name, t0, ph in sorted(all_gaps, reverse=True)[:5]:
                print('      {:.2f}s  {} t={:.2f} ({})'.format(length, name, t0, ph))
            print('    -> WEAK 해제 R 은 입장 끊김 최대 {:.2f}s 보다 커야 트랙 없는 입장 순간에 풀리지 않습니다.'.format(
                max([x[0] for x in all_gaps if x[3] == '입장'] or [0.0])))

    # ---------------- [2b] 라이브 오점유 에피소드 ----------------
    print('')
    print('[2b] 라이브 기록의 뒷좌 오점유 에피소드')
    any_ep = False
    for s in sessions:
        meta, rows, gt = s['meta'], s['rows'], s['gt']
        prefix = meta.get('fold_zone_prefix', 'Rear')
        acts = [r['t'] for r in rows if 'act' in r['marker']]
        for n in meta['zone_names']:
            if not n.startswith(prefix):
                continue
            for st_, en_, tk, pr, sm in episodes(meta, rows, n):
                if n == gt and st_ >= s['enter'] and st_ < s['exit']:
                    continue                # 탑승자 본인의 점유
                any_ep = True
                prev_act = [(k + 1, t) for k, t in enumerate(acts) if t <= st_ + 0.5]
                act_txt = ''
                if prev_act:
                    k, t = prev_act[-1]
                    if st_ - t <= 20.0:
                        act_txt = '  <- 동작#{} 시작 {:+.1f}s 후'.format(k, st_ - t)
                print('    {:<44s} {:<7s} {:7.2f} -> {:7.2f} ({:5.2f}s) 트랙근거 {:4d} 점근거 {:4d} SNRmax {:.0f}{}'.format(
                    s['name'], n, st_, en_, en_ - st_, tk, pr, sm, act_txt))
    if not any_ep:
        print('    없음')

    # ---------------- [2c] 얼어붙은 트랙 지속시간 ----------------
    print('')
    print('[2c] 뒷좌 존 안 트랙이 좌표 고정된 최장 시간 (착석 중 탑승자 좌석 / 빈 구간 전체 뒷좌)')
    for s in sessions:
        meta, rows, gt = s['meta'], s['rows'], s['gt']
        fr, zones = frozen_tracks(meta, rows)
        if fr is None:
            print('    {:<44s} 메타에 존 좌표 정보(zones)가 없어 계산 불가'.format(s['name']))
            continue
        _, mism = track_evidence(meta, rows, None)
        prefix = meta.get('fold_zone_prefix', 'Rear')
        seat_best, empty_best = (0.0, None), (0.0, None)
        for r, cur in zip(rows, fr):
            t = r['t']
            seated = gt is not None and s['enter'] <= t < s['exit']
            for tid, zn, d, _ in cur:
                if zn is None or not zn.startswith(prefix):
                    continue
                if seated and zn == gt:
                    if d > seat_best[0]:
                        seat_best = (d, t - d)
                elif not seated:
                    if d > empty_best[0]:
                        empty_best = (d, t - d)
        print('    {:<44s} 착석 중 {:>6s}{:<9s}  빈 구간 {:>6s}{:<9s}  (존 판정 재계산 불일치 {}프레임, 손상 트랙 프레임 {})'.format(
            s['name'],
            fmt(seat_best[0]) if gt else '-',
            ' @{:.0f}s'.format(seat_best[1]) if seat_best[1] is not None else '',
            fmt(empty_best[0]),
            ' @{:.0f}s'.format(empty_best[1]) if empty_best[1] is not None else '',
            mism, corrupt_frames(rows)))

    confs = [int(x) for x in a.confirms.split(',') if x.strip()]

    # ---------------- [2d] 정지 구간 (두 m 마커 사이) ----------------
    still = [(s, still_windows(s)) for s in occ_sessions]
    print('')
    print('[2d] 정지 구간 (첫 m ~ 둘째 m, 탑승자 좌석, @+초 = 정지 시작 기준)')
    skipped = []
    for s, (win, n_act) in still:
        if win is None:
            skipped.append('{}(m {}개)'.format(s['name'], n_act))
            continue
        meta, rows, gt = s['meta'], s['rows'], s['gt']
        fr, _ = frozen_tracks(meta, rows)
        ev0, _ = track_evidence(meta, rows, None)
        if fr is None or ev0 is None:
            print('    {:<44s} 메타에 존 좌표 정보(zones)가 없어 계산 불가'.format(s['name']))
            continue
        t0, t1 = win
        print('    {}  {}  정지 {:.1f}s (s{:+.1f} ~ s{:+.1f})'.format(
            s['name'], gt, t1 - t0, t0 - s['enter'], t1 - s['enter']))
        res = {K: still_analysis(meta, rows, gt, t0, t1, K, ev0, fr) for K in confs}
        print('      실제 갱신 공백 최장       ' + ' | '.join(
            'K={} {} @+{:.1f} (얼어붙은 트랙이 메움 {})'.format(
                K, fmt(r['gap'][0]), r['gap'][1] - t0, fmt(r['gap'][2])) for K, r in res.items()))
        print('      얼어붙은 트랙만 남은 최장 ' + ' | '.join(
            'K={} {} @+{:.1f} (그 공백 {})'.format(
                K, fmt(r['cover'][2]), r['cover'][1] - t0, fmt(r['cover'][0])) for K, r in res.items()))
        r = res[confs[0]]
        nf, fm = r['frames']['nf'], r['frames']['fm']

        def ratio(a_, b_):
            return '{}/{} ({:.1f}%)'.format(a_, b_, 100.0 * a_ / b_ if b_ else 0.0)
        print('      점 {}개 이상 프레임        일반 {}   fineMotion {}'.format(
            meta['min_points'], ratio(nf[1], nf[0]), ratio(fm[1], fm[0])))
        if r['has_n0']:
            print('      존 안 점 중 속도 0         일반 {}   fineMotion {}'.format(
                ratio(nf[3], nf[2]), ratio(fm[3], fm[2])))
        else:
            print('      존 안 점 중 속도 0         기록 없음 (속도 0 열 추가 전 로그)')
        if r['has_p0']:
            print('      프레임 전체 점 중 속도 0   일반 {}   fineMotion {}'.format(
                ratio(nf[5], nf[4]), ratio(fm[5], fm[4])))
        fz = r['frozen_max']
        print('      존 안 트랙                 있던 시간 {:.1f}%  ID {}  구간 중 생성 {} / 사라짐 {}  좌표 고정 최장 {}{}'.format(
            100.0 * r['zone_track_ratio'], ' '.join('#{}'.format(t) for t in r['zone_ids']) or '-',
            len(r['born']), len(r['gone']), fmt(fz[0]),
            ' @+{:.1f}'.format(fz[1] - t0) if fz[1] is not None else ''))
    if skipped:
        print('    제외(m 마커가 2개가 아님): ' + ', '.join(skipped))
    if not any(w is not None for _, (w, _) in still):
        print('    정지 구간 세션 없음')

    # ---------------- [3] 후보 비교 (전 세션 합산) ----------------
    rels = [float(x) for x in a.releases.split(',') if x.strip()]
    Es = [float(x) for x in a.empty_confirms.split(',') if x.strip()]
    schemes = [x.strip().upper() for x in a.schemes.split(',') if x.strip()]
    cands = []                                     # (라벨, 방식, adaptive, K, R, E)
    for sc in schemes:
        if sc == 'A':
            cands += [('기존', 'A', False, k, None, E) for E in Es for k in confs]
            cands += [('adaptive R={:g}'.format(R), 'A', True, k, R, None) for R in rels for k in confs]
        elif sc == 'B':
            cands += [('B안', 'B', False, k, None, E) for E in Es for k in confs]
        else:
            print('!! 알 수 없는 해제 방식 {} (A 또는 B)'.format(sc))
    live_keys = set()
    for s in sessions:
        m_ = s['meta']
        f_ = m_.get('freeze_sec')
        if m_['adaptive']:
            live_keys.add((True, m_['presence_confirm'], m_['weak_release'], None, f_))
        else:
            live_keys.add((False, m_['presence_confirm'], None,
                           float(m_.get('empty_confirm_sec', cg.EMPTY_CONFIRM_SEC)), f_))

    n_occ, n_emp = len(occ_sessions), len(sessions) - len(occ_sessions)
    print('')
    print('[3] 후보 비교 (탑승 {}세션 + 빈차 {}세션, 같은 기록을 재생해 합산)'.format(n_occ, n_emp))
    freezes = []
    for x in a.freezes.split(','):
        x = x.strip().lower()
        freezes.append(None if x in ('none', '-', '') else float(x))
    need = set(f for f in freezes if f is not None)
    if any(c[1] == 'B' for c in cands):
        need.add(None)                             # B안은 필터가 꺼져 있어도 좌표 갱신 여부가 필요
    ev_cache = {}
    for si, s in enumerate(sessions):
        for f in need:
            ev, _ = track_evidence(s['meta'], s['rows'], f)
            if ev is None:
                print('    !! {} 에 존 좌표 정보가 없어 얼어붙은 트랙 필터·B안을 평가할 수 없습니다.'.format(s['name']))
                freezes = [None]
                cands = [c for c in cands if c[1] == 'A']
                break
            ev_cache[(si, f)] = ev
        else:
            continue
        break
    head = '    {:<16s} {:>1s} {:>4s} {:>4s} | {:>8s} {:>12s} {:>8s} {:>4s} | {:>13s} | {:>8s} {:>17s}'.format(
        '구성', 'K', 'F', 'E', '최악입장', '착석중EMPTY', '착석폴딩', '불안전', '퇴장해제 최대/평균', '오점유', '오차단 (빈시간대비)')
    print(head)
    print('    ' + '-' * 118)
    for F, (label, sc, adp, K, R, E) in [(f, c) for f in freezes for c in cands]:
        agg = evaluate(sessions, sc, adp, K, R, F, E, ev_cache)
        rel = agg['rel']
        rel_max_txt = '-'
        if rel:
            k = max(range(len(rel)), key=lambda j: rel[j])
            rel_max_txt = fmt(rel[k]) + ('*' if agg['rel_open'][k] else '')
        tag = ' <- 라이브' if (sc == 'A' and (adp, K, R, E, F) in live_keys) else ''
        rate = 100.0 * agg['fblk'] / agg['empty'] if agg['empty'] > 0 else 0.0
        print('    {:<16s} {:>1d} {:>4s} {:>4s} | {:>8s} {:>4d}회/{:>6s} {:>8s} {:>4d} | {:>6s}/{:>6s} | {:>8s} {:>8s} ({:4.1f}%) {}{}'.format(
            label, K, '-' if F is None else '{:g}s'.format(F), '-' if E is None else '{:g}s'.format(E),
            fmt(agg['lat']), agg['ev'], fmt(agg['et']), fmt(agg['fold']),
            agg['unsafe'], rel_max_txt,
            fmt(sum(rel) / len(rel) if rel else None), fmt(agg['focc']), fmt(agg['fblk']), rate,
            'X' if agg['unsafe'] else 'O', tag))
    print('')
    print('    구성         : 기존 = 현행 상태머신(A안), adaptive = 근거 강도별 해제(A안), B안 = 마지막 실제 갱신부터 E')
    print('    F            : 얼어붙은 트랙 필터. 좌표가 F초 이상 고정된 트랙은 트랙 근거에서 제외(- = 끔). 점 근거는 그대로')
    print('    E            : EMPTY 확정 대기. A안은 근거가 끊긴 뒤부터, B안은 마지막 실제 갱신부터 센다(- = adaptive 자체 값)')
    print('    최악입장     : 탑승 세션 중 가장 늦은 첫 점유(s 마커 기준, 음수 = 마커보다 먼저 잡음)')
    print('    착석중EMPTY  : 첫 점유 ~ 퇴장 사이 탑승자 좌석이 비었다고 판정된 횟수/시간. 0 이어야 안전')
    print('    착석폴딩     : 같은 구간에 폴딩이 허용된 시간. 0 이어야 안전. 불안전 = 둘 중 하나라도 발생한 세션 수')
    print('    퇴장해제     : e 마커 ~ 탑승자 좌석 EMPTY. e 마커가 있는 세션만. * = 측정이 끝날 때까지 해제 안 됨(실제로는 더 김)')
    print('    오점유       : 탑승자 좌석이 아닌 뒷좌 점유 + 탑승자 좌석의 입장 전/해제 후 점유')
    print('    오차단       : 뒷좌가 실제로 비어 있는데 폴딩이 막힌 시간(퇴장 후 해제 지연 포함), 괄호는 빈 시간 대비 비율')
    return 0


# ===========================================================================
# 셀프테스트: 라이브 모니터 + 재호출로 CSV 를 만들고, 재생이 100% 일치하는지 확인
# ===========================================================================
def _selftest():
    import random
    import tempfile
    from frame_logger import FrameLogger
    from presence_monitor import PresenceClearanceMonitor
    from track_parser import Track
    from pointcloud_parser import Point

    random.seed(7)
    seats = cg.SEATS_PLACEHOLDER
    names = [z.name for z in seats]

    def center(n):
        z = [q for q in seats if q.name == n][0]
        return (z.xmin + z.xmax) / 2, (z.ymin + z.ymax) / 2

    lx, ly = center('Rear-L')
    cx, cy = center('Rear-C')
    FR = 0.055
    ENTER, EXIT, END = 10.0, 40.0, 55.0
    GAP = (12.0, 13.2)               # 입장 직후 1.2초 근거 끊김(오늘 실측 1.1초와 비슷)

    def frame_at(t):
        tracks, pts = [], []
        if ENTER <= t < EXIT and not (GAP[0] <= t < GAP[1]):
            if t > 14.0 and random.random() < 0.7:
                tracks.append(Track(5, lx + random.uniform(-0.02, 0.02),
                                    ly + random.uniform(-0.02, 0.02), 0.9))
            if t <= 14.0 or random.random() < 0.6:
                pts += [Point(lx, ly, 0.9, 0.0, 20) for _ in range(random.randint(3, 12))]
        if abs(t - 25.0) < FR / 2:
            pts += [Point(cx, cy, 0.9, 0.0, 9) for _ in range(4)]       # 단발
        if 30.0 <= t < 30.0 + 3 * FR:
            pts += [Point(cx, cy, 0.9, 0.0, 9) for _ in range(4)]       # 3프레임 버스트
        return tracks, pts

    path = os.path.join(tempfile.gettempdir(), 'analyze_frames_selftest.csv')
    ok_all = True
    for adaptive, R in ((True, 1.0), (False, None)):
        mon = PresenceClearanceMonitor(seats, adaptive=adaptive,
                                       weak_release=R if adaptive else None)
        meta = {'zone_names': names, 'zones': 'placeholder', 'gt': 'Rear-L', 'min_points': mon.min_points,
                'presence_confirm': mon.presence_confirm, 'presence_window_mult': 4,
                'adaptive': adaptive, 'weak_release': R if adaptive else 1.0,
                'weak_hold': 0.3, 'strong_track_sec': 0.5, 'strong_sustain_sec': 3.0,
                'warmup_block_sec': cg.WARMUP_BLOCK_SEC, 'fold_zone_prefix': 'Rear'}
        log = FrameLogger(path, names, meta)
        now, fn, next_frame = 0.0, 100, 0.0
        last_t, last_p = [], []
        while now < END:
            if now >= next_frame:                      # 가끔 두 프레임이 한 번에 도착
                batch = 2 if random.random() < 0.1 else 1
                for _ in range(batch):
                    tr, pt = frame_at(next_frame)
                    last_t, last_p = tr, pt
                    res = mon.update(tr, pt, now)
                    if abs(next_frame - ENTER) < FR / 2:
                        log.mark('sit')
                    if abs(next_frame - EXIT) < FR / 2:
                        log.mark('stand')
                    log.log_frame(now, fn, tr, pt, res, mon.fold_permitted(now))
                    fn += 1
                    if random.random() < 0.02:         # 프레임 누락
                        fn += 1
                    next_frame += FR
            else:
                mon.update(last_t, last_p, now, new_frame=False)
                log.note_stale(now)
            now += random.uniform(0.004, 0.03)         # 루프 회전 간격
        log.close()

        meta2, rows = load(path)
        st, fd, s0 = replay(meta2, rows, adaptive, 1, R if adaptive else 1.0)
        total = len(rows) * len(names)
        ok = sum(1 for i, r in enumerate(rows) for n in names if st[i][n] == r['z'][n]['state'])
        fok = sum(1 for i, r in enumerate(rows) if int(fd[i]) == r['fold'])
        label = 'adaptive R=1.0' if adaptive else '기존'
        print('OK' if ok == total and fok == len(rows) else 'FAIL',
              ': {} 재생 일치 {}/{} , 폴딩 {}/{}'.format(label, ok, total, fok, len(rows)))
        ok_all &= (ok == total and fok == len(rows))

    # 끊김 검출 + 후보 비교가 기대대로 나오는지
    meta2, rows = load(path)                              # 마지막(기존) 로그
    enter = next(r['t'] for r in rows if 'sit' in r['marker'])
    exit_ = [r['t'] for r in rows if 'stand' in r['marker']][-1]
    g = gap_analysis(meta2, rows, 'Rear-L', enter, exit_, 55.0, 8.0)
    mx = max(x[0] for x in g['gaps'])
    print('OK' if 1.1 <= mx <= 1.4 else 'FAIL', ': 입장 끊김 최대 {:.2f}s 검출 (심은 값 1.2s)'.format(mx))
    ok_all &= 1.1 <= mx <= 1.4

    res = {}
    for R in (1.0, 2.0):
        s, f, s0 = replay(meta2, rows, True, 1, R)
        res[R] = metrics(meta2, rows, s, f, s0, 'Rear-L', enter, exit_)
    c1 = res[1.0]['seated_empty_ev'] >= 1
    c2 = res[2.0]['seated_empty_ev'] == 0
    print('OK' if c1 else 'FAIL', ': R=1.0 은 입장 끊김에서 착석중 EMPTY {}회'.format(res[1.0]['seated_empty_ev']))
    print('OK' if c2 else 'FAIL', ': R=2.0 은 착석중 EMPTY {}회'.format(res[2.0]['seated_empty_ev']))
    ok_all &= c1 and c2

    s1, f1, t1 = replay(meta2, rows, True, 1, 1.0)
    s3, f3, t3 = replay(meta2, rows, True, 3, 1.0)
    m1 = metrics(meta2, rows, s1, f1, t1, 'Rear-L', enter, exit_)
    m3 = metrics(meta2, rows, s3, f3, t3, 'Rear-L', enter, exit_)
    c3 = m3['false_occ_t'] < m1['false_occ_t']
    print('OK' if c3 else 'FAIL', ': K=3 이 Rear-C 단발을 걸러 오점유 감소 ({:.2f}s -> {:.2f}s)'.format(
        m1['false_occ_t'], m3['false_occ_t']))
    ok_all &= c3

    # ---- 여러 세션 합산 + 빈 차 세션 ----
    import contextlib
    path2 = os.path.join(tempfile.gettempdir(), 'analyze_frames_selftest_empty.csv')
    mon = PresenceClearanceMonitor(seats, adaptive=True, weak_release=1.0)
    meta_e = {'zone_names': names, 'zones': 'placeholder', 'gt': None, 'min_points': mon.min_points,
              'presence_confirm': 1, 'presence_window_mult': 4, 'adaptive': True,
              'weak_release': 1.0, 'weak_hold': 0.3, 'strong_track_sec': 0.5,
              'strong_sustain_sec': 3.0, 'warmup_block_sec': cg.WARMUP_BLOCK_SEC,
              'fold_zone_prefix': 'Rear'}
    log = FrameLogger(path2, names, meta_e)
    now, fn, nf = 0.0, 1, 0.0
    lt, lp = [], []
    while now < 40.0:
        if now >= nf:
            pts = []
            if 15.0 <= nf < 15.0 + 2 * FR or 28.0 <= nf < 29.0:     # 빈 차 오검출 2회
                pts = [Point(cx, cy, 0.9, 0.0, 8) for _ in range(4)]
            lt, lp = [], pts
            res = mon.update([], pts, now)
            log.log_frame(now, fn, [], pts, res, mon.fold_permitted(now))
            fn += 1
            nf += FR
        else:
            mon.update(lt, lp, now, new_frame=False)
            log.note_stale(now)
        now += random.uniform(0.004, 0.03)
    log.close()

    class _A:                     # resolve_session 이 쓰는 인자만
        gt = None
    sess, why = resolve_session(path2, _A)
    c4 = sess is not None and sess['kind'] == '빈차'
    print('OK' if c4 else 'FAIL', ': 마커 없는 gt=None 세션을 빈 차로 인식')
    ok_all &= c4
    m2, r2 = load(path2)
    s_, f_, t_ = replay(m2, r2, True, 1, 1.0)
    me = metrics(m2, r2, s_, f_, t_, None, None, None)
    c5 = me['false_occ_t'] > 0.5 and me['empty_t'] > 30.0 and me['seated_empty_ev'] == 0
    print('OK' if c5 else 'FAIL', ': 빈 차 세션 오점유 {:.2f}s / 빈 시간 {:.1f}s 집계'.format(
        me['false_occ_t'], me['empty_t']))
    ok_all &= c5

    class _B:
        gt = 'Rear-L'
    sess, why = resolve_session(path2, _B)
    c6 = sess is None and '마커' in why
    print('OK' if c6 else 'FAIL', ': GT 가 있는데 마커가 없으면 건너뛰고 이유 표시')
    ok_all &= c6

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main([path, path2, '--confirms', '1'])
    txt = buf.getvalue()
    c7 = rc == 0 and '탑승 1세션 + 빈차 1세션' in txt and 'adaptive R=2' in txt
    print('OK' if c7 else 'FAIL', ': 탑승+빈차 두 파일 합산 비교 실행')
    ok_all &= c7

    # ---- 얼어붙은 트랙 필터: 퇴장 후 고정 유령만 걸러지고 착석 중엔 영향 없음 ----
    path3 = os.path.join(tempfile.gettempdir(), 'analyze_frames_selftest_frozen.csv')
    mon = PresenceClearanceMonitor(seats)                      # 기존 4.0s, K=1
    meta_f = dict(meta_e, gt='Rear-L', adaptive=False)
    log = FrameLogger(path3, names, meta_f)
    now, fn, nf = 0.0, 1, 0.0
    lt, lp = [], []
    while now < 70.0:
        if now >= nf:
            tr, pts = [], []
            if 8.0 <= nf < 30.0:                                 # 앉아 있는 사람: 좌표가 계속 변함
                tr = [Track(5, lx + random.uniform(-0.03, 0.03), ly + random.uniform(-0.03, 0.03), 0.9)]
                if random.random() < 0.5:
                    pts = [Point(lx, ly, 0.9, 0.0, 20) for _ in range(5)]
            elif nf >= 30.0:                                     # 퇴장 후 유령: 좌표 고정, 점 없음
                tr = [Track(9, lx, ly, 0.9)]
            lt, lp = tr, pts
            res = mon.update(tr, pts, now)
            if abs(nf - 8.0) < FR / 2:
                log.mark('sit')
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
    m3, r3 = load(path3)
    e3 = next(r['t'] for r in r3 if 'sit' in r['marker'])
    x3 = next(r['t'] for r in r3 if 'stand' in r['marker'])
    ev0, mism = track_evidence(m3, r3, None)
    c8 = mism == 0
    print('OK' if c8 else 'FAIL', ': 트랙 좌표로 재계산한 존 판정이 기록과 일치 (불일치 {})'.format(mism))
    ok_all &= c8
    s_, f_, t_ = replay(m3, r3, False, 1, 1.0)
    mn = metrics(m3, r3, s_, f_, t_, 'Rear-L', e3, x3)
    ev3, _ = track_evidence(m3, r3, 3.0)
    s_, f_, t_ = replay(m3, r3, False, 1, 1.0, ev3)
    mf = metrics(m3, r3, s_, f_, t_, 'Rear-L', e3, x3)
    c9 = bool(mn.get('release_open')) and not mf.get('release_open') and 5.0 <= mf['release'] <= 9.0
    print('OK' if c9 else 'FAIL', ': 필터 끔 -> 퇴장 후 {:.1f}s 까지 미해제, F=3s -> {:.2f}s 에 해제'.format(
        mn['release'], mf['release']))
    ok_all &= c9
    c10 = mf['seated_empty_ev'] == 0 and mf['seated_fold_t'] == 0
    print('OK' if c10 else 'FAIL', ': F=3s 에서도 착석 중 EMPTY 0회 (좌표가 변하는 트랙은 유지)')
    ok_all &= c10
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main([path3, '--confirms', '1', '--releases', '', '--freezes', 'none,3'])
    txt = buf.getvalue()
    c11 = rc == 0 and '[2c]' in txt and '  3s   4s |' in txt
    print('OK' if c11 else 'FAIL', ': --freezes 후보 비교표 출력')
    ok_all &= c11

    # ---- B안: 조용한 시간을 마지막 실제 갱신부터 센다 ----
    #   path3: 퇴장(30s) 순간 유령 트랙이 새로 나타나 좌표 고정, 점 없음
    def release_of(scheme, F, E):
        ev, _ = track_evidence(m3, r3, F)
        s_, f_, t_ = replay(m3, r3, False, 1, 1.0, ev, scheme, E)
        return metrics(m3, r3, s_, f_, t_, 'Rear-L', e3, x3)
    mb1 = release_of('B', 1.0, 4.0)
    ma1 = release_of('A', 1.0, 4.0)
    mb6 = release_of('B', 6.0, 4.0)
    c12 = (3.8 <= mb1['release'] <= 4.3 and 4.8 <= ma1['release'] <= 5.3 and
           5.8 <= mb6['release'] <= 6.3)
    print('OK' if c12 else 'FAIL', ': 유령 해제 A안 F=1 {:.2f}s (F+E) / B안 F=1 {:.2f}s (E) / B안 F=6 {:.2f}s (F)'.format(
        ma1['release'], mb1['release'], mb6['release']))
    ok_all &= c12
    c13 = mb1['seated_empty_ev'] == 0 and mb1['seated_fold_t'] == 0
    print('OK' if c13 else 'FAIL', ': B안 F=1 에서도 착석 중 EMPTY 0회')
    ok_all &= c13
    ma2 = release_of('A', 1.0, 2.0)
    c14 = 2.8 <= ma2['release'] <= 3.3 and cg.EMPTY_CONFIRM_SEC == 4.0
    print('OK' if c14 else 'FAIL', ': A안 E=2 후보 재생 {:.2f}s, 재생 뒤 EMPTY_CONFIRM_SEC 원복 {}'.format(
        ma2['release'], cg.EMPTY_CONFIRM_SEC))
    ok_all &= c14

    # ---- 정지 구간(두 m 사이) 진단 + 속도 0 점 열 ----
    def make_still(p, mark_exit):
        mon = PresenceClearanceMonitor(seats)
        log = FrameLogger(p, names, dict(meta_e, gt='Rear-L', adaptive=False))
        now, fn, nf = 0.0, 1, 0.0
        lt, lp = [], []
        while now < 60.0:
            if now >= nf:
                tr, pts = [], []
                if 5.0 <= nf < 45.0:
                    if 20.0 <= nf < 21.5:                   # 얼어붙은 트랙만, 점 없음
                        tr = [Track(5, lx + 0.01, ly + 0.01, 0.9)]
                    elif 21.5 <= nf < 22.0 or 30.0 <= nf < 30.8:
                        pass                                 # 아무 근거 없음
                    else:
                        tr = [Track(5, lx + random.uniform(-0.03, 0.03), ly + random.uniform(-0.03, 0.03), 0.9)]
                        v = 0.0 if is_fm_frame(fn) else 0.4  # fineMotion 프레임 점만 속도 0
                        pts = [Point(lx, ly, 0.9, v, 20) for _ in range(4)]
                lt, lp = tr, pts
                res = mon.update(tr, pts, now)
                for tm, lab in ((5.0, 'sit'), (10.0, 'act'), (40.0, 'act')) + (((45.0, 'stand'),) if mark_exit else ()):
                    if abs(nf - tm) < FR / 2:
                        log.mark(lab)
                log.log_frame(now, fn, tr, pts, res, mon.fold_permitted(now))
                fn += 1
                nf += FR
            else:
                mon.update(lt, lp, now, new_frame=False)
                log.note_stale(now)
            now += random.uniform(0.004, 0.03)
        log.close()

    path4 = os.path.join(tempfile.gettempdir(), 'analyze_frames_selftest_still.csv')
    path5 = os.path.join(tempfile.gettempdir(), 'analyze_frames_selftest_still_noexit.csv')
    make_still(path4, True)
    make_still(path5, False)
    s4, _ = resolve_session(path4, _A)
    s4['gt'] = 'Rear-L'
    win, _ = still_windows(s4)
    fr4, _ = frozen_tracks(s4['meta'], s4['rows'])
    ev4, _ = track_evidence(s4['meta'], s4['rows'], None)
    st4 = still_analysis(s4['meta'], s4['rows'], 'Rear-L', win[0], win[1], 1, ev4, fr4)
    g, cv = st4['gap'], st4['cover']
    # 심은 공백 2.0s 중 점은 20.0s 에 끊기지만, K=1 도 창이 4프레임이라 점 근거가 3프레임(0.17s) 더 이어진다.
    #   프레임 격자(55ms)로는 마지막 점 근거 20.13s, 근거 복귀 22.0~22.055s, 얼어붙은 트랙 마지막 21.45s
    #   -> 공백 약 1.87~1.93s, 얼어붙은 트랙이 메운 시간 약 1.32s (+ 루프 지터 최대 0.03s)
    c15 = win is not None and 1.82 <= g[0] <= 1.98 and 1.27 <= g[2] <= 1.37 and abs(cv[2] - g[2]) < 1e-9
    print('OK' if c15 else 'FAIL', ': 정지 구간 공백 최장 {:.2f}s (기대 약 1.9s), 얼어붙은 트랙이 메운 시간 {:.2f}s (기대 약 1.32s)'.format(
        g[0], g[2]))
    ok_all &= c15
    nf_, fm_ = st4['frames']['nf'], st4['frames']['fm']
    c16 = (st4['has_n0'] and st4['has_p0'] and fm_[2] > 0 and fm_[3] == fm_[2] and nf_[2] > 0 and nf_[3] == 0
           and fm_[5] == fm_[3] and nf_[5] == 0)
    print('OK' if c16 else 'FAIL', ': 속도 0 점 열 기록·분류 (fineMotion 프레임 {}/{}, 일반 {}/{})'.format(
        fm_[3], fm_[2], nf_[3], nf_[2]))
    ok_all &= c16

    # 이전 로그(속도 0 열 없음)도 읽힌다
    with io.open(path4, encoding='utf-8') as f:
        lines = f.read().splitlines()
    metas = [ln for ln in lines if ln.startswith('# ')]
    table = list(csv.reader([ln for ln in lines if not ln.startswith('# ')]))
    keep = [k for k, h in enumerate(table[0]) if not (h == 'n_points0' or h.endswith('_n0'))]
    old = os.path.join(tempfile.gettempdir(), 'analyze_frames_selftest_old.csv')
    with io.open(old, 'w', encoding='utf-8', newline='') as f:
        f.write('\n'.join(metas) + '\n')
        w = csv.writer(f)
        for row in table:
            w.writerow([row[k] for k in keep])
    so, _ = resolve_session(old, _B)
    fro, _ = frozen_tracks(so['meta'], so['rows'])
    evo, _ = track_evidence(so['meta'], so['rows'], None)
    sto = still_analysis(so['meta'], so['rows'], 'Rear-L', win[0], win[1], 1, evo, fro)
    c17 = so['rows'][0]['z']['Rear-L']['n0'] is None and not sto['has_n0'] and abs(sto['gap'][0] - g[0]) < 1e-9
    print('OK' if c17 else 'FAIL', ': 속도 0 열이 없는 이전 로그도 분석 (공백 결과 동일)')
    ok_all &= c17

    # 퇴장해제 평균은 e 마커가 있는 세션만
    s5, _ = resolve_session(path5, _B)
    s4b, _ = resolve_session(path4, _B)
    agg = evaluate([s4b, s5], 'A', False, 1, None, None, None, {})
    c18 = s4b['has_exit'] and not s5['has_exit'] and len(agg['rel']) == 1
    print('OK' if c18 else 'FAIL', ': 퇴장해제는 e 마커 있는 세션만 집계 ({}개)'.format(len(agg['rel'])))
    ok_all &= c18

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main([path4, path3, '--confirms', '1,2', '--releases', '', '--freezes', 'none,1',
                   '--schemes', 'A,B', '--empty-confirms', '3,4'])
    txt = buf.getvalue()
    c19 = (rc == 0 and '[2d]' in txt and '실제 갱신 공백 최장' in txt and 'B안' in txt and
           '제외(m 마커가 2개가 아님)' in txt)
    print('OK' if c19 else 'FAIL', ': --schemes A,B / --empty-confirms / [2d] 출력')
    ok_all &= c19

    # ---- 손상 프레임: 얼어붙은 유령이 한 프레임 빠졌다 같은 좌표로 돌아와도 '갱신'이 아니다 ----
    path6 = os.path.join(tempfile.gettempdir(), 'analyze_frames_selftest_corrupt.csv')
    mon = PresenceClearanceMonitor(seats)
    log = FrameLogger(path6, names, dict(meta_e, gt='Rear-L', adaptive=False))
    now, fn, nf = 0.0, 1, 0.0
    lt, lp = [], []
    while now < 50.0:
        if now >= nf:
            tr, pts = [], []
            skip = False
            if 8.0 <= nf < 20.0:
                tr = [Track(5, lx + random.uniform(-0.03, 0.03), ly + random.uniform(-0.03, 0.03), 0.9)]
                pts = [Point(lx, ly, 0.9, 0.4, 20) for _ in range(4)]
            elif nf >= 20.0:
                tr = [Track(9, lx, ly, 0.9)]                     # 퇴장 후 얼어붙은 유령
                if abs(nf - 22.0) < FR / 2:                      # 손상 프레임: 이상한 트랙만, 다음 번호 빠짐
                    tr = [Track(1589318287, 0.0, -1.57e31, -5.86e31)]
                    skip = True
                if abs(nf - 23.0) < FR / 2:                      # 트랙 목록이 빈 손상 프레임
                    tr = []
                    skip = True
            lt, lp = tr, pts
            res = mon.update(tr, pts, now)
            if abs(nf - 8.0) < FR / 2:
                log.mark('sit')
            if abs(nf - 20.0) < FR / 2:
                log.mark('stand')
            log.log_frame(now, fn, tr, pts, res, mon.fold_permitted(now))
            fn += 2 if skip else 1
            nf += FR
        else:
            mon.update(lt, lp, now, new_frame=False)
            log.note_stale(now)
        now += random.uniform(0.004, 0.03)
    log.close()
    m6, r6 = load(path6)
    x6 = next(r['t'] for r in r6 if 'stand' in r['marker'])
    e6 = next(r['t'] for r in r6 if 'sit' in r['marker'])
    fr6, _ = frozen_tracks(m6, r6)
    at25 = next(cur for r, cur in zip(r6, fr6) if r['t'] >= x6 + 5.0)
    d9 = [d for tid, _, d, chg in at25 if tid == 9]
    back = [chg for r, cur in zip(r6, fr6) if x6 + 2.0 < r['t'] < x6 + 2.3 for tid, _, _, chg in cur if tid == 9]
    ev6, _ = track_evidence(m6, r6, 2.0)
    s_, f_, t_ = replay(m6, r6, False, 1, 1.0, ev6, 'A', 4.0)
    mc = metrics(m6, r6, s_, f_, t_, 'Rear-L', e6, x6)
    c20 = (corrupt_frames(r6) == 1 and d9 and d9[0] >= 4.9 and back and not any(back)
           and 5.8 <= mc['release'] <= 6.3)
    print('OK' if c20 else 'FAIL', ': 손상 프레임 {}개 무시, 유령 고정 시간 {:.2f}s 로 이어짐, A안 F=2 해제 {:.2f}s (F+E)'.format(
        corrupt_frames(r6), d9[0] if d9 else -1, mc['release']))
    ok_all &= c20

    # ---- 라이브 필터(F=3, E=3, K=2)로 기록한 로그를 오프라인 재생하면 100% 일치 ----
    path7 = os.path.join(tempfile.gettempdir(), 'analyze_frames_selftest_livefilter.csv')
    old_e = cg.EMPTY_CONFIRM_SEC
    cg.EMPTY_CONFIRM_SEC = 3.0                                   # main --empty-confirm 3 과 같은 방식
    try:
        mon = PresenceClearanceMonitor(seats, presence_confirm=2, freeze_sec=3.0)
        meta_l = dict(meta_e, gt='Rear-L', adaptive=False, presence_confirm=2,
                      empty_confirm_sec=cg.EMPTY_CONFIRM_SEC, freeze_sec=mon.freeze_sec)
        log = FrameLogger(path7, names, meta_l)
        now, fn, nf = 0.0, 1, 0.0
        lt, lp = [], []
        while now < 45.0:
            if now >= nf:
                tr, pts = [], []
                skip = False
                if 8.0 <= nf < 25.0:                                 # 착석: 좌표가 변하다 가끔 멈춤, 점은 간헐
                    frozen = 15.0 <= nf < 17.5                      # 2.5s 멈춤 (F=3 보다 짧음)
                    jit = 0.0 if frozen else random.uniform(-0.03, 0.03)
                    tr = [Track(5, lx + 0.02 + jit, ly, 0.9)]
                    if random.random() < 0.4:
                        pts = [Point(lx, ly, 0.9, 0.0 if is_fm_frame(fn) else 0.4, 20) for _ in range(4)]
                elif nf >= 25.0:
                    tr = [Track(9, lx, ly, 0.9)]                     # 퇴장 후 유령(좌표 고정, 점 없음)
                    if abs(nf - 27.0) < FR / 2:
                        tr = [Track(1589318287, 0.0, -1.57e31, 2.0)]   # 손상 프레임
                        skip = True
                lt, lp = tr, pts
                res = mon.update(tr, pts, now)
                if abs(nf - 8.0) < FR / 2:
                    log.mark('sit')
                if abs(nf - 25.0) < FR / 2:
                    log.mark('stand')
                log.log_frame(now, fn, tr, pts, res, mon.fold_permitted(now))
                fn += 2 if skip else 1
                nf += FR
            else:
                mon.update(lt, lp, now, new_frame=False)
                log.note_stale(now)
            now += random.uniform(0.004, 0.03)
        log.close()
    finally:
        cg.EMPTY_CONFIRM_SEC = old_e
    m7, r7 = load(path7)
    st7, fd7, t07, par7 = live_replay(m7, r7)
    tot7 = len(r7) * len(names)
    ok7 = sum(1 for i, r in enumerate(r7) for n in names if st7[i][n] == r['z'][n]['state'])
    fok7 = sum(1 for i, r in enumerate(r7) if int(fd7[i]) == r['fold'])
    c21 = ok7 == tot7 and fok7 == len(r7) and par7 is not None and par7[0] == par7[1]
    print('OK' if c21 else 'FAIL', ': 라이브 필터 로그 재생 일치 {}/{}, 폴딩 {}/{}, 필터 일치 {}'.format(
        ok7, tot7, fok7, len(r7), par7))
    ok_all &= c21
    e7 = next(r['t'] for r in r7 if 'sit' in r['marker'])
    x7 = next(r['t'] for r in r7 if 'stand' in r['marker'])
    mm7 = metrics(m7, r7, st7, fd7, t07, 'Rear-L', e7, x7)
    c22 = (mm7['seated_empty_ev'] == 0 and mm7['seated_fold_t'] == 0 and 5.8 <= mm7['release'] <= 6.3
           and cg.EMPTY_CONFIRM_SEC == old_e)
    print('OK' if c22 else 'FAIL', ': F=3/E=3 착석 중 EMPTY 0회(2.5s 멈춤은 유지), 유령 해제 {:.2f}s (F+E=6s, 손상 프레임 무관)'.format(
        mm7['release']))
    ok_all &= c22

    os.remove(path)
    os.remove(path2)
    os.remove(path3)
    for p in (path4, path5, old, path6, path7):
        os.remove(p)
    print('')
    print('all analyze_frames selftests passed' if ok_all else '!! SELFTEST FAILED')
    return 0 if ok_all else 1


if __name__ == '__main__':
    sys.exit(main())
