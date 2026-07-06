"""
차량 내부(In-Cabin) 실시간 탑승자 감지.

[핵심 설계 — 왜 point cloud 개수가 아니라 motion-energy인가]
  pc_generation의 point cloud는 EnergyTop128 때문에 '탑승자 유무와 무관하게'
  항상 에너지 상위 128개 셀을 point로 만든다. 실측(empty.bin vs person.bin)에서도
  빈 차가 121점, 사람이 108점으로 오히려 빈 차가 더 많았다. 즉 point 개수/클러스터
  크기로는 사람을 못 가린다.
  대신 '정지 클러터 제거 후 non-zero Doppler 최대 에너지'(motion-energy score)는
  empty(mean 3.91) / person(mean 5.23)을 명확히 분리한다.
    threshold=4.10 기준 empty 0% vs person 95% vs stop(정지호흡) 48% 초과.
  => 이 score로 존재를 판정하고, point cloud는 시각화용으로만 쓴다.

[실시간 루프 최적화]
  - 매 반복 버퍼를 최신 프레임까지 비워(latency drain) 최신 상태만 처리.
  - 프레임 없을 때도 plt.pause로 GUI 이벤트 루프를 돌려 'Not Responding' 방지.
  - 화면은 ~6FPS로 throttle.

[⚠️ 반드시 캘리브레이션]
  motion-energy threshold는 환경(차량/거치 위치/게인)마다 다르다.
  실제 차량에서 '빈 차'를 한 번 녹화해 --calibrate 로 기준선을 잡고,
  그 값으로 OCC_THRESHOLD 를 지정할 것. 아래 기본값(4.10)은 실습 캡처 기준.
"""
import os
os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(os.cpu_count() or 4))

import sys
import time
from collections import deque

import numpy as np

from steaming import adcCapThread
import configuration as cfg
from pc_generation import (PointCloudProcessCFG, FrameConfig, bin2np_frame,
                           frame2pointcloud, frameReshape, rangeFFT,
                           clutter_removal, dopplerFFT)

USE_DCA_CONTROL = '--no-dca-control' not in sys.argv
SHOW_PLOT = '--no-plot' not in sys.argv
CALIBRATE = '--calibrate' in sys.argv

# ---- 튜닝 상수 ----
PLOT_INTERVAL = 1.0 / 6.0     # 화면 갱신 최소 간격(초)
RANGE_MIN, RANGE_MAX = 0.2, 2.0   # 탐지 거리 게이트(m)
DOPPLER_GUARD = 2             # zero-Doppler 주변 정지잔차로 제외할 bin 수
OCC_THRESHOLD = 4.10          # motion-energy 임계값 (환경마다 재보정 필요)
MACRO_MARGIN = 1.5            # threshold + 이 값 초과면 대형움직임(macro)으로 분류
WIN_FRAMES = 50               # 시간통합 윈도우(10FPS면 5초)
PRESENT_RATIO = 0.15          # 윈도우 내 초과비율 이 이상이면 '사람 있음'


def _range_gate(fc):
    lo = max(int(RANGE_MIN / cfg.RANGE_RESOLUTION), 1)
    hi = min(int(RANGE_MAX / cfg.RANGE_RESOLUTION), fc.numRangeBins)
    return lo, hi


def frame_occupancy_score(np_frame, fc, lo, hi):
    """정지 클러터 제거 후 ROI 내 non-zero Doppler 최대 에너지(dB).

    빈 공간이면 노이즈만 남아 낮고, 사람(호흡/미세움직임)이 있으면 높다.
    """
    reshaped = frameReshape(np_frame, fc)
    r = rangeFFT(reshaped, fc)
    r = clutter_removal(r, axis=2)          # 고정물(시트/대시보드) 제거
    d = dopplerFFT(r, fc)                    # (tx, rx, doppler, range)
    rd = np.sum(d, axis=(0, 1))             # 안테나 합산 -> (doppler, range)
    rdDB = np.log10(np.abs(rd) + 1e-12)
    center = fc.numDopplerBins // 2         # fftshift 후 0속도 위치
    roi = rdDB[:, lo:hi].copy()
    roi[center - DOPPLER_GUARD: center + DOPPLER_GUARD + 1, :] = -100.0  # 정지잔차 제외
    return float(roi.max())


def process_frame_to_pc(np_frame, pcCFG, shift_arr):
    """복원된 복소 프레임 -> point cloud (N,6). 시각화 전용.
    reg_data(128 강제채움)를 쓰지 않아 실제 검출 점만 표시한다.
    """
    pc = frame2pointcloud(np_frame, pcCFG)
    if pc.shape[0] == 0 or pc.shape[1] == 0:
        return np.zeros((0, 6), dtype=np.float32)
    raw = np.transpose(pc, (1, 0))          # (N,6) [x,y,z,V,energy,R]
    raw[:, :3] = raw[:, :3] + shift_arr
    return raw


