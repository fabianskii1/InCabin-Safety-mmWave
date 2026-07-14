"""
[A안] 졸음 특징(feature) 실시간 로거 — firmware 수정 없이, 현재 5FPS 출력 그대로 사용.

칩이 주는 것: HR/RR 추정치(+confidence). 진짜 beat-to-beat HRV는 아니지만,
슬라이딩 윈도우에서 아래 특징을 뽑으면 졸음 초기 지표가 된다.
  · RR 평균, RR 변동성(std)     : 졸리면 호흡이 느려지고 불규칙해짐
  · HR 평균, HR 변동성(std)     : HR 추정치의 흔들림(약식 HRV proxy)
  · baseline 대비 변화량         : 개인차 제거(주행 초기 baseline 기준)

[HR/RR 칼럼 자동 식별]
  이 펌웨어 빌드의 TLV10 필드 배치가 파서 가정과 다를 수 있어, 값의 '범위'로 찾는다.
  - HR 추정치: 대략 40~120 bpm 범위에서 진동
  - RR 추정치: 대략 6~35 rpm 범위에서 진동
  가장 그 범위 안에 오래 머무는 칼럼을 각각 HR/RR로 택한다.
  (--hr-col / --rr-col 로 수동 지정도 가능)

실행:
  py drowsiness_feature_logger.py <userPort> <dataPort> vod_vs_driver_seat.cfg
    --baseline 30        # 주행 초기 baseline 수집 시간(초)
    --window 20          # 특징 계산 슬라이딩 윈도우(초)
  -> 콘솔에 실시간 특징 + drowsy_feat_<시각>.csv 저장. Ctrl+C 종료.
"""
import argparse
import struct
import time
from collections import deque
from datetime import datetime

import numpy as np

import config
import serialhelper
import utils

VS_TLV_TYPE = 10
HR_RANGE = (40.0, 120.0)   # bpm
RR_RANGE = (6.0, 35.0)     # rpm


