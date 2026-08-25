"""
Exp4 (346명 데이터셋 중 수면박탈 실험) 로더.

DROZY와 달리 이 데이터셋은 raw ECG가 아니라 neurokit로 '이미 계산된 특징'을 준다.
1 row = 1 subject x 1 period(Rural/Urban), _Bl(주행 전 baseline)/_Dr(주행 중)/_Dr-Bl(변화량) 3벌.

라벨: KSSscenario1/2 = KSS_B_x - KSS_(x-1) (그 period 동안 졸림이 실제로 변한 양).
      order_scenario로 Rural/Urban 중 어느 게 scenario1/2였는지 매핑됨(검증: 63/63 일치).
      본 로더는 raw KSSscenario 값(정수, 음수 가능)과 이진 라벨(1=졸림 증가) 둘 다 반환.

레이더로 뽑을 수 있는 특징만 사용(라디오파로는 R-R 원시 간격을 못 재므로 HRV_* 정밀지표는 제외):
  ECG_Rate_Mean, RSP_Rate_Mean, RSP_Amplitude_Mean 의 _Bl/_Dr/_Dr-Bl.
  (RRV/HRV류는 정밀 IBI 필요 -> IWR6843 5FPS로는 불가, 비교용으로만 별도 로드 가능)
"""
import csv
import os

import numpy as np

RADAR_FEASIBLE = [
    'ECG_Rate_Mean', 'RSP_Rate_Mean', 'RSP_Amplitude_Mean',
]
RICH_HRV = [  # 참고용(레이더 불가, ECG 정밀 IBI 필요) - 비교 실험용
    'HRV_RMSSD', 'HRV_SDNN', 'HRV_LFHF', 'RRV_RMSSD',
]


def _subject_num(participant_code):
    return participant_code.split('_')[0]  # "01_AC16" -> "01"


