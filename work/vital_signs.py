"""
raw ADC bin -> 흉부 range bin phase -> 호흡수(RR) / 심박수(HR) 추출.

파이프라인:
  raw bin -> frameReshape -> rangeFFT (pc_generation 재사용)
  -> 프레임당 단일 TX/RX, loop 평균으로 range profile 확보
  -> 타깃 range bin 선택 (energy max 또는 phase-variance)
  -> np.angle -> np.unwrap -> 위상 시계열 (샘플레이트 = frame rate)
  -> 밴드패스(RR 0.1~0.5Hz / HR 0.8~2Hz) -> FFT 피크 -> BPM

주의(신호처리):
  - point cloud 경로처럼 3TX를 합치지 않는다. TDM MIMO는 TX마다 phase center가
    달라서 위상을 오염시킨다. 여기서는 단일 TX-RX 쌍만 사용.
  - "에너지 최대 bin"은 흉부가 아니라 정적 클러터(대시보드 등)를 고를 수 있다.
    --bin-select phase_var 옵션은 위상이 실제로 호흡/심박 대역에서 변조되는
    bin을 고르므로 더 안정적일 수 있다.
  - HR 대역(0.8~2Hz)에는 RR의 2nd/3rd harmonic이 들어올 수 있어 별도로 배제한다.
"""
import argparse
import os

import numpy as np
from scipy.signal import butter, filtfilt

import configuration as cfg
from pc_generation import FrameConfig, RawDataReader, bin2np_frame, frameReshape, rangeFFT


def load_range_profiles(bin_path, num_frames, tx_idx, rx_idx):
    """raw bin에서 프레임별 range profile(단일 TX/RX, loop 평균)을 뽑는다."""
    frameConfig = FrameConfig()
    frame_bytes = frameConfig.frameSize * 4
    file_bytes = os.path.getsize(bin_path)
    max_frames = file_bytes // frame_bytes
    if num_frames <= 0 or num_frames > max_frames:
        if 0 < max_frames < num_frames:
            print('[warn] requested %d frames but file has only %d. clamping.' % (num_frames, max_frames))
        num_frames = max_frames

    reader = RawDataReader(bin_path)
    profiles = np.zeros((num_frames, frameConfig.numRangeBins), dtype=np.complex128)
    for i in range(num_frames):
        bin_frame = reader.getNextFrame(frameConfig)
        np_frame = bin2np_frame(bin_frame)
        reshaped = frameReshape(np_frame, frameConfig)     # (tx, rx, loops, samples)
        rfft = rangeFFT(reshaped, frameConfig)             # (tx, rx, loops, samples), complex
        # 프레임 내 128 loop는 ~수십ms 안에 끝나 타깃이 정적이므로 평균으로 노이즈만 줄인다.
        profiles[i] = rfft[tx_idx, rx_idx].mean(axis=0)    # -> (samples,)
    reader.close()
    return profiles, num_frames


def bandpass_filter(x, fps, low, high, order=4):
    nyq = fps / 2.0
    low_n = max(low / nyq, 1e-6)
    high_n = min(high / nyq, 0.999)
    b, a = butter(order, [low_n, high_n], btype='band')
    return filtfilt(b, a, x)


def select_target_bin(profiles, fps, method='energy', bin_lo=1, bin_hi=None):
    """타깃(흉부) range bin 선택.

    energy    : 요청 스펙대로 시간평균 진폭이 가장 큰 bin (정적 클러터에 낚일 수 있음).
    phase_var : 각 bin의 위상을 0.1~2Hz로 밴드패스한 뒤 분산이 가장 큰 bin.
                흉부만 실제로 그 대역에서 변조되므로 더 견고함.
    """
    n_bins = profiles.shape[1]
    if bin_hi is None:
        bin_hi = n_bins
    bin_lo = max(bin_lo, 1)  # bin 0(DC 근접)은 제외
    window = slice(bin_lo, bin_hi)

    if method == 'energy':
        metric = np.abs(profiles).mean(axis=0)
    elif method == 'phase_var':
        metric = np.zeros(n_bins)
        for b in range(bin_lo, bin_hi):
            phase = np.unwrap(np.angle(profiles[:, b]))
            filtered = bandpass_filter(phase, fps, 0.1, 2.0)
            metric[b] = np.var(filtered)
    else:
        raise ValueError('unknown bin-select method: %s' % method)

    local_idx = np.argmax(metric[window])
    return window.start + local_idx, metric


def fft_peak_bpm(signal, fps, band, zero_pad_factor=8, exclude_freqs=None, exclude_tol=0.05):
    """밴드 내 FFT 피크 주파수를 BPM으로 반환. exclude_freqs 근방은 후보에서 제외."""
    n = len(signal)
    nfft = int(2 ** np.ceil(np.log2(max(n * zero_pad_factor, n))))
    window = np.hanning(n)
    spec = np.abs(np.fft.rfft(signal * window, n=nfft))
    freqs = np.fft.rfftfreq(nfft, d=1.0 / fps)

    mask = (freqs >= band[0]) & (freqs <= band[1])
    if exclude_freqs:
        for ef in exclude_freqs:
            mask &= np.abs(freqs - ef) > exclude_tol

    if not np.any(mask):
        return None, freqs, spec

    band_freqs = freqs[mask]
    band_spec = spec[mask]
    peak_freq = band_freqs[np.argmax(band_spec)]
    return peak_freq * 60.0, freqs, spec


