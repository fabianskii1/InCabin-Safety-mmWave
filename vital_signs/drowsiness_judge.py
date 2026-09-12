"""개인 기준선 이탈 판정 — 각성도 저하(졸음) 모니터.

최종보고서 3.3절의 판정 로직을 twin_bridge.py에 이식한 버전이다.
DD-Database(7명·14세션, 실제 ECG)로 검증한 최종 로직만 그대로 옮겼으며
(ROC AUC 0.689, 개인화 상한 0.747), 분석에만 쓰인 dd_common.py의 나머지
함수(라벨링, 캐시 로딩 등)는 실시간 판정에 필요 없어 가져오지 않았다.

판정 특징은 hr_std_delta(20초 창 HR 표준편차 - 개인 기준선) 단독이다.
hr_slope, RR 계열은 최종 검증에서 제외된 특징이므로 포함하지 않는다
(3.3.7절 "최종 설계 파라미터" 표 참조).

[스코프] 이것은 '졸음 확정 경보'가 아니라 '개인 기준선 이탈 모니터'다.
운영점(z=1.5)에서 재현율 73% / 오탐 44%로 안전 경보 성능에는 못 미친다.
이탈의 원인도 특정하지 않는다(졸음·움직임·긴장 등 복합 가능).
twin_bridge.py의 Hold/Warning(무호흡)처럼 결정론적으로 확정하는 것과
성격이 다르므로, payload의 별도 필드(drowsy_state)로 노출한다.
"""

from __future__ import annotations

import math
from collections import deque

WINDOW_SEC = 20.0        # 특징 추출 슬라이딩 창
HOP_SEC = 10.0           # 창 이동 간격 (twin_bridge는 프레임마다 갱신되므로
                         # 여기서는 '최근 WINDOW_SEC초 버퍼'로 근사한다)
BASELINE_SEC = 60.0      # 개인 기준선(σ0) 수집 구간 — 세션 시작 후 첫 60초
SCALE_SEC = 300.0        # 시간 평활(인과적 이동평균) 창 — 5분, Cohen's d 정점
CAL_SEC = 600.0          # 개인 z-정규화 보정 구간 — 세션 시작 후 첫 10분
Z_THRESH = 1.5           # 이탈 판정 임계 z-score
NEED_CONSECUTIVE = 4     # 히스테리시스: 연속 N회 이상이어야 확정/해제