def load_kss_map(exp4_dir):
    """participant_code 숫자 -> {'order':1/2, 'kss1':int, 'kss2':int} 딕셔너리."""
    path = os.path.join(exp4_dir, 'Preprocessed', 'Questionnaire', 'Exp4_Database.csv')
    out = {}
    with open(path, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            try:
                sid = _subject_num(row['participant_code'])
                out[sid] = {
                    'order': int(row['order_scenario']),
                    'kss1': int(row['KSSscenario1']),
                    'kss2': int(row['KSSscenario2']),
                }
            except (ValueError, KeyError):
                continue
    return out


def kss_delta_for_period(kss_row, period):
    """period(Rural/Urban)에 해당하는 KSS 변화량(정수) 반환.
    order=1 -> scenario1=Rural, scenario2=Urban / order=2 -> 반대 (검증됨 63/63)."""
    if kss_row['order'] == 1:
        return kss_row['kss1'] if period == 'Rural' else kss_row['kss2']
    else:
        return kss_row['kss2'] if period == 'Rural' else kss_row['kss1']


def load_dataset(exp4_dir, feature_set='radar', segm_file='features_segm_1.csv',
                 pos_thresh=1, neg_thresh=0):
    """(X, y, groups, feature_names) 반환.

    feature_set: 'radar'(레이더로 가능한 특징만) 또는 'rich'(HRV 정밀지표 포함, 비교용)
    라벨: delta_kss >= pos_thresh -> 1(졸림 증가), delta_kss <= neg_thresh -> 0(비증가).
          그 사이(0<delta<pos_thresh)는 애매하여 제외.
    """
    names = RADAR_FEASIBLE if feature_set == 'radar' else RADAR_FEASIBLE + RICH_HRV
    cols = [n + '_Dr-Bl' for n in names]  # 변화량(주행중 - 주행전 baseline) 컬럼만 사용

    kss_map = load_kss_map(exp4_dir)
    seg_path = os.path.join(exp4_dir, 'Preprocessed', 'Physio', 'periods', segm_file)

    X, y, G, deltas = [], [], [], []
    skipped_no_kss = skipped_mid = skipped_nan = 0
    with open(seg_path, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            sid = row['subject_id']
            period = row['period']
            if sid not in kss_map:
                skipped_no_kss += 1
                continue
            delta = kss_delta_for_period(kss_map[sid], period)

            if delta >= pos_thresh:
                label = 1
            elif delta <= neg_thresh:
                label = 0
            else:
                skipped_mid += 1
                continue

            try:
                feats = [float(row[c]) for c in cols]
            except (ValueError, KeyError):
                skipped_nan += 1
                continue
            if any(np.isnan(v) for v in feats):
                skipped_nan += 1
                continue

            X.append(feats); y.append(label); G.append(sid); deltas.append(delta)

    print('[load_exp4] 사용 %d행 | 제외: KSS없음 %d, 중간값 %d, 특징결측 %d'
          % (len(X), skipped_no_kss, skipped_mid, skipped_nan))
    return np.array(X), np.array(y), np.array(G), cols, np.array(deltas)


def load_trend_dataset(exp4_dir, feature_set='radar',
                       window_file='features_window_180s_overlap_0.csv',
                       pos_thresh=1, neg_thresh=0):
    """30분 period 안의 시계열(_Dr, 여러 time window)에서 '추세(기울기)'를 뽑아
    (X, y, groups, feature_names, deltas) 반환.

    _Bl은 period 내내 고정값(첫 5분 baseline 반복)이라 시계열이 아니지만,
    _Dr은 window(segment_id)마다 실제로 다른 값 -> 진짜 시계열이다.
    각 subject-period에서 _Dr 값을 시간(time_start)에 대해 선형회귀하여
    slope(추세), mean, std를 특징으로 사용한다.
    """
    names = RADAR_FEASIBLE if feature_set == 'radar' else RADAR_FEASIBLE + RICH_HRV
    dr_cols = [n + '_Dr' for n in names]

    kss_map = load_kss_map(exp4_dir)
    win_path = os.path.join(exp4_dir, 'Preprocessed', 'Physio', 'windows', window_file)

    # (subject, period) -> list of (time_start, {col: val})
    groups_data = {}
    with open(win_path, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            key = (row['subject_id'], row['period'])
            try:
                t = float(row['time_start'])
                vals = {c: float(row[c]) for c in dr_cols}
            except (ValueError, KeyError):
                continue
            if any(np.isnan(v) for v in vals.values()):
                continue
            groups_data.setdefault(key, []).append((t, vals))

    feat_names = []
    for n in names:
        feat_names += [n + '_slope', n + '_mean', n + '_std']

    X, y, G, deltas = [], [], [], []
    skipped_no_kss = skipped_mid = skipped_short = 0
    for (sid, period), series in groups_data.items():
        if sid not in kss_map:
            skipped_no_kss += 1
            continue
        if len(series) < 3:            # 추세 추정엔 최소 몇 개 점 필요
            skipped_short += 1
            continue
        series.sort(key=lambda z: z[0])
        ts = np.array([t for t, _ in series])

        row_feats = []
        for c in dr_cols:
            vs = np.array([v[c] for _, v in series])
            slope = np.polyfit(ts, vs, 1)[0]     # 시간에 따른 변화율
            row_feats += [slope, vs.mean(), vs.std()]

        delta = kss_delta_for_period(kss_map[sid], period)
        if delta >= pos_thresh:
            label = 1
        elif delta <= neg_thresh:
            label = 0
        else:
            skipped_mid += 1
            continue

        X.append(row_feats); y.append(label); G.append(sid); deltas.append(delta)

    print('[load_exp4:trend] 사용 %d행 | 제외: KSS없음 %d, 중간값 %d, 창부족 %d'
          % (len(X), skipped_no_kss, skipped_mid, skipped_short))
    return np.array(X), np.array(y), np.array(G), feat_names, np.array(deltas)
