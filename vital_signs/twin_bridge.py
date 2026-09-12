"""Official Vital Signs UART → digital twin (no Industrial Visualizer).

IWR6843ISK official .bin still only speaks CLI/DATA UART. This process
opens those ports, sends the official cfg, parses tracker + vitals TLVs,
and POSTs to http://127.0.0.1:8766/event.
"""

from __future__ import annotations

import argparse
import struct
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
GUI_ROOT = REPO_ROOT / 'computer_GUI'
if str(GUI_ROOT) not in sys.path:
    sys.path.insert(0, str(GUI_ROOT))

from digital_twin.bridge import TwinPublisher
from drowsiness_judge import DrowsinessJudge

ISK_REARVIEW_CFG = HERE / 'chirp_configs' / 'vital_signs_ISK_rearview.cfg'

MAGIC = b'\x02\x01\x04\x03\x06\x05\x08\x07'
HEADER_LEN = 40
TLV_HDR = struct.Struct('<II')
FRAME_HDR = struct.Struct('<Q8I')
TRACK_STRUCT = struct.Struct('<I27f')
VITALS_STRUCT = struct.Struct('<2H33f')

TLV_TRACK = 1010
TLV_VITALS = 1040

HOLD_DEVIATION = 0.025
HOLD_EXIT_DEV = 0.04
HOLD_BREATH_MAX = 0.10
HOLD_ENTER_PACKETS = 2
HOLD_EXIT_PACKETS = 3
DEV_SMOOTH_N = 3
HOLD_ARM_SEC = 5.0
HOLD_PTP_FLAT = 0.5
HOLD_PTP_JUMP = 1.0
HOLD_PTP_RATIO = 5.0
MOTION_SPEED = 0.40
MOTION_QUIET_FRAMES = 10
MOTION_STALE_FRAMES = 33
HOLD_WARNING_SEC = 10.0
EMPTY_CLEAR_SEC = 3.0
HR_MEDIAN_N = 10


def _median(values):
    good = [float(v) for v in values if v is not None and float(v) > 0]
    if not good:
        return None
    good.sort()
    mid = len(good) // 2
    if len(good) % 2:
        return good[mid]
    return (good[mid - 1] + good[mid]) / 2.0


def parse_frame(packet):
    """Return {ntracks, tracks, vitals} or None if the header is bad."""
    if len(packet) < HEADER_LEN:
        return None
    try:
        _magic, _ver, total, _plat, _fn, _cpu, _nobj, num_tlvs, _sub = FRAME_HDR.unpack(
            packet[:HEADER_LEN])
    except struct.error:
        return None
    if total < HEADER_LEN or total > 65536 or num_tlvs > 64:
        return None

    out = {'ntracks': None, 'tracks': [], 'vitals': None}
    offset = HEADER_LEN
    for _ in range(num_tlvs):
        if offset + TLV_HDR.size > len(packet):
            break
        tlv_type, tlv_len = TLV_HDR.unpack_from(packet, offset)
        offset += TLV_HDR.size
        payload = packet[offset:offset + tlv_len]
        offset += tlv_len
        if len(payload) < tlv_len:
            break
        if tlv_type == TLV_TRACK:
            n = tlv_len // TRACK_STRUCT.size
            tracks = []
            for i in range(n):
                rec = TRACK_STRUCT.unpack_from(payload, i * TRACK_STRUCT.size)
                tracks.append({
                    'id': rec[0],
                    'x': rec[1], 'y': rec[2], 'z': rec[3],
                    'vx': rec[4], 'vy': rec[5], 'vz': rec[6],
                })
            out['ntracks'] = n
            out['tracks'] = tracks
        elif tlv_type == TLV_VITALS and tlv_len >= VITALS_STRUCT.size:
            rec = VITALS_STRUCT.unpack_from(payload, 0)
            out['vitals'] = {
                'id': rec[0],
                'range_bin': rec[1],
                'breath_deviation': rec[2],
                'heart_rate': rec[3],
                'breath_rate': rec[4],
                'heart_wave': rec[5:20],
                'breath_wave': rec[20:35],
            }
    return out


