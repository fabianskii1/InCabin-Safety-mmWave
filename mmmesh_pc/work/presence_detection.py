"""
raw ADC bin -> "사람 있음/없음" (occupancy / presence detection).

왜 이 스크립트가 따로 필요한가:
  pc_generation.py의 point cloud는 EnergyTop128 + reg_data 때문에 프레임마다
  '무조건 128개'로 고정된다(신경망 입력용). 그래서 점 개수로는 유무를 못 가린다.
  여기서는 점 개수 대신 '움직임 에너지'로 판단한다.

원리:
  - static clutter removal 후에는 고정물(시트/대시보드) 반사가 사라진다.
  - 정지한 사람도 호흡/미세움직임 -> non-zero Doppler 성분이 남는다.
  - 빈 공간이면 노이즈만 남아 에너지가 낮다.
  => ROI(관심 거리 구간) 안에서, zero-Doppler(정지 잔차)를 제외한
     Range-Doppler 에너지의 최대치를 'occupancy score'로 쓴다.
     score > threshold 이면 '사람 있음'.

사용법:
  1) 먼저 빈 차/빈 공간을 녹화한 bin으로 기준선 확인:
       py presence_detection.py empty.bin --calibrate
     -> score 분포(평균/최대)가 나온다. 그 최대치보다 살짝 위를 threshold로 잡는다.
  2) 실제 판정:
       py presence_detection.py test.bin --threshold <위에서 정한 값>
"""
import argparse
import os

import numpy as np

import configuration as cfg
from pc_generation import (
    FrameConfig, RawDataReader, bin2np_frame,
    frameReshape, rangeFFT, clutter_removal, dopplerFFT,
)


def frame_occupancy_score(frame, frameConfig, range_gate, zero_doppler_guard):
    """한 프레임의 occupancy score(정지성분 제외 최대 에너지, dB)를 반환."""
    reshaped = frameReshape(frame, frameConfig)
    rangeResult = rangeFFT(reshaped, frameConfig)
    rangeResult = clutter_removal(rangeResult, axis=2)          # 고정물 제거
    dopplerResult = dopplerFFT(rangeResult, frameConfig)        # (tx, rx, doppler, range)

    # 안테나 전체 합산 -> (doppler, range) 에너지 맵
    rdMap = np.sum(dopplerResult, axis=(0, 1))
    rdDB = np.log10(np.abs(rdMap) + 1e-12)

    lo, hi = range_gate
    doppler_center = frameConfig.numDopplerBins // 2  # fftshift 후 정지(0속도) 위치

    # ROI: 거리 구간 [lo,hi] & zero-Doppler 주변(guard)은 정지 잔차라 제외
    roi = rdDB[:, lo:hi].copy()
    g = zero_doppler_guard
    roi[doppler_center - g: doppler_center + g + 1, :] = -100.0

    score = float(roi.max())
    # 참고 지표: ROI 평균 에너지도 같이 반환
    mean_energy = float(rdDB[:, lo:hi].mean())
    return score, mean_energy


def main():
    p = argparse.ArgumentParser(description='raw ADC bin -> 사람 있음/없음 판정')
    p.add_argument('bin_file')
    p.add_argument('--frames', type=int, default=0, help='0=파일 전체')
    p.add_argument('--range-min', type=float, default=0.2, help='탐지 최소 거리(m)')
    p.add_argument('--range-max', type=float, default=2.0, help='탐지 최대 거리(m)')
    p.add_argument('--doppler-guard', type=int, default=2,
                   help='zero-Doppler 주변 몇 bin을 정지 잔차로 제외할지')
    p.add_argument('--threshold', type=float, default=None,
                   help='occupancy score 임계값. 이 값 초과면 "사람 있음"')
    p.add_argument('--window', type=int, default=50,
                   help='시간통합 판정 윈도우 크기(프레임). 10FPS면 50=5초. '
                        '호흡 1주기(2~5초)를 여러 번 담을 만큼 길게.')
    p.add_argument('--present-ratio', type=float, default=0.15,
                   help='윈도우 안에서 임계 초과 프레임 비율이 이 값 이상이면 "사람 있음". '
                        '정지 호흡은 순간에너지가 주기적으로만 튀므로 100%%가 아니라 일부만 넘어도 존재로 본다.')
    p.add_argument('--calibrate', action='store_true',
                   help='판정 대신 score 통계만 출력(빈 공간 기준선 잡기용)')
    args = p.parse_args()

    frameConfig = FrameConfig()
    frame_bytes = frameConfig.frameSize * 4
    file_bytes = os.path.getsize(args.bin_file)
    max_frames = file_bytes // frame_bytes
    n = args.frames if 0 < args.frames <= max_frames else max_frames

    lo = max(int(args.range_min / cfg.RANGE_RESOLUTION), 1)
    hi = min(int(args.range_max / cfg.RANGE_RESOLUTION), frameConfig.numRangeBins)
    print('[info] frames=%d, ROI range bin [%d,%d] = %.2f~%.2f m'
          % (n, lo, hi, lo * cfg.RANGE_RESOLUTION, hi * cfg.RANGE_RESOLUTION))

    from collections import deque

    reader = RawDataReader(args.bin_file)
    scores = np.zeros(n)
    do_decide = (not args.calibrate) and (args.threshold is not None)
    recent = deque(maxlen=args.window)   # 최근 window개 프레임의 초과 여부(True/False)
    verdicts = np.zeros(n, dtype=bool)   # 프레임별 '시간통합 최종 판정'
    for i in range(n):
        bin_frame = reader.getNextFrame(frameConfig)
        np_frame = bin2np_frame(bin_frame)
        score, mean_e = frame_occupancy_score(np_frame, frameConfig, (lo, hi), args.doppler_guard)
        scores[i] = score

        if do_decide:
            raw_present = score > args.threshold
            recent.append(raw_present)
            # 윈도우 내 초과 비율로 최종 판정 (순간 깜빡임을 흡수)
            ratio = sum(recent) / len(recent)
            occupied = ratio >= args.present_ratio
            verdicts[i] = occupied
            print('Frame %4d | score=%6.3f | %s | win초과 %4.0f%% -> %s'
                  % (i, score,
                     'peak' if raw_present else '    ',
                     ratio * 100,
                     'PRESENT (사람있음)' if occupied else 'empty'))

    reader.close()

    print('=' * 50)
    print('score  min=%.3f  mean=%.3f  max=%.3f  std=%.3f'
          % (scores.min(), scores.mean(), scores.max(), scores.std()))
    if do_decide:
        occ_pct = 100.0 * verdicts.mean()
        peak_pct = 100.0 * np.mean(scores > args.threshold)
        print('순간 임계초과 프레임: %.1f%%   |   시간통합 "사람있음" 판정: %.1f%% of time'
              % (peak_pct, occ_pct))
        print('(정지 호흡이면 순간초과는 낮아도, 시간통합 판정은 거의 100%%에 가까워야 정상)')
    if args.calibrate:
        suggest = scores.max() + 2 * scores.std()
        print('[calibrate] 빈 공간 기준선. 사람 판정 threshold 권장값 ~= %.3f' % suggest)
        print('            (빈 공간 max보다 여유있게 위. 실제 사람 데이터로 재확인 필요)')
    elif args.threshold is None:
        print('[안내] --threshold 를 주지 않았습니다. 먼저 빈 공간 bin으로')
        print('       --calibrate 를 돌려 기준선을 잡으세요.')


if __name__ == '__main__':
    main()