def plot_results(t, phase, rr_signal, hr_signal, rr_freqs, rr_spec, hr_freqs, hr_spec,
                  rr_bpm, hr_bpm, target_range_m, out_path='vital_signs_output.png'):
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(3, 2, figsize=(12, 9))
    fig.suptitle('Vital signs @ target range = %.3f m' % target_range_m)

    axes[0, 0].plot(t, phase)
    axes[0, 0].set_title('Unwrapped chest phase (raw)')
    axes[0, 0].set_xlabel('time (s)')
    axes[0, 0].set_ylabel('rad')

    rr_label = '%.1f' % rr_bpm if rr_bpm else 'N/A'
    axes[1, 0].plot(t, rr_signal, color='tab:green')
    axes[1, 0].set_title('Respiration band (0.1-0.5Hz)  RR=%s BPM' % rr_label)
    axes[1, 0].set_xlabel('time (s)')

    hr_label = '%.1f' % hr_bpm if hr_bpm else 'N/A'
    axes[2, 0].plot(t, hr_signal, color='tab:red')
    axes[2, 0].set_title('Heartbeat band (0.8-2Hz)  HR=%s BPM' % hr_label)
    axes[2, 0].set_xlabel('time (s)')

    mask_rr = rr_freqs <= 1.0
    axes[0, 1].plot(rr_freqs[mask_rr] * 60, rr_spec[mask_rr])
    axes[0, 1].axvspan(0.1 * 60, 0.5 * 60, color='green', alpha=0.15)
    if rr_bpm:
        axes[0, 1].axvline(rr_bpm, color='k', linestyle='--')
    axes[0, 1].set_title('RR spectrum')
    axes[0, 1].set_xlabel('BPM')

    mask_hr = hr_freqs <= 3.0
    axes[1, 1].plot(hr_freqs[mask_hr] * 60, hr_spec[mask_hr])
    axes[1, 1].axvspan(0.8 * 60, 2.0 * 60, color='red', alpha=0.15)
    if hr_bpm:
        axes[1, 1].axvline(hr_bpm, color='k', linestyle='--')
    axes[1, 1].set_title('HR spectrum (RR harmonics excluded)')
    axes[1, 1].set_xlabel('BPM')

    axes[2, 1].axis('off')

    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    print('[info] saved plot -> %s' % out_path)
    plt.show()


def main():
    p = argparse.ArgumentParser(description='raw ADC bin -> RR/HR 추출')
    p.add_argument('bin_file')
    p.add_argument('--frames', type=int, default=0, help='0=파일 전체 사용')
    p.add_argument('--fps', type=float, default=10.0,
                   help='frame rate(Hz). 캡처 당시 mmWave Studio periodicity와 반드시 일치시킬 것')
    p.add_argument('--tx', type=int, default=0)
    p.add_argument('--rx', type=int, default=0)
    p.add_argument('--range-min', type=float, default=0.2, help='타깃 탐색 최소 거리(m)')
    p.add_argument('--range-max', type=float, default=1.5, help='타깃 탐색 최대 거리(m)')
    p.add_argument('--bin-select', choices=['energy', 'phase_var'], default='energy')
    p.add_argument('--no-plot', action='store_true')
    args = p.parse_args()

    profiles, n_frames = load_range_profiles(args.bin_file, args.frames, args.tx, args.rx)
    duration = n_frames / args.fps
    print('[info] frames=%d, fps=%.2f, duration=%.1fs' % (n_frames, args.fps, duration))
    print('[info] FFT 주파수 분해능 ~= %.2f BPM (60s+ 캡처 권장, KPI RMSE<=2bpm 기준)'
          % (60.0 / duration))

    bin_lo = max(int(args.range_min / cfg.RANGE_RESOLUTION), 1)
    bin_hi = min(int(args.range_max / cfg.RANGE_RESOLUTION), profiles.shape[1])
    target_bin, metric = select_target_bin(profiles, args.fps, method=args.bin_select,
                                            bin_lo=bin_lo, bin_hi=bin_hi)
    target_range_m = target_bin * cfg.RANGE_RESOLUTION
    print('[info] target range bin = %d (%.3f m), select method=%s'
          % (target_bin, target_range_m, args.bin_select))
    if target_bin < 25:
        # pc_generation.py의 frame2pointcloud도 bin<25(~1.07m)를 RangeCut으로 버림.
        # 이 근접 bin은 실제 타깃보다 TX-RX coupling leakage일 가능성이 높다.
        print('[warn] target bin이 근접 영역(< bin 25, ~1.07m)에 있음. '
              '실제 흉부가 아니라 TX-RX coupling leakage를 골랐을 수 있음. '
              '--range-min 을 실제 레이더-흉부 거리에 맞게 올려서 재확인 권장.')

    phase = np.unwrap(np.angle(profiles[:, target_bin]))
    t = np.arange(n_frames) / args.fps

    rr_signal = bandpass_filter(phase, args.fps, 0.1, 0.5)
    rr_bpm, rr_freqs, rr_spec = fft_peak_bpm(rr_signal, args.fps, (0.1, 0.5))

    hr_signal = bandpass_filter(phase, args.fps, 0.8, 2.0)
    exclude = None
    if rr_bpm is not None:
        rr_freq = rr_bpm / 60.0
        exclude = [rr_freq * 2, rr_freq * 3]
    hr_bpm, hr_freqs, hr_spec = fft_peak_bpm(hr_signal, args.fps, (0.8, 2.0), exclude_freqs=exclude)

    print('=' * 44)
    print('호흡수 (RR): %s BPM' % ('%.1f' % rr_bpm if rr_bpm is not None else 'N/A'))
    print('심박수 (HR): %s BPM (RR 2nd/3rd harmonic 제외)'
          % ('%.1f' % hr_bpm if hr_bpm is not None else 'N/A'))
    print('=' * 44)

    if not args.no_plot:
        plot_results(t, phase, rr_signal, hr_signal, rr_freqs, rr_spec, hr_freqs, hr_spec,
                     rr_bpm, hr_bpm, target_range_m)


if __name__ == '__main__':
    main()