class OfficialVitals:
    def __init__(self):
        self.label = ''
        self.ntracks = 0
        self.hr = 0
        self.rr = 0
        self.deviation = None
        self.range_bin = None
        self.apnea_sec = 0.0
        self.in_motion = False
        self.motion_quiet = 0
        self.motion_frames = 0
        self.hold_started = None
        self.hold_exit = 0
        self.hold_enter = 0
        self.hold_armed = False
        self.vitals_since = None
        self.hold_wave_ptp = None
        self.empty_since = None
        self.dev_hist = []
        self.hr_hist = []
        self.rr_hist = []
        self._last_wave = None
        self.judge = DrowsinessJudge()   # 개인 기준선 이탈 모니터 (3.3절)
        self.drowsy_state = 'CALIB'

    def _reset_hold(self):
        self.hold_started = None
        self.hold_exit = 0
        self.hold_enter = 0
        self.hold_wave_ptp = None
        self.apnea_sec = 0.0

    def _reset_hold_arm(self):
        self._reset_hold()
        self.hold_armed = False
        self.vitals_since = None
        self.dev_hist = []

    def _reset_motion(self):
        self.in_motion = False
        self.motion_quiet = 0
        self.motion_frames = 0

    def _smooth_dev(self, deviation):
        self.dev_hist.append(float(deviation))
        while len(self.dev_hist) > DEV_SMOOTH_N:
            self.dev_hist.pop(0)
        return sum(self.dev_hist) / len(self.dev_hist)

    def _packet_ptp(self):
        wf = self._last_wave
        if not wf:
            return 0.0
        return float(max(wf) - min(wf))

    def _unwrap_jump(self, deviation):
        if deviation >= HOLD_BREATH_MAX:
            return True
        ptp = self._packet_ptp()
        last = self.hold_wave_ptp
        if last is not None and last < HOLD_PTP_FLAT and ptp >= max(HOLD_PTP_JUMP, last * HOLD_PTP_RATIO):
            return True
        return False

    def _tick_hold(self):
        now = time.monotonic()
        if self.hold_started is None:
            self.hold_started = now
        self.apnea_sec = max(0.0, now - self.hold_started)
        if self.apnea_sec >= HOLD_WARNING_SEC:
            return 'Warning'
        return 'Hold'

    def _classify(self, deviation):
        low = deviation < HOLD_DEVIATION
        breathing_again = HOLD_EXIT_DEV <= deviation < HOLD_BREATH_MAX
        if self.vitals_since is None:
            self.vitals_since = time.monotonic()
        if breathing_again or (time.monotonic() - self.vitals_since >= HOLD_ARM_SEC):
            self.hold_armed = True
        in_hold = self.label in ('Hold', 'Warning') or self.hold_started is not None
        if in_hold:
            if low:
                self.hold_exit = 0
                return self._tick_hold(), False
            if self._unwrap_jump(deviation):
                return self._tick_hold(), True
            if breathing_again:
                self.hold_exit += 1
                if self.hold_exit >= HOLD_EXIT_PACKETS:
                    self._reset_hold()
                    return 'Breathing', False
                return self._tick_hold(), False
            self.hold_exit = 0
            return self._tick_hold(), False
        if not self.hold_armed:
            self.hold_enter = 0
            return 'Breathing', False
        if low:
            self.hold_enter += 1
            if self.hold_enter >= HOLD_ENTER_PACKETS:
                return self._tick_hold(), False
            return 'Breathing', False
        self.hold_enter = 0
        return 'Breathing', False

    def _track_speed(self, tracks):
        speed = 0.0
        for t in tracks:
            mag = (t['vx'] ** 2 + t['vy'] ** 2 + t['vz'] ** 2) ** 0.5
            if mag > speed:
                speed = mag
        return speed

    def _update_motion(self, speed):
        if speed >= MOTION_SPEED:
            self.in_motion = True
            self.motion_quiet = 0
            self.motion_frames += 1
        else:
            if self.in_motion:
                self.motion_frames += 1
            self.motion_quiet += 1
            if self.motion_quiet >= MOTION_QUIET_FRAMES:
                self.in_motion = False
                self.motion_frames = 0
        return self.in_motion

    def _show_empty(self):
        self._reset_hold_arm()
        self.judge = DrowsinessJudge()
        self.drowsy_state = 'CALIB'
        self.ntracks = 0
        self.label = ''
        self.hr = 0
        self.rr = 0
        self.deviation = None
        self.range_bin = None
        self.hr_hist = []
        self.rr_hist = []

    def update(self, frame):
        vitals = frame.get('vitals')
        track_reported = frame.get('ntracks') is not None
        if track_reported:
            self.ntracks = int(frame['ntracks'])
        if vitals is not None:
            self.deviation = float(vitals['breath_deviation'])
            self.range_bin = int(vitals['range_bin'])
            self._last_wave = vitals.get('breath_wave')

        if track_reported and self.ntracks == 0:
            self._reset_motion()
            if self.empty_since is None:
                self.empty_since = time.monotonic()
            if time.monotonic() - self.empty_since >= EMPTY_CLEAR_SEC:
                self._show_empty()
                return
            if self.label in ('Hold', 'Warning'):
                self.label = self._tick_hold()
            return

        if track_reported:
            self.empty_since = None
            if self._update_motion(self._track_speed(frame.get('tracks') or [])):
                self._reset_hold()
                self.label = 'Motion'
                if self.motion_frames >= MOTION_STALE_FRAMES:
                    self.hr = 0
                    self.rr = 0
                    self.hr_hist = []
                    self.rr_hist = []
                return

        if vitals is None:
            if self.label in ('Hold', 'Warning'):
                self.label = self._tick_hold()
            return

        deviation = self._smooth_dev(float(vitals['breath_deviation']))
        label, skip_wave = self._classify(deviation)
        if not skip_wave:
            hr = float(vitals['heart_rate'])
            rr = float(vitals['breath_rate'])
            if hr > 0:
                self.hr_hist.append(hr)
                while len(self.hr_hist) > HR_MEDIAN_N:
                    self.hr_hist.pop(0)
                self.drowsy_state = self.judge.update(time.monotonic(), hr)
            if rr > 0:
                self.rr_hist.append(rr)
                while len(self.rr_hist) > HR_MEDIAN_N:
                    self.rr_hist.pop(0)
            if label in ('Hold', 'Warning'):
                self.hold_wave_ptp = self._packet_ptp()

        med_hr = _median(self.hr_hist)
        self.hr = 0 if med_hr is None else int(round(med_hr))
        if label in ('Hold', 'Warning'):
            self.rr = 0
        else:
            rr = float(vitals['breath_rate'])
            self.rr = 0 if rr <= 0 else int(round(rr))
        self.label = label

    def payload(self):
        empty = (self.label == '') and self.ntracks == 0
        detail = self.label or '검색 중'
        if self.deviation is not None:
            detail = '{}  dev={:.4f}'.format(detail, self.deviation)
        status = 'SEARCHING' if empty else (self.label.upper() if self.label else 'NORMAL')
        return {
            'source': 'vital_signs',
            'status': status,
            'present': (not empty) and self.ntracks > 0,
            'rr': 0 if empty else self.rr,
            'hr': 0 if empty else self.hr,
            'apnea_sec': round(self.apnea_sec, 1),
            'detail': detail,
            'drowsy_state': self.drowsy_state,   # 'CALIB' | 'NORMAL' | 'DEVIATED'
        }


