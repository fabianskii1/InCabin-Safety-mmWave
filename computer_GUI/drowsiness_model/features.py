"""
특징 추출 계약(contract) — 공개 데이터셋과 레이더가 '똑같이' 만들어야 하는 특징.

입력: 시간축 위의 RR(호흡수) 추정 시계열 + HR(심박수) 시계열
출력: 슬라이딩 윈도우마다 [rr_mean, rr_std, hr_mean, hr_std] (+ 파생)

이 4개가 핵심 계약이다. drivedb/UL-DD 학습도, 레이더 추론도 전부 이 함수로 특징을 만든다.
=> 학습(공개데이터)과 추론(레이더)의 입력 형태가 자동으로 일치.

주의: 졸음은 '개인 대비 변화'라 per-subject 정규화(z-score)가 중요.
      normalize_per_subject()를 학습 전에 그룹별로 적용할 것.
"""
import numpy as np

FEATURE_NAMES = ['rr_mean', 'rr_std', 'hr_mean', 'hr_std', 'rr_cv', 'hr_cv']


def rr_series_from_resp(resp, fs, sub_win=30.0, hop=5.0, band=(0.1, 0.5)):
    """호흡 파형(RESP) -> RR(bpm) 추정 시계열. sub_win초 창을 hop초씩 슬라이딩하며 FFT 피크.

    레이더는 칩이 RR을 직접 주지만, drivedb는 호흡 '파형'이라 여기서 RR로 변환한다.
    반환: (t_series, rr_series)  — hop 간격의 RR 추정값.
    """
    from scipy.signal import detrend
    n_sub = int(sub_win * fs)
    n_hop = int(hop * fs)
    ts, rrs = [], []
    for start in range(0, len(resp) - n_sub, n_hop):
        seg = detrend(resp[start:start + n_sub])
        w = seg * np.hanning(len(seg))
        spec = np.abs(np.fft.rfft(w))
        freqs = np.fft.rfftfreq(len(seg), d=1.0 / fs)
        m = (freqs >= band[0]) & (freqs <= band[1])
        if not np.any(m):
            continue
        peak = freqs[m][np.argmax(spec[m])]
        ts.append((start + n_sub / 2) / fs)
        rrs.append(peak * 60.0)
    return np.array(ts), np.array(rrs)


def resample_to(t_ref, t_src, v_src):
    """v_src(t_src)를 t_ref 시각으로 선형보간. (HR 채널을 RR 시각에 맞추는 용도)"""
    if len(t_src) == 0:
        return np.full_like(t_ref, np.nan, dtype=float)
    return np.interp(t_ref, t_src, v_src)


def window_features(t, rr, hr, win_sec=60.0, hop_sec=20.0):
    """RR/HR 시계열 -> 윈도우 특징 행들. t는 rr/hr와 같은 시각 축(초).

    반환: (X, t_centers)  X.shape=(n_windows, len(FEATURE_NAMES))
    """
    if len(t) < 2:
        return np.zeros((0, len(FEATURE_NAMES))), np.array([])
    dt = np.median(np.diff(t))
    n_win = max(int(win_sec / dt), 2)
    n_hop = max(int(hop_sec / dt), 1)
    rows, centers = [], []
    for s in range(0, len(t) - n_win + 1, n_hop):
        rr_w = rr[s:s + n_win]
        hr_w = hr[s:s + n_win]
        rr_w = rr_w[np.isfinite(rr_w)]
        hr_w = hr_w[np.isfinite(hr_w)]
        if len(rr_w) < 3 or len(hr_w) < 3:
            continue
        rr_m, rr_s = rr_w.mean(), rr_w.std()
        hr_m, hr_s = hr_w.mean(), hr_w.std()
        rows.append([
            rr_m, rr_s, hr_m, hr_s,
            rr_s / rr_m if rr_m > 1e-6 else 0.0,   # 변동계수(CV)
            hr_s / hr_m if hr_m > 1e-6 else 0.0,
        ])
        centers.append(t[s] + win_sec / 2)
    return np.array(rows), np.array(centers)


def normalize_per_subject(X, groups):
    """subject(group)별로 z-score 정규화. 개인차(기본 HR/RR 절대값 차이) 제거.
    주의: 각성+졸림을 통째로 정규화하면 상태 차이까지 지워질 수 있음
    (라벨이 세션 단위인 DROZY에선 normalize_to_baseline을 쓸 것)."""
    Xn = X.astype(float).copy()
    for g in np.unique(groups):
        m = groups == g
        mu = Xn[m].mean(axis=0)
        sd = Xn[m].std(axis=0)
        sd[sd < 1e-6] = 1.0
        Xn[m] = (Xn[m] - mu) / sd
    return Xn


def normalize_to_baseline(X, y, groups, baseline_label=0):
    """각 subject의 '각성(baseline_label)' 윈도우를 기준선으로 삼아 z-score.

    실차 시스템의 '운전자 각성 상태 보정'과 동일한 개념:
      각성일 때의 그 사람 HR/RR을 0점으로 잡고, 거기서 얼마나 벗어났는지로 본다.
    => 사람마다 다른 절대 HR/RR을 제거하면서, 상태 차이(각성 대비 졸림)는 보존.
       subject 간에도 비교 가능해진다.
    """
    Xn = X.astype(float).copy()
    for g in np.unique(groups):
        m = groups == g
        base = m & (y == baseline_label)
        if base.sum() >= 3:                 # 각성 기준 윈도우 충분
            mu = X[base].mean(axis=0)
            sd = X[base].std(axis=0)
        else:                               # 각성 세션 없으면 전체 평균으로 대체
            mu = X[m].mean(axis=0)
            sd = X[m].std(axis=0)
        sd[sd < 1e-6] = 1.0
        Xn[m] = (X[m] - mu) / sd
    return Xn
