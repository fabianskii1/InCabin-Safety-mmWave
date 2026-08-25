"""
[A안] 실시간 졸음 판정 — firmware 수정 없이, 현재 5FPS HR/RR 출력으로 동작.

판정 로직 = ① 개인 baseline 이탈 + ② 추세(기울기) 추적 + ③ 지속성(hysteresis).

  ① 평균 이탈 (mean-deviation)
     주행 초기 baseline(RR0, HR0)을 기준으로, 현재 윈도우 평균이 얼마나 벗어났는지.
     "졸리면 호흡이 느려진다"는 절대 수준 신호.

  ② 추세(기울기) 추적 (trend/slope)  <-- Exp4(63명, 수면박탈 실험) 재분석으로 확인
     공개 데이터셋(DROZY 14명 / Exp4 63명)으로 검증한 결과, HR·RR의 '평균값'만으로는
     사람이 바뀌면 판별이 거의 안 됐다(교차검증 정확도가 다수결 baseline 근처).
     반면 같은 세션 안에서 시간에 따른 HR의 '기울기(slope)'는 통계적으로 유의했고
     (Mann-Whitney p=0.013), 이 기울기만으로 학습한 분류기가 baseline 대비
     +7~+11%p 높은 정확도를 냈다(피험자 단위 교차검증, accuracy 0.60~0.65).
     즉 "지금 얼마나 낮은가"보다 "최근에 얼마나 빠르게 낮아지고 있는가"가 더 강한 신호.
     -> 슬라이딩 윈도우마다 HR/RR을 시간에 대해 선형회귀해 기울기를 뽑는다.

  ③ 지속성 (sliding-window persistence)
     ①②가 한 번 튀었다고 바로 경보 내지 않는다. occupancy 판정 때와 같은 방식으로,
     최근 K회 판정 중 다수가 "위험"이어야 최종 확정 -> 순간 노이즈에 의한 오탐 억제.

[개인차가 제거되는 이유]
  ①은 baseline을 빼므로 '그 사람의 절대 수준' 차이가 제거된다.
  ②는 애초에 절대값이 아니라 그 사람 안에서의 변화율이라 개인차가 원천적으로 없다.
  두 신호는 서로 다른 것을 보므로(수준 vs 변화 속도) OR 결합이 각각의 약점을 보완한다.

[HR/RR 칼럼 자동 식별]
  이 펌웨어 빌드의 TLV10 필드 배치가 파서 가정과 다를 수 있어, 값의 '범위'로 찾는다.
  - HR 추정치: 대략 40~120 bpm 범위에서 진동
  - RR 추정치: 대략 6~35 rpm 범위에서 진동
  (--hr-col / --rr-col 로 수동 지정도 가능)

실행:
  py drowsiness_feature_logger.py <userPort> <dataPort> vod_vs_driver_seat.cfg
    --baseline 30        # 주행 초기 baseline 수집 시간(초)
    --window 20          # 특징 계산 슬라이딩 윈도우(초)
    --consecutive 4      # 연속 N회 위험판정이면 최종 확정 (다수결 아님, 아래 설명 참고)
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

# --- 판정 임계값 ---
# 이 값들은 '데이터셋에서 그대로 가져온 절대 기준'이 아니라(도메인이 다름: 시뮬레이터
# 수면박탈 실험 vs 실차 레이더), '그 사람의 baseline 대비 상대 기준'으로 잡는다.
# 그래야 개인차 · 센서/환경 차이에 강건하다. 실측 후 튜닝 대상.
RR_DEV_RATIO = 0.15   # baseline 대비 RR이 15% 이상 낮아지면 '수준' 위험 신호

# SLOPE_PCTL: Exp4(63명, 수면박탈 실험) 재분석 결과, 졸림 증가군의 HR slope는
# 전체 분포 중앙값 기준 하위 38% 부근에 몰려 있었다(하위 20%는 너무 엄격해
# 위험군의 78%를 놓침 - 재현율 22%). 38로 완화하면 재현율은 51%로 오르지만
# 오탐률도 19%->27%로 같이 오른다(공짜 개선이 아님, 재현율/오탐 트레이드오프).
# 이 오탐 상승은 '다수결' 지속성 검증으로는 못 막는다(안정상태에서도 이항분포상
# 약 25% 확정 오탐 - 아래 DrowsinessJudge 설명 참고). '연속 확인' 방식으로 억제한다.
SLOPE_PCTL = 38
MIN_SLOPE_HISTORY = 6   # 추세 판정을 시작하기 전 최소 누적 기록 수

# STD_PCTL: DD-Database(7명, 20초 창 = 이 코드와 동일 스케일) 재분석 결과, 개인 baseline
# 대비 HR 변동성(hr_std_delta)이 졸림 임박 구간에서 유의하게 컸다(p=3.9e-25, 평상시
# 평균 0.45 -> 졸림임박 평균 0.95). 세션 내 상위 25%(=변동성이 유독 큰 쪽)를 위험으로 본다.
STD_PCTL = 75           # 상위 25% (100-75)
MIN_STD_HISTORY = 6


def identify_cols(hist):
    """수집된 (frames, nfield) 배열에서 HR/RR로 가장 그럴듯한 칼럼 인덱스 반환."""
    arr = np.array(hist)
    n = arr.shape[1]
    hr_score, rr_score = np.zeros(n), np.zeros(n)
    for c in range(n):
        col = arr[:, c]
        in_hr = np.mean((col >= HR_RANGE[0]) & (col <= HR_RANGE[1]))
        in_rr = np.mean((col >= RR_RANGE[0]) & (col <= RR_RANGE[1]))
        varied = 1.0 if np.std(col) > 1e-3 else 0.0
        hr_score[c] = in_hr * varied
        rr_score[c] = in_rr * varied
    return int(np.argmax(hr_score)), int(np.argmax(rr_score)), hr_score, rr_score


def window_slope(t_arr, v_arr):
    """윈도우 안 값들을 시간에 선형회귀 -> 기울기(단위/초). 점이 2개 미만이면 0."""
    if len(t_arr) < 2 or np.ptp(t_arr) < 1e-6:
        return 0.0
    return float(np.polyfit(t_arr, v_arr, 1)[0])


class DrowsinessJudge:
    """① 평균 이탈 + ② 추세(기울기) + ③ 지속성(연속 확인)을 합친 판정기.

    [③ 왜 '다수결'이 아니라 '연속'인가]
    SLOPE_PCTL은 세션 자기 자신의 최근 기록 중 percentile이라, 정의상 안정 상태에서도
    매 판정마다 약 SLOPE_PCTL%(=38%) 확률로 raw_flag가 뜬다(percentile의 수학적 성질,
    실제 위험 여부와 무관). '최근 N회 중 과반' 방식(다수결)은 이런 무작위 산발적 발생에
    약해서, 이항분포 계산상 SLOPE_PCTL=38/vote=7일 때도 안정 상태에서 약 25% 확률로
    오탐 확정된다(합성 시나리오 실측: 60회 중 8회, 13%). 반면 '연속 N회'를 요구하면
    무작위 산발적 발생이 연달아 이어질 확률은 훨씬 낮아, 같은 조건에서 오탐 확정 확률이
    약 1.4%(연속 4회 기준)로 줄어든다(몬테카를로 시뮬레이션 검증). 진짜 지속되는 추세는
    '연속으로' 걸릴 수밖에 없으므로 검출력은 유지된다.
    """

    def __init__(self, need_consecutive=4, need_clear=4):
        self.baseline = None            # (rr0, hr0)
        self.baseline_hr_std = None     # baseline 구간 HR의 표준편차(변동성 기준선)
        self.hr_slope_hist = deque(maxlen=200)   # 세션 내 hr_slope 기록(추세의 '개인 분포')
        self.hr_std_delta_hist = deque(maxlen=200)   # 세션 내 (현재std - baseline std) 기록
        self.need_consecutive = need_consecutive   # 진입: 연속 이만큼 위험해야 DROWSY 진입
        self.need_clear = need_clear               # 이탈: 연속 이만큼 정상이어야 DROWSY 해제
        self.consec_risk = 0
        self.consec_clear = 0
        self.state_drowsy = False   # hysteresis 상태: 한번 진입하면 이탈 조건 전까지 유지

    def set_baseline(self, rr0, hr0, hr_std0=0.0):
        self.baseline = (rr0, hr0)
        self.baseline_hr_std = hr_std0

    def update(self, rr_mean, hr_mean, hr_slope, hr_std):
        """한 번의 윈도우 계산 결과를 넣고 (raw_flag, confirmed_flag, detail) 반환."""
        rr0, hr0 = self.baseline
        rr_dev_ratio = (rr_mean - rr0) / rr0 if rr0 else 0.0

        level_risk = rr_dev_ratio <= -RR_DEV_RATIO   # ① 수준: RR이 baseline보다 확 낮음

        self.hr_slope_hist.append(hr_slope)
        trend_risk = False
        slope_pctl_val = None
        if len(self.hr_slope_hist) >= MIN_SLOPE_HISTORY:
            slope_pctl_val = np.percentile(self.hr_slope_hist, SLOPE_PCTL)
            # 지금 기울기가 '이 세션에서 지금까지 본 것 중 유독 급격히 감소'하는 축에 속하면 위험
            trend_risk = hr_slope <= slope_pctl_val and hr_slope < 0

        # ③ 변동성(variability) : DD-Database(20초 창, 실시간과 동일 스케일) 재분석에서
        # 유일하게 강하게 유의했던 신호(p=3.9e-25). baseline 대비 HR 표준편차가 늘어난 정도를
        # 세션 내 상위 25%(STD_PCTL)와 비교한다.
        hr_std_delta = hr_std - (self.baseline_hr_std or 0.0)
        self.hr_std_delta_hist.append(hr_std_delta)
        variability_risk = False
        std_pctl_val = None
        if len(self.hr_std_delta_hist) >= MIN_STD_HISTORY:
            std_pctl_val = np.percentile(self.hr_std_delta_hist, STD_PCTL)
            variability_risk = hr_std_delta >= std_pctl_val and hr_std_delta > 0

        # trend를 필수 게이트로 두고(가장 검증된 신호), level 또는 variability 중
        # 하나만 추가로 걸려도 위험으로 본다: trend AND (level OR variability).
        # 순수 OR(노이즈 바닥 41%)와 3-way AND(1.3%, 그러나 실전에서 세 신호가 동시에
        # 나빠지길 요구하면 재현율이 지나치게 낮아질 위험) 사이의 절충으로,
        # 순간 노이즈 바닥 계산상 약 10.6%이며 hysteresis로 억제됨을 시뮬레이션 확인함.
        raw_flag = trend_risk and (level_risk or variability_risk)

        # hysteresis 상태 갱신: 진입(연속 위험 need_consecutive회) / 이탈(연속 정상 need_clear회)
        # 조건을 다르게 둬서, 한번 DROWSY 상태가 되면 산발적으로 정상 판정 한두 번 섞여도
        # 즉시 풀리지 않고 '지속된 위험 상황'으로 계속 유지된다(리셋 후 매번 재확정하던
        # 이전 방식의 '깜빡임' 문제 해결).
        if raw_flag:
            self.consec_risk += 1
            self.consec_clear = 0
        else:
            self.consec_clear += 1
            self.consec_risk = 0

        just_entered = (not self.state_drowsy) and self.consec_risk >= self.need_consecutive
        just_cleared = self.state_drowsy and self.consec_clear >= self.need_clear
        if just_entered:
            self.state_drowsy = True
        elif just_cleared:
            self.state_drowsy = False
        confirmed = self.state_drowsy

        detail = {
            'rr_dev_ratio': rr_dev_ratio, 'level_risk': level_risk,
            'hr_slope': hr_slope, 'slope_pctl_val': slope_pctl_val, 'trend_risk': trend_risk,
            'hr_std_delta': hr_std_delta, 'std_pctl_val': std_pctl_val,
            'variability_risk': variability_risk,
        }
        return raw_flag, confirmed, detail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('userPort'); ap.add_argument('dataPort'); ap.add_argument('configFile')
    ap.add_argument('--fps', type=float, default=5.0, help='출력 프레임레이트(200ms=5)')
    ap.add_argument('--baseline', type=float, default=30.0, help='개인 baseline 수집(초)')
    ap.add_argument('--window', type=float, default=20.0, help='특징 슬라이딩 윈도우(초)')
    ap.add_argument('--consecutive', type=int, default=4,
                    help='연속 N회 위험판정이면 최종 확정(다수결 아님, 오탐 억제에 더 강함)')
    ap.add_argument('--hr-col', type=int, default=-1, help='HR 칼럼 수동지정(-1=자동)')
    ap.add_argument('--rr-col', type=int, default=-1, help='RR 칼럼 수동지정(-1=자동)')
    ap.add_argument('--model-path', default=None,
                    help='DD-Database로 학습한 모델(drowsy_hr_model.pkl) 경로. '
                         '지정하면 hr_std_delta로 모델 예측도 참고용으로 함께 출력/기록한다. '
                         '주의: 이 모델은 최종 판정을 대체하지 않는다(검증 f1=0.51로 아직 '
                         '단독 신뢰하기엔 이르다고 판단, 규칙 기반 판정과 나란히 비교하는 용도).')
    args = ap.parse_args()

    model_bundle = None
    if args.model_path:
        import joblib
        model_bundle = joblib.load(args.model_path)
        print('[모델] %s 로드됨 (%s, 학습 시 f1(macro)=%.3f vs naive=%.3f)'
              % (args.model_path, model_bundle['trained_on'],
                 model_bundle.get('model_f1_macro', float('nan')),
                 model_bundle.get('naive_f1_macro', float('nan'))))

    cfgFile = config.read_config_file(args.configFile)
    _ = config.parse_config_file(cfgFile)
    serialUser = serialhelper.SerialHelper(args.userPort, 115200)
    serialData = serialhelper.SerialHelper(args.dataPort, 921600)
    serialUser.sendConfig(cfgFile)

    out_path = 'drowsy_feat_' + datetime.now().strftime('%Y-%m-%d_%H-%M-%S') + '.csv'
    win_n = int(args.window * args.fps)
    t_win = deque(maxlen=win_n)
    hr_win, rr_win = deque(maxlen=win_n), deque(maxlen=win_n)
    ident_hist = []
    hr_col, rr_col = args.hr_col, args.rr_col
    baseline_set = False
    base_rr, base_hr = [], []
    judge = DrowsinessJudge(need_consecutive=args.consecutive)
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
            t_win.append(elapsed); hr_win.append(hr); rr_win.append(rr)

            # --- 2단계: baseline 수집 ---
            if elapsed < args.baseline:
                if HR_RANGE[0] <= hr <= HR_RANGE[1]:
                    base_hr.append(hr)
                if RR_RANGE[0] <= rr <= RR_RANGE[1]:
                    base_rr.append(rr)
                if n % 10 == 0:
                    print('[baseline] %.0fs 수집중... HR~%.0f RR~%.0f' % (elapsed, hr, rr))
                continue
            if not baseline_set:
                rr0 = np.mean(base_rr) if base_rr else rr
                hr0 = np.mean(base_hr) if base_hr else hr
                hr_std0 = np.std(base_hr) if len(base_hr) >= 2 else 0.0
                judge.set_baseline(rr0, hr0, hr_std0)
                baseline_set = True
                print('[baseline 확정] RR0=%.1f, HR0=%.1f, HR_std0=%.2f' % (rr0, hr0, hr_std0))

            # --- 3단계: 슬라이딩 윈도우 특징 + 판정 ---
            if len(rr_win) < win_n:
                continue
            t_arr = np.array(t_win)
            rr_arr, hr_arr = np.array(rr_win), np.array(hr_win)
            rr_mean, hr_mean, hr_std = rr_arr.mean(), hr_arr.mean(), hr_arr.std()
            hr_slope = window_slope(t_arr, hr_arr)   # bpm/초

            raw_flag, confirmed, d = judge.update(rr_mean, hr_mean, hr_slope, hr_std)

            # 모델(있으면): hr_std_delta는 judge가 이미 계산한 값을 그대로 재사용
            # (같은 baseline 기준, 이중계산·불일치 방지). 참고용이라 confirmed에는 영향 없음.
            model_pred = None
            if model_bundle is not None:
                model_pred = int(model_bundle['model'].predict([[d['hr_std_delta']]])[0])

            feat = {
                't': elapsed, 'rr_mean': rr_mean, 'hr_mean': hr_mean,
                'hr_slope_per_min': hr_slope * 60.0, 'hr_std_delta': d['hr_std_delta'],
                'rr_dev_ratio': d['rr_dev_ratio'], 'level_risk': int(d['level_risk']),
                'trend_risk': int(d['trend_risk']), 'variability_risk': int(d['variability_risk']),
                'confirmed_drowsy': int(confirmed),
                'model_pred': -1 if model_pred is None else model_pred,
            }
            rows.append(list(feat.values()))
            if n % 5 == 0 or confirmed:
                flag_str = ('🚨 DROWSY 확정' if confirmed else
                           ('⚠ 위험신호' if raw_flag else '정상'))
                model_str = '' if model_pred is None else ('  | 모델:%s' % ('졸림' if model_pred else '평상'))
                print('[%5.0fs] RR %.1f(Δ%+.0f%%) | HR %.1f (기울기 %+.2f/분) | %s%s'
                      % (elapsed, rr_mean, d['rr_dev_ratio'] * 100,
                         hr_mean, feat['hr_slope_per_min'], flag_str, model_str))
    except KeyboardInterrupt:
        print('\n[A안] 종료. 저장 중...')
    finally:
        if rows:
            hdr = ('t,rr_mean,hr_mean,hr_slope_per_min,hr_std_delta,rr_dev_ratio,'
                  'level_risk,trend_risk,variability_risk,confirmed_drowsy,model_pred')
            np.savetxt(out_path, np.array(rows), delimiter=',', header=hdr,
                       comments='', fmt='%.4f')
            print('[A안] 저장: %s (%d개 특징행)' % (out_path, len(rows)))
        else:
            print('[A안] 특징행이 없습니다. baseline/window 시간보다 오래 측정했는지 확인.')


if __name__ == '__main__':
    main()