def read_cfg_lines(path):
    lines = []
    with open(path, 'r', encoding='utf-8') as fp:
        for line in fp:
            raw = line.strip()
            if not raw or raw.startswith('%'):
                continue
            lines.append(raw)
    return lines


def send_cfg(cli, lines):
    if cli.in_waiting:
        leftover = cli.read(cli.in_waiting)
        if leftover:
            print(leftover.decode('utf-8', errors='replace'), end='')
    for cmd in lines:
        cli.write((cmd + '\n').encode('utf-8'))
        print(cmd)
        deadline = time.monotonic() + (6.0 if cmd.startswith('sensorStart') else 3.0)
        while time.monotonic() < deadline:
            resp = cli.readline()
            if not resp:
                continue
            text = resp.decode('utf-8', errors='replace')
            print(text, end='' if text.endswith('\n') else '\n')
            low = text.lower()
            if 'done' in low or 'error' in low or 'not recognized' in low:
                break
        time.sleep(0.08)


def start_twin_server():
    from digital_twin.server import HOST, PORT, TwinHandler
    from http.server import ThreadingHTTPServer

    try:
        server = ThreadingHTTPServer((HOST, PORT), TwinHandler)
    except OSError as exc:
        print('Digital twin: already running ({})'.format(exc))
        return None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print('Digital twin: http://{}:{}'.format(HOST, PORT))
    return server