def classify(score, recent):
    """score와 최근 윈도우로 (status_code, ratio) 판정.
    0=Empty, 1=Micro/Respiration, 2=Macro Movement.
    """
    recent.append(score > OCC_THRESHOLD)
    ratio = sum(recent) / len(recent)
    if ratio < PRESENT_RATIO:
        return 0, ratio
    # 존재함: 순간 에너지가 크게 튀면 대형 움직임, 아니면 미세/호흡
    if score > OCC_THRESHOLD + MACRO_MARGIN:
        return 2, ratio
    return 1, ratio


def drain_to_latest(cap):
    """버퍼를 비우고 가장 최근 int16 프레임만 반환. (latest or None, overwritten)."""
    latest, overwritten = None, False
    while True:
        readItem, itemNum, _ = cap.getFrame()
        if itemNum > 0:
            latest = readItem
            continue
        if itemNum == -1:
            overwritten = True
        break
    return latest, overwritten


def main():
    pcCFG = PointCloudProcessCFG()
    fc = FrameConfig()
    shift_arr = cfg.MMWAVE_RADAR_LOC
    lo, hi = _range_gate(fc)
    print('[info] range gate bin [%d,%d] = %.2f~%.2f m, occ_threshold=%.2f%s'
          % (lo, hi, lo * cfg.RANGE_RESOLUTION, hi * cfg.RANGE_RESOLUTION,
             OCC_THRESHOLD, '  [CALIBRATE MODE]' if CALIBRATE else ''))

    if USE_DCA_CONTROL:
        try:
            from dca1000_control import DCA1000Control
            dca = DCA1000Control()
            dca.start_streaming()
            dca.close()
            time.sleep(0.5)
        except Exception as e:
            print('[warn] DCA control failed:', e)
            print('       -> lua가 DCA를 설정했다고 가정하고 수신만 진행')

    cap = adcCapThread(1, 'adc')
    cap.start()
    print('[main] receiver started, waiting for packets on 4098...')

    plt = None
    if SHOW_PLOT and not CALIBRATE:
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d import Axes3D  # noqa
        plt.ion()
        fig = plt.figure(figsize=(8, 6))
        ax = fig.add_subplot(111, projection='3d')
        ax.set_xlim(-3, 3); ax.set_ylim(0, 6); ax.set_zlim(-2, 4)
        ax.set_xlabel('X (m)'); ax.set_ylabel('Y (m)'); ax.set_zlabel('Z (m)')
        scat = ax.scatter([], [], [], s=40, edgecolors='k', linewidths=0.3)
        title = ax.set_title('In-Car Monitor - waiting...')
        plt.show(block=False)

    recent = deque(maxlen=WIN_FRAMES)
    calib_scores = []
    frame_count = 0
    last_plot = 0.0
    color_map = {0: 'gray', 1: 'cyan', 2: 'red'}
    label_map = {0: 'EMPTY', 1: 'RESPIRATION/MICRO', 2: 'MACRO MOVEMENT'}
    tcolor_map = {0: 'black', 1: 'blue', 2: 'red'}

    try:
        while True:
            latest, overwritten = drain_to_latest(cap)
            if overwritten:
                print('[main] buffer overwritten (처리가 수신을 못 따라감)')
            if latest is None:
                if plt is not None:
                    plt.pause(0.005)
                else:
                    time.sleep(0.005)
                continue

            frame_count += 1
            np_frame = bin2np_frame(latest)
            score = frame_occupancy_score(np_frame, fc, lo, hi)

            if CALIBRATE:
                calib_scores.append(score)
                if frame_count % 10 == 0:
                    arr = np.array(calib_scores)
                    print('[calib] n=%d  score mean=%.3f max=%.3f std=%.3f  '
                          '제안 threshold=%.3f'
                          % (len(arr), arr.mean(), arr.max(), arr.std(),
                             arr.max() + 2 * arr.std()))
                continue

            status_code, ratio = classify(score, recent)

            if status_code != 0 or frame_count % 15 == 0:
                print('[%d] %-18s | score=%.3f | win초과 %3.0f%%'
                      % (frame_count, label_map[status_code], score, ratio * 100))

            if plt is not None and (time.time() - last_plot) >= PLOT_INTERVAL:
                last_plot = time.time()
                pc = process_frame_to_pc(np_frame, pcCFG, shift_arr)
                if len(pc) > 0:
                    scat._offsets3d = (pc[:, 0], pc[:, 1], pc[:, 2])
                    scat.set_color(color_map[status_code])
                else:
                    scat._offsets3d = ([], [], [])
                title.set_text('In-Car Monitor - Frame %d\nOccupant: %s (score %.2f)'
                               % (frame_count, label_map[status_code], score))
                title.set_color(tcolor_map[status_code])
                plt.pause(0.001)
            elif plt is not None:
                plt.pause(0.001)

    except KeyboardInterrupt:
        print('\n[main] stopping...')
        if CALIBRATE and calib_scores:
            arr = np.array(calib_scores)
            print('[calib] 최종: mean=%.3f max=%.3f std=%.3f -> threshold 권장 %.3f'
                  % (arr.mean(), arr.max(), arr.std(), arr.max() + 2 * arr.std()))
    finally:
        cap.whileSign = False
        time.sleep(0.2)
        print('[main] done. total frames:', frame_count)


if __name__ == '__main__':
    main()