class DrowsinessJudge:
    """HR 시계열만 넣어주면 20초 창 hr_std -> 5분 평활 -> 개인 z-정규화 ->
    히스테리시스까지 전부 내부에서 처리한다.

    사용법(twin_bridge.py 쪽):
        judge = DrowsinessJudge()
        ...  # 매 프레임 hr 값이 나올 때마다
        state = judge.update(now_sec, hr)
        # state: 'CALIB'(기준선/보정 수집 중) | 'NORMAL' | 'DEVIATED'
    """

    def __init__(self, window_sec=WINDOW_SEC, hop_sec=HOP_SEC,
                 baseline_sec=BASELINE_SEC, scale_sec=SCALE_SEC,
                 cal_sec=CAL_SEC, z_thresh=Z_THRESH,
                 need_consecutive=NEED_CONSECUTIVE):
        self.window_sec = window_sec
        self.hop_sec = hop_sec
        self.baseline_sec = baseline_sec
        self.scale_sec = scale_sec
        self.cal_sec = cal_sec
        self.z_thresh = z_thresh
        self.need_consecutive = need_consecutive

        self.t0 = None                       # 첫 hr 샘플 시각(세션 시작 기준점)
        self.hr_buf = deque()                # [(t, hr)] 최근 window_sec초만 유지
        self.base_hr = []                    # baseline_sec초 동안의 raw HR (σ0 계산용)
        self.baseline_std = None             # σ0

        self.last_window_t = None            # 마지막으로 hr_std를 뽑은 시각(hop 간격 제어)
        self.delta_hist = deque()            # [(t, delta)] 최근 scale_sec초 (인과 평활용)

        self.cal_vals = []                   # 보정구간(cal_sec) 동안의 평활값 s_t 모음
        self.cal_mu = None
        self.cal_sd = None

        self.consec_risk = 0
        self.consec_clear = 0
        self.state_deviated = False
        self._state = 'CALIB'   # 마지막 확정 상태(hop 스킵 시 이걸 그대로 반환)

        self.last_hr_std = None
        self.last_delta = None
        self.last_smooth = None
        self.last_z = None

    def _elapsed(self, t):
        if self.t0 is None:
            self.t0 = t
        return t - self.t0

    def update(self, t, hr):
        """t: 초 단위 타임스탬프(단조 증가, time.monotonic() 등 무관 — 상대시간만 씀)
        hr: 그 순간의 심박수(bpm). 0 이하이거나 결측이면 호출하지 않을 것.
        반환: 'CALIB' | 'NORMAL' | 'DEVIATED'
        """
        elapsed = self._elapsed(t)

        # --- ① 특징 추출: 최근 window_sec초 버퍼 유지 + hop 간격마다 hr_std 계산 ---
        self.hr_buf.append((t, hr))
        while self.hr_buf and t - self.hr_buf[0][0] > self.window_sec:
            self.hr_buf.popleft()

        # baseline(σ0) 수집: 세션 시작 baseline_sec초 동안의 raw HR
        if elapsed <= self.baseline_sec:
            self.base_hr.append(hr)
            return self._state
        elif self.baseline_std is None:
            if len(self.base_hr) >= 5:
                self.baseline_std = _std(self.base_hr)
            else:
                # baseline 구간에 유효 샘플이 너무 적으면 기준선을 못 잡음 -> 계속 CALIB
                return self._state

        # hop_sec마다 한 번씩만 새 창을 뽑는다(twin_bridge는 프레임마다 호출되므로).
        # 스킵하는 동안에도 직전에 확정된 상태(CALIB 포함)를 그대로 유지해야 한다.
        if self.last_window_t is not None and (t - self.last_window_t) < self.hop_sec:
            return self._state
        if len(self.hr_buf) < 5:
            return self._state
        self.last_window_t = t

        hrs = [v for _, v in self.hr_buf]
        hr_std = _std(hrs)
        delta = hr_std - self.baseline_std
        self.last_hr_std, self.last_delta = hr_std, delta

        # --- ② 시간 평활: 과거 scale_sec초 이동평균(인과적, 미래 미사용) ---
        self.delta_hist.append((t, delta))
        while self.delta_hist and t - self.delta_hist[0][0] > self.scale_sec:
            self.delta_hist.popleft()
        smooth = sum(d for _, d in self.delta_hist) / len(self.delta_hist)
        self.last_smooth = smooth

        # --- ③ 개인 정규화: 세션 초반 cal_sec초로 z-정규화 기준(mu, sd) 확정 ---
        if self.cal_mu is None:
            if elapsed <= self.baseline_sec + self.cal_sec:
                self.cal_vals.append(smooth)
                self._state = 'CALIB'
                return self._state
            if len(self.cal_vals) >= 10:
                self.cal_mu = sum(self.cal_vals) / len(self.cal_vals)
                self.cal_sd = _std(self.cal_vals) or 1e-6
            else:
                # 보정 표본이 너무 적으면(연결 끊김 등) 판정을 시작하지 않음
                self._state = 'CALIB'
                return self._state

        z = (smooth - self.cal_mu) / self.cal_sd
        self.last_z = z

        # --- ④ 히스테리시스: 연속 N회 확인해야 확정/해제 ---
        raw_flag = z >= self.z_thresh
        if raw_flag:
            self.consec_risk += 1
            self.consec_clear = 0
        else:
            self.consec_clear += 1
            self.consec_risk = 0

        if (not self.state_deviated) and self.consec_risk >= self.need_consecutive:
            self.state_deviated = True
        elif self.state_deviated and self.consec_clear >= self.need_consecutive:
            self.state_deviated = False

        self._state = 'DEVIATED' if self.state_deviated else 'NORMAL'
        return self._state

    def detail(self):
        """디버그/로깅용 스냅샷."""
        return {
            'hr_std': self.last_hr_std,
            'hr_std_delta': self.last_delta,
            'hr_std_delta_smooth': self.last_smooth,
            'z': self.last_z,
            'baseline_std': self.baseline_std,
            'cal_mu': self.cal_mu,
            'cal_sd': self.cal_sd,
        }


def _std(values):
    n = len(values)
    if n < 2:
        return 0.0
    m = sum(values) / n
    var = sum((v - m) ** 2 for v in values) / n
    return math.sqrt(var)