def open_serial(port, baud, timeout):
    try:
        import serial
    except ImportError:
        print('pyserial 이 필요합니다.  vital_signs\\.venv 또는 pip install pyserial')
        raise
    return serial.Serial(port, baud, timeout=timeout)


def run(cli_port, data_port, cfg_path, twin_url, serve):
    cfg_path = Path(cfg_path)
    if not cfg_path.is_file():
        print('cfg 없음:', cfg_path)
        return 1

    if serve:
        start_twin_server()

    print('Visualizer 는 끄세요. COM 이 겹칩니다.')
    print('CLI  {}  115200'.format(cli_port))
    print('DATA {}  921600'.format(data_port))
    print('cfg ', cfg_path)
    print('보드 NRST 후 이 스크립트가 cfg 를 보냅니다.')
    print()

    try:
        cli = open_serial(cli_port, 115200, timeout=0.4)
        data = open_serial(data_port, 921600, timeout=0.05)
    except Exception as exc:
        print('COM 열기 실패:', exc)
        print('Visualizer / occupancy GUI / 다른 run_twin 이 켜져 있으면 포트를 닫으세요.')
        return 1

    send_cfg(cli, read_cfg_lines(cfg_path))
    vitals = OfficialVitals()
    twin = TwinPublisher(url=twin_url)
    buf = bytearray()
    last_print = 0.0
    frames = 0

    try:
        while True:
            chunk = data.read(8192)
            if chunk:
                buf.extend(chunk)
            leftover = cli.read(cli.in_waiting or 0)
            if leftover:
                print(leftover.decode('utf-8', errors='replace'), end='')

            while True:
                idx = buf.find(MAGIC)
                if idx < 0:
                    if len(buf) > 8:
                        del buf[:-7]
                    break
                if idx:
                    del buf[:idx]
                if len(buf) < HEADER_LEN:
                    break
                total = int.from_bytes(buf[12:16], 'little')
                if total < HEADER_LEN or total > 65536:
                    del buf[:8]
                    continue
                if len(buf) < total:
                    break
                packet = bytes(buf[:total])
                del buf[:total]
                parsed = parse_frame(packet)
                if parsed is None:
                    continue
                vitals.update(parsed)
                twin.publish_event(vitals.payload())
                frames += 1

            now = time.monotonic()
            if now - last_print >= 1.0:
                last_print = now
                p = vitals.payload()
                print('[{:5d}] {:<10}  HR={:3d}  RR={:3d}  hold={:4.1f}s  tracks={}  {}'.format(
                    frames, p['status'], p['hr'], p['rr'], p['apnea_sec'],
                    vitals.ntracks, p['detail']))
            if not chunk:
                time.sleep(0.01)
    except KeyboardInterrupt:
        print('\nstopped')
    finally:
        try:
            cli.close()
            data.close()
        except Exception:
            pass
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Official ISK UART → digital twin (Visualizer 없이)')
    parser.add_argument('cli', nargs='?', default='COM4', help='CLI COM (Enhanced)')
    parser.add_argument('data', nargs='?', default='COM5', help='DATA COM (Standard)')
    parser.add_argument('cfg', nargs='?', default=str(ISK_REARVIEW_CFG), help='official .cfg')
    parser.add_argument('--twin-url', default='http://127.0.0.1:8766/event')
    parser.add_argument('--no-serve', action='store_true', help='트윈 서버를 이 프로세스에서 켜지 않음')
    args = parser.parse_args(argv)
    return run(args.cli, args.data, args.cfg, args.twin_url, serve=not args.no_serve)


if __name__ == '__main__':
    raise SystemExit(main())
