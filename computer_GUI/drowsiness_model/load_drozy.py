"""
DROZY 로더 — ECG에서 HR·RR 추출 + KSS 졸림 라벨. (진짜 졸음 학습용)

DROZY는 ECG만 있고 호흡 채널이 없어, RR은 EDR(ECG-derived respiration)로 유도한다.
파일명 N-M.edf = subject N, test M. KSS.txt[N-1][M-1] = 그 세션의 졸림 점수(1~9, 0=결측).

라벨(이진): KSS<=LOW=각성(0), KSS>=HIGH=졸림(1), 중간(4~6)/결측은 제외.
group = subject 번호 (같은 사람이 학습/테스트에 동시에 들어가지 않게).

load_dataset()이 load_drivedb와 '같은 형태' (t, rr, hr, label, group)를 반환하므로
features.py / train_drowsiness.py는 그대로 재사용된다.

필요 패키지: pyedflib, neurokit2
"""
import glob
import os
import warnings

import numpy as np

from features import rr_series_from_resp, resample_to

warnings.filterwarnings('ignore')

KSS_LOW = 3    # 이하 = 각성
KSS_HIGH = 7   # 이상 = 졸림


def read_kss(drozy_dir):
    """KSS.txt -> rows[subject-1][test-1] = 점수."""
    rows = []
    with open(os.path.join(drozy_dir, 'KSS.txt')) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append([int(x) for x in line.split()])
    return rows


def kss_to_label(kss):
    if kss <= 0:
        return -1              # 결측
    if kss <= KSS_LOW:
        return 0               # 각성
    if kss >= KSS_HIGH:
        return 1               # 졸림
    return -1                  # 중간(애매) 제외


def load_ecg(edf_path):
    import pyedflib
    edf = pyedflib.EdfReader(edf_path)
    idx = edf.getSignalLabels().index('ECG')
    fs = int(edf.getSampleFrequency(idx))
    sig = edf.readSignal(idx)
    edf.close()
    return sig, fs


def load_record(edf_path, kss):
    """한 세션(.edf) -> (t, rr, hr, label) 또는 None."""
    import neurokit2 as nk
    label_val = kss_to_label(kss)
    if label_val < 0:
        return None
    ecg, fs = load_ecg(edf_path)
    try:
        sig, _ = nk.ecg_process(ecg, sampling_rate=fs)   # R-peak, 순시 HR
    except Exception as e:
        print('  [skip] %s: ecg_process 실패(%s)' % (os.path.basename(edf_path), e))
        return None
    hr_full = sig['ECG_Rate'].values                     # 순시 심박(bpm) @ fs
    edr = nk.ecg_rsp(hr_full, sampling_rate=fs)           # EDR: HR에서 호흡 파형 유도
    t_rr, rr = rr_series_from_resp(edr, fs)               # 호흡 파형 -> RR 시계열
    if len(t_rr) < 10:
        return None
    hr_t = np.arange(len(hr_full)) / fs
    hr = resample_to(t_rr, hr_t, hr_full)                 # HR을 RR 시각에 맞춤
    label = np.full(len(t_rr), label_val, dtype=int)      # 세션 전체가 한 라벨
    return t_rr, rr, hr, label


def load_dataset(drozy_dir, subjects=None):
    """DROZY 전체(또는 일부 subject) -> (t, rr, hr, label, group)."""
    kss_table = read_kss(drozy_dir)
    edfs = sorted(glob.glob(os.path.join(drozy_dir, 'psg', '*.edf')))
    T, RR, HR, LAB, GRP = [], [], [], [], []
    for path in edfs:
        base = os.path.splitext(os.path.basename(path))[0]   # 'N-M'
        try:
            subj, test = map(int, base.split('-'))
        except ValueError:
            continue
        if subjects and subj not in subjects:
            continue
        kss = kss_table[subj - 1][test - 1]
        lab = kss_to_label(kss)
        print('[load] %-5s subj%-2d test%d KSS=%d -> %s'
              % (base, subj, test, kss,
                 {-1: '제외', 0: '각성', 1: '졸림'}[lab]))
        if lab < 0:
            continue
        out = load_record(path, kss)
        if out is None:
            continue
        t, rr, hr, l = out
        T.append(t); RR.append(rr); HR.append(hr); LAB.append(l)
        GRP.append(np.full(len(t), subj))      # group = subject
    if not T:
        raise RuntimeError('로드된 세션이 없습니다.')
    return (np.concatenate(T), np.concatenate(RR), np.concatenate(HR),
            np.concatenate(LAB), np.concatenate(GRP))