def identify_cols(hist):
    """수집된 (frames, nfield) 배열에서 HR/RR로 가장 그럴듯한 칼럼 인덱스 반환."""
    arr = np.array(hist)
    n = arr.shape[1]
    hr_score, rr_score = np.zeros(n), np.zeros(n)
    for c in range(n):
        col = arr[:, c]
        # 값이 해당 생리 범위 안에 있고, 완전 상수(=플래그)가 아닌 칼럼 선호
        in_hr = np.mean((col >= HR_RANGE[0]) & (col <= HR_RANGE[1]))
        in_rr = np.mean((col >= RR_RANGE[0]) & (col <= RR_RANGE[1]))
        varied = 1.0 if np.std(col) > 1e-3 else 0.0
        hr_score[c] = in_hr * varied
        rr_score[c] = in_rr * varied
    return int(np.argmax(hr_score)), int(np.argmax(rr_score)), hr_score, rr_score


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('userPort'); ap.add_argument('dataPort'); ap.add_argument('configFile')
    ap.add_argument('--fps', type=float, default=5.0, help='출력 프레임레이트(200ms=5)')
    ap.add_argument('--baseline', type=float, default=30.0, help='개인 baseline 수집(초)')
    ap.add_argument('--window', type=float, default=20.0, help='특징 슬라이딩 윈도우(초)')
    ap.add_argument('--hr-col', type=int, default=-1, help='HR 칼럼 수동지정(-1=자동)')
    ap.add_argument('--rr-col', type=int, default=-1, help='RR 칼럼 수동지정(-1=자동)')
    args = ap.parse_args()

    cfgFile = config.read_config_file(args.configFile)
    _ = config.parse_config_file(cfgFile)
    serialUser = serialhelper.SerialHelper(args.userPort, 115200)
    serialData = serialhelper.SerialHelper(args.dataPort, 921600)
    serialUser.sendConfig(cfgFile)

    out_path = 'drowsy_feat_' + datetime.now().strftime('%Y-%m-%d_%H-%M-%S') + '.csv'
    win_n = int(args.window * args.fps)
    hr_win, rr_win = deque(maxlen=win_n), deque(maxlen=win_n)
    ident_hist = []                 # 칼럼 식별용 초기 수집
    hr_col, rr_col = args.hr_col, args.rr_col
    baseline = None                 # (rr_mean, hr_mean) 개인 기준선
    base_rr, base_hr = [], []
    rows = []
    databuffer = bytearray()
    n = 0
    t0 = time.time()

    print('[A안] 시작. 처음 %ds는 baseline(가만히 정상 호흡). Ctrl+C 종료.' % args.baseline)
    try:
        while True:
            newdata = serialData.readall()
            newDebug = serialUser.readall()
            if newDebug:
                print(str(newDebug, 'utf-8'), end='')
            if not newdata:
                continue
            databuffer += newdata
            magicIdx = databuffer.find(b'\x02\x01\x04\x03\x06\x05\x08\x07')
            if magicIdx == -1:
                continue
            databuffer = databuffer[magicIdx:]
            if len(databuffer) < 12:
                continue
            totalLen = int.from_bytes(databuffer[8:12], 'little')
            if len(databuffer) < totalLen:
                continue

            header, index = utils.getHeader(databuffer, 0)
            vs = None
            for _i in range(header['numTLVs']):
                tlv, index = utils.getTlv(databuffer, index)
                if tlv['type'] == VS_TLV_TYPE:
                    nf = tlv['length'] // 4
                    vs = struct.unpack('<%df' % nf, databuffer[index:index + nf * 4])
                index += tlv['length']
            databuffer = databuffer[index:]
            if vs is None:
                continue
            n += 1
            elapsed = time.time() - t0

            # --- 1단계: HR/RR 칼럼 자동 식별 (첫 ~5초 수집) ---
            if hr_col < 0 or rr_col < 0:
                ident_hist.append(vs)
                if elapsed < 5.0:
                    continue
                hr_col, rr_col, hs, rs = identify_cols(ident_hist)
                print('[식별] HR=col%d, RR=col%d (자동). 값 확인하며 이상하면 --hr-col/--rr-col 지정'
                      % (hr_col, rr_col))

            hr, rr = float(vs[hr_col]), float(vs[rr_col])
            hr_win.append(hr); rr_win.append(rr)

            # --- 2단계: baseline 수집 ---
            if elapsed < args.baseline:
                if HR_RANGE[0] <= hr <= HR_RANGE[1]:
                    base_hr.append(hr)
                if RR_RANGE[0] <= rr <= RR_RANGE[1]:
                    base_rr.append(rr)
                if n % 10 == 0:
                    print('[baseline] %.0fs 수집중... HR~%.0f RR~%.0f' % (elapsed, hr, rr))
                continue
            if baseline is None:
                baseline = (np.mean(base_rr) if base_rr else rr,
                            np.mean(base_hr) if base_hr else hr)
                print('[baseline 확정] RR0=%.1f, HR0=%.1f' % (baseline[0], baseline[1]))

            # --- 3단계: 슬라이딩 윈도우 특징 ---
            if len(rr_win) < win_n:
                continue
            rr_arr, hr_arr = np.array(rr_win), np.array(hr_win)
            feat = {
                't': elapsed,
                'rr_mean': rr_arr.mean(), 'rr_std': rr_arr.std(),
                'hr_mean': hr_arr.mean(), 'hr_std': hr_arr.std(),
                'rr_dev': rr_arr.mean() - baseline[0],   # baseline 대비
                'hr_dev': hr_arr.mean() - baseline[1],
            }
            rows.append(list(feat.values()))
            if n % 5 == 0:
                print('[%5.0fs] RR %.1f(±%.2f, Δ%+.1f) | HR %.1f(±%.2f, Δ%+.1f)'
                      % (elapsed, feat['rr_mean'], feat['rr_std'], feat['rr_dev'],
                         feat['hr_mean'], feat['hr_std'], feat['hr_dev']))
    except KeyboardInterrupt:
        print('\n[A안] 종료. 저장 중...')
    finally:
        if rows:
            hdr = 't,rr_mean,rr_std,hr_mean,hr_std,rr_dev,hr_dev'
            np.savetxt(out_path, np.array(rows), delimiter=',', header=hdr,
                       comments='', fmt='%.4f')
            print('[A안] 저장: %s (%d개 특징행)' % (out_path, len(rows)))
        else:
            print('[A안] 특징행이 없습니다. baseline/window 시간보다 오래 측정했는지 확인.')


if __name__ == '__main__':
    main()
