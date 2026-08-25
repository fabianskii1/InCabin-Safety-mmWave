"""
DD-Database (Dryad doi:10.5061/dryad.5tb2rbp9c) 로더.

[이 데이터셋을 쓰는 이유]
DROZY/Exp4는 특징이 이미 굵은 단위(세션당 1개 / 최소 180초)로 뭉개져 있어,
우리 실시간 코드의 20초 창과 스케일이 맞지 않았다(그래서 학습된 모델을 그대로
배포할 수 없었고 규칙 기반으로 대체했다). DD-Database는 raw ECG(128Hz, 2시간 연속)
이므로 실시간 코드와 '동일한 창 길이'로 직접 잘라 특징을 만들 수 있다.
=> 학습/배포 간 스케일 불일치가 원천적으로 없다.

구성: 10명 x 2세션(각 2시간). 4 EEG + 2 EOG + 1 ECG(EKG) @128Hz.
라벨: 피험자가 '졸리다고 느낄 때' 직접 누른 이벤트 버튼의 타임스탬프(annotations edf).

라벨링 방식(이 로더):
  - 이벤트 시각 이전 PRE_EVENT_SEC 구간의 창 -> 1 (졸림 임박)
  - 어떤 이벤트로부터도 FAR_EVENT_SEC 이상 떨어진 창 -> 0 (평상시)
  - 그 사이(애매 구간)는 제외
주의: EEG/EOG는 사용하지 않는다(레이더로 취득 불가). ECG만 사용해 HR을 복원하며,
      호흡(RR) 채널이 없으므로 이 데이터셋으로는 HR 계열 특징만 검증 가능하다.

필요 패키지: pyedflib, neurokit2
"""
import glob
import os
import re
import warnings

import numpy as np

warnings.filterwarnings('ignore')

WINDOW_SEC = 20.0        # 실시간 코드(drowsiness_feature_logger)와 동일한 창 길이
HOP_SEC = 10.0           # 창 이동 간격
PRE_EVENT_SEC = 60.0     # 이벤트 직전 60초 내 창 = 졸림 임박(1)
FAR_EVENT_SEC = 300.0    # 모든 이벤트에서 300초 이상 떨어진 창 = 평상시(0)
MIN_EVENTS = 10          # 이벤트가 이보다 적은 피험자는 제외(라벨 신뢰 어려움)

FEATURE_NAMES = ['hr_mean', 'hr_std', 'hr_slope']


def list_sessions(dd_dir):
    """(subject, trial, signal_path, annot_path) 목록."""
    out = []
    for path in sorted(glob.glob(os.path.join(dd_dir, '*.edf'))):
        base = os.path.basename(path)
        if 'annotations' in base:
            continue
        m = re.match(r'(\d{2})([MF])_(\d)\.edf$', base)
        if not m:
            continue
        subj, gender, trial = m.group(1), m.group(2), m.group(3)
        annot = os.path.join(dd_dir, '%s%s_%s_annotations.edf' % (subj, gender, trial))
        if os.path.exists(annot):
            out.append((subj, trial, path, annot))
    return out


def read_events(annot_path):
    """annotation edf -> 이벤트 시각(초) 배열."""
    import pyedflib
    edf = pyedflib.EdfReader(annot_path)
    onsets, _dur, _desc = edf.readAnnotations()
    edf.close()
    return np.asarray([float(o) for o in onsets])


def ecg_to_hr_series(signal_path, sampling_limit_sec=None):
    """ECG(EKG 채널) -> (t_sec, hr_bpm) 시계열. neurokit2로 R-peak 기반 순시 HR 복원."""
    import pyedflib
    import neurokit2 as nk

    edf = pyedflib.EdfReader(signal_path)
    labels = edf.getSignalLabels()
    idx = next((i for i, l in enumerate(labels) if 'EKG' in l.upper() or 'ECG' in l.upper()), None)
    if idx is None:
        edf.close()
        return None, None
    fs = int(edf.getSampleFrequency(idx))
    sig = edf.readSignal(idx)
    edf.close()

    if sampling_limit_sec:
        sig = sig[:int(fs * sampling_limit_sec)]
    try:
        signals, _info = nk.ecg_process(sig, sampling_rate=fs)
    except Exception as e:
        print('  [skip] ecg_process 실패: %s' % e)
        return None, None
    hr = signals['ECG_Rate'].values          # 샘플마다의 순시 HR(bpm)
    t = np.arange(len(hr)) / fs
    return t, hr


