"""
PhysioNet drivedb 로더 (스캐폴딩용 리허설 데이터).

drivedb는 원래 '스트레스' 데이터셋이라 alert/drowsy 라벨이 없다. 여기서는
파이프라인이 도는지 확인하기 위해, 각 레코드에서 '안정(rest) 구간 vs 주행(active)
구간'을 두 상태(0/1)로 라벨링하는 리허설을 한다.
  -> Healey&Picard 프로토콜: 처음/끝은 정지 휴식, 가운데는 시내/고속 주행.
     여기선 단순화해서 [앞 12% 구간 = 0(rest)], [중앙 40% 구간 = 1(active)] 로 잡는다.

UL-DD가 승인되면 이 파일의 load_record()만 UL-DD용으로 교체하면 된다
(RR/HR 시계열 + 진짜 alert/drowsy 라벨을 반환하도록).
"""
import os

import numpy as np
import wfdb

from features import rr_series_from_resp, resample_to


def load_record(rec_name, pn_dir='drivedb', local_dir=None):
    """한 레코드 -> (t, rr_series, hr_series, label_series) 또는 None(채널 없으면).

    local_dir 주면 그 폴더의 캐시 파일을 읽고, 없으면 PhysioNet에서 원격 다운로드.
    label: 0=rest(안정), 1=active(주행). 리허설용 pseudo-label.
    """
    try:
        if local_dir:
            rec = wfdb.rdrecord(os.path.join(local_dir, rec_name))
        else:
            rec = wfdb.rdrecord(rec_name, pn_dir=pn_dir)
    except Exception as e:
        print('  [skip] %s: %s' % (rec_name, e))
        return None
    names = rec.sig_name
    if 'RESP' not in names or 'HR' not in names:
        print('  [skip] %s: RESP/HR 채널 없음 (%s)' % (rec_name, names))
        return None

    fs = rec.fs
    resp = rec.p_signal[:, names.index('RESP')]
    hr_raw = rec.p_signal[:, names.index('HR')]
    hr_t = np.arange(len(hr_raw)) / fs

    # RESP -> RR 시계열
    t_rr, rr = rr_series_from_resp(resp, fs)
    if len(t_rr) < 10:
        print('  [skip] %s: RR 추정 실패' % rec_name)
        return None
    hr = resample_to(t_rr, hr_t, hr_raw)

    # pseudo-label: 앞 12%=rest(0), 중앙 30~70%=active(1), 나머지=제외(-1)
    total = t_rr[-1]
    label = np.full(len(t_rr), -1, dtype=int)
    label[t_rr < 0.12 * total] = 0
    label[(t_rr > 0.35 * total) & (t_rr < 0.70 * total)] = 1
    return t_rr, rr, hr, label


def load_dataset(records=None, pn_dir='drivedb', local_dir=None):
    """여러 레코드 -> (t, rr, hr, label, group) 시계열 병합. group=subject 인덱스."""
    if records is None:
        records = wfdb.get_record_list(pn_dir)
    T, RR, HR, LAB, GRP = [], [], [], [], []
    for gi, rname in enumerate(records):
        print('[load] %s ...' % rname)
        out = load_record(rname, pn_dir, local_dir=local_dir)
        if out is None:
            continue
        t, rr, hr, lab = out
        T.append(t); RR.append(rr); HR.append(hr); LAB.append(lab)
        GRP.append(np.full(len(t), gi))
    if not T:
        raise RuntimeError('로드된 레코드가 없습니다.')
    return (np.concatenate(T), np.concatenate(RR), np.concatenate(HR),
            np.concatenate(LAB), np.concatenate(GRP))