def label_for_window(w_start, w_end, events):
    """창 [w_start, w_end)에 라벨 부여. 1=졸림 임박, 0=평상시, -1=애매(제외)."""
    if len(events) == 0:
        return -1
    # 창 끝 시점 이후 가장 가까운 이벤트까지의 거리
    ahead = events[events >= w_end]
    dist_ahead = (ahead[0] - w_end) if len(ahead) else np.inf
    # 창과 가장 가까운 이벤트까지의 거리(앞/뒤 모두)
    nearest = np.min(np.abs(events - (w_start + w_end) / 2.0))

    if dist_ahead <= PRE_EVENT_SEC:
        return 1
    if nearest >= FAR_EVENT_SEC:
        return 0
    return -1


def load_dataset(dd_dir, window_sec=WINDOW_SEC, hop_sec=HOP_SEC, limit_sec=None,
                 verbose=True, cache_path=None):
    """(X, y, groups, feature_names) 반환. groups=subject(문자열).

    cache_path 지정 시, 무거운 ECG R-peak 처리 결과(X,y,G)를 npz로 저장/재사용한다.
    특징 정규화 방식을 여러 번 실험할 때 매번 ECG를 다시 처리하지 않기 위함
    (같은 (window_sec, hop_sec, limit_sec) 조합에서만 캐시가 유효).
    """
    if cache_path and os.path.exists(cache_path):
        d = np.load(cache_path, allow_pickle=True)
        if (float(d['window_sec']) == window_sec and float(d['hop_sec']) == hop_sec
                and str(d['limit_sec']) == str(limit_sec)):
            if verbose:
                print('[cache] %s 에서 로드 (%d개 창)' % (cache_path, len(d['X'])))
            return d['X'], d['y'], d['G'], list(d['names'])

    sessions = list_sessions(dd_dir)
    X, y, G = [], [], []
    for subj, trial, sig_path, annot_path in sessions:
        events = read_events(annot_path)
        if len(events) < MIN_EVENTS:
            if verbose:
                print('[skip] %s_%s: 이벤트 %d개(<%d)' % (subj, trial, len(events), MIN_EVENTS))
            continue
        t, hr = ecg_to_hr_series(sig_path, sampling_limit_sec=limit_sec)
        if t is None:
            continue

        n_win = n_pos = n_neg = 0
        total = t[-1]
        w = 0.0
        while w + window_sec <= total:
            lab = label_for_window(w, w + window_sec, events)
            if lab >= 0:
                m = (t >= w) & (t < w + window_sec)
                hw, tw = hr[m], t[m]
                if len(hw) >= 5 and np.all(np.isfinite(hw)):
                    slope = float(np.polyfit(tw, hw, 1)[0])   # 실시간 코드와 동일 방식
                    X.append([hw.mean(), hw.std(), slope])
                    y.append(lab); G.append(subj)
                    n_win += 1
                    n_pos += (lab == 1); n_neg += (lab == 0)
            w += hop_sec
        if verbose:
            print('[load] %s_%s: 이벤트 %2d개 -> 창 %4d개 (졸림 %3d / 평상 %3d)'
                  % (subj, trial, len(events), n_win, n_pos, n_neg))

    X, y, G = np.array(X), np.array(y), np.array(G)
    if cache_path:
        np.savez(cache_path, X=X, y=y, G=G, names=np.array(FEATURE_NAMES),
                 window_sec=window_sec, hop_sec=hop_sec, limit_sec=str(limit_sec))
        if verbose:
            print('[cache] %s 에 저장' % cache_path)
    return X, y, G, FEATURE_NAMES


def add_baseline_delta_features(X, G, y, names, baseline_label=0):
    """subject별 baseline_label(기본 0=평상시) 창들의 중앙값을 그 사람의 기준선으로 삼아,
    각 특징의 '기준선 대비 변화량'을 추가 특징으로 만든다. (DROZY/Exp4에서 반복 확인된
    개인차 문제 대응 - 여기서도 절대값이 baseline보다 낮은 성능을 보여 시도한다.)
    반환: (X_delta, delta_names) - 원본 X와 같은 shape, 각 열이 '그 사람 기준선 대비 변화량'.
    """
    X_delta = np.zeros_like(X, dtype=float)
    for g in np.unique(G):
        m = G == g
        base_mask = m & (y == baseline_label)
        if base_mask.sum() < 3:
            base_mask = m   # 평상시 창이 너무 적으면 그 사람 전체 중앙값으로 대체
        ref = np.median(X[base_mask], axis=0)
        X_delta[m] = X[m] - ref
    delta_names = [n + '_delta' for n in names]
    return X_delta, delta_names
