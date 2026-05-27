"""
IWR1843 UART 실시간 수신 파이프라인 (3주차)
==============================================
사용법:
  python uart_receiver.py --config COM3 --data COM4
  python uart_receiver.py --config /dev/tty.SLAB_USBtoUART --data /dev/tty.SLAB_USBtoUART5

흐름:
  1. Config Port로 .cfg 파일 전송 → 레이더 동작 시작
  2. Data Port에서 TLV 패킷 실시간 수신
  3. parse_frame()으로 포인트 클라우드 추출
  4. 콘솔 출력 (추후 4주차 ROI 필터링 등 연결 예정)
"""

import argparse
import time
import threading
import queue
import serial
import numpy as np

from tlv_parser import extract_frame_from_buffer, parse_frame

# ────────────────────────────────────────────────
# 기본 설정값
# ────────────────────────────────────────────────
CONFIG_BAUD  = 115200
DATA_BAUD    = 921600
READ_TIMEOUT = 1.0        # serial read timeout (초)
BUFFER_MAX   = 2 ** 20   # 수신 버퍼 최대 1MB

# IWR1843 기본 cfg 명령 (최소한의 동작 설정)
# 실제 프로젝트에서는 .cfg 파일을 읽어서 전송하세요
DEFAULT_CFG_COMMANDS = [
    "sensorStop",
    "flushCfg",
    "dfeDataOutputMode 1",
    "channelCfg 15 7 0",
    "adcCfg 2 1",
    "adcbufCfg -1 0 1 1 1",
    "profileCfg 0 77 7 7 57.14 0 0 70.295 1 256 5209 0 0 158",
    "chirpCfg 0 0 0 0 0 0 0 1",
    "chirpCfg 1 1 0 0 0 0 0 4",
    "chirpCfg 2 2 0 0 0 0 0 2",
    "frameCfg 0 2 16 0 100 1 0",
    "lowPower 0 0",
    "guiMonitor -1 1 0 0 0 0 1",
    "cfarCfg -1 0 2 8 4 3 0 15.0 0",
    "cfarCfg -1 1 0 4 2 3 1 15.0 0",
    "multiObjBeamForming -1 1 0.5",
    "clutterRemoval -1 0",
    "calibDcRangeSig -1 0 -5 8 256",
    "extendedMaxVelocity -1 0",
    "bpmCfg -1 0 0 0",
    "lvdsStreamCfg -1 0 0 0",
    "compRangeBiasAndRxChanPhase 0.0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0",
    "measureRangeBiasAndRxChanPhase 0 1.5 0.2",
    "CQRxSatMonitor 0 3 5 121 0",
    "CQSigImgMonitor 0 127 4",
    "analogMonitor 0 0",
    "aoaFovCfg -1 -90 90 -90 90",
    "cfarFovCfg -1 0 0 8.92",
    "cfarFovCfg -1 1 -1 1.00",
    "sensorStart",
]


# ────────────────────────────────────────────────
# Config Port: 설정 명령 전송
# ────────────────────────────────────────────────
def send_config(config_port: str, commands: list[str]) -> bool:
    """
    Config UART 포트로 설정 명령어 목록을 순서대로 전송.

    Parameters
    ----------
    config_port : str
        Config 포트 이름 (예: 'COM3', '/dev/tty.SLAB_USBtoUART')
    commands : list[str]
        전송할 CLI 명령어 목록

    Returns
    -------
    bool
        성공 시 True
    """
    print(f"[INFO] Config Port 연결: {config_port} @ {CONFIG_BAUD} baud")
    try:
        ser = serial.Serial(
            port=config_port,
            baudrate=CONFIG_BAUD,
            timeout=READ_TIMEOUT,
        )
    except serial.SerialException as e:
        print(f"[ERROR] Config Port 열기 실패: {e}")
        return False

    try:
        time.sleep(0.5)
        for cmd in commands:
            cmd_bytes = (cmd + '\n').encode('utf-8')
            ser.write(cmd_bytes)
            time.sleep(0.05)  # 보드 처리 대기

            # 응답 확인 (선택적)
            response = ser.read(ser.in_waiting or 1)
            if response:
                print(f"  >> {cmd!r}  ← {response.decode('utf-8', errors='ignore').strip()}")
            else:
                print(f"  >> {cmd!r}")

        print("[INFO] 설정 전송 완료. 레이더 시작됨.")
        return True

    except Exception as e:
        print(f"[ERROR] 설정 전송 중 오류: {e}")
        return False

    finally:
        ser.close()


def send_config_from_file(config_port: str, cfg_path: str) -> bool:
    """
    .cfg 파일을 읽어서 Config Port로 전송.

    Parameters
    ----------
    cfg_path : str
        mmWave Studio 또는 직접 작성한 .cfg 파일 경로
    """
    try:
        with open(cfg_path, 'r') as f:
            commands = [
                line.strip()
                for line in f
                if line.strip() and not line.startswith('%')  # 주석 제거
            ]
        return send_config(config_port, commands)
    except FileNotFoundError:
        print(f"[ERROR] .cfg 파일 없음: {cfg_path}")
        return False


# ────────────────────────────────────────────────
# Data Port: 실시간 수신 스레드
# ────────────────────────────────────────────────
class UARTReceiver:
    """
    UART Data Port에서 TLV 패킷을 실시간으로 수신·파싱하는 클래스.

    사용 예:
        receiver = UARTReceiver('COM4')
        receiver.start()

        while True:
            frame = receiver.get_frame(timeout=0.5)
            if frame:
                points = frame['points']  # np.ndarray (N,4)
                print(f"검출 포인트 수: {len(points)}")
    """

    def __init__(self, data_port: str, baud: int = DATA_BAUD):
        self.data_port = data_port
        self.baud      = baud
        self._serial   = None
        self._buffer   = bytearray()
        self._frame_q  = queue.Queue(maxsize=10)
        self._running  = False
        self._thread   = None
        self.frame_count = 0

    def start(self) -> bool:
        """수신 스레드 시작."""
        print(f"[INFO] Data Port 연결: {self.data_port} @ {self.baud} baud")
        try:
            self._serial = serial.Serial(
                port=self.data_port,
                baudrate=self.baud,
                timeout=READ_TIMEOUT,
            )
        except serial.SerialException as e:
            print(f"[ERROR] Data Port 열기 실패: {e}")
            return False

        self._running = True
        self._thread  = threading.Thread(target=self._receive_loop, daemon=True)
        self._thread.start()
        print("[INFO] 수신 스레드 시작됨.")
        return True

    def stop(self):
        """수신 스레드 종료."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._serial and self._serial.is_open:
            self._serial.close()
        print("[INFO] 수신 종료.")

    def get_frame(self, timeout: float = 1.0) -> dict | None:
        """
        파싱 완료된 프레임을 큐에서 꺼내 반환.
        timeout 초 내에 프레임이 없으면 None 반환.
        """
        try:
            return self._frame_q.get(timeout=timeout)
        except queue.Empty:
            return None

    def _receive_loop(self):
        """백그라운드 수신 루프 (별도 스레드에서 실행)."""
        print("[INFO] 데이터 수신 대기 중...")

        while self._running:
            try:
                # 읽을 수 있는 바이트 수 확인
                waiting = self._serial.in_waiting
                if waiting > 0:
                    chunk = self._serial.read(waiting)
                    self._buffer.extend(chunk)
                else:
                    # 데이터 없으면 짧게 대기
                    time.sleep(0.005)
                    continue

                # 버퍼 과다 시 앞부분 제거 (메모리 보호)
                if len(self._buffer) > BUFFER_MAX:
                    print(f"[WARN] 버퍼 초과 — 앞 512KB 제거")
                    self._buffer = self._buffer[BUFFER_MAX // 2:]

                # 버퍼에서 완전한 프레임 추출 (Magic Word 기준)
                while True:
                    frame_bytes, self._buffer = extract_frame_from_buffer(self._buffer)
                    if frame_bytes is None:
                        break

                    # TLV 파싱
                    frame_data = parse_frame(frame_bytes)
                    if frame_data is None:
                        continue

                    self.frame_count += 1

                    # 큐가 꽉 차있으면 가장 오래된 프레임 버리기 (실시간성 유지)
                    if self._frame_q.full():
                        try:
                            self._frame_q.get_nowait()
                        except queue.Empty:
                            pass

                    self._frame_q.put_nowait(frame_data)

            except serial.SerialException as e:
                print(f"[ERROR] 시리얼 읽기 오류: {e}")
                break
            except Exception as e:
                print(f"[ERROR] 예상치 못한 오류: {e}")
                break

        print("[INFO] 수신 루프 종료.")


# ────────────────────────────────────────────────
# 메인 실행 — 실시간 콘솔 출력 데모
# ────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='IWR1843 UART TLV 실시간 파서')
    parser.add_argument('--config', type=str, required=True,
                        help='Config UART 포트 (예: COM3 또는 /dev/tty.SLAB_USBtoUART)')
    parser.add_argument('--data', type=str, required=True,
                        help='Data UART 포트 (예: COM4 또는 /dev/tty.SLAB_USBtoUART5)')
    parser.add_argument('--cfg-file', type=str, default=None,
                        help='.cfg 파일 경로 (없으면 내장 기본값 사용)')
    parser.add_argument('--frames', type=int, default=0,
                        help='수신할 최대 프레임 수 (0=무한)')
    args = parser.parse_args()

    # ── Step 1: 설정 전송 ──────────────────────────
    if args.cfg_file:
        success = send_config_from_file(args.config, args.cfg_file)
    else:
        print("[INFO] 내장 기본 설정 사용 (실제 사용 시 .cfg 파일 권장)")
        success = send_config(args.config, DEFAULT_CFG_COMMANDS)

    if not success:
        print("[ERROR] 설정 전송 실패. 종료합니다.")
        return

    time.sleep(1.0)  # 레이더 초기화 대기

    # ── Step 2: Data Port 수신 시작 ────────────────
    receiver = UARTReceiver(args.data)
    if not receiver.start():
        return

    # ── Step 3: 실시간 데이터 소비 루프 ─────────────
    print("\n[INFO] 실시간 포인트 클라우드 수신 중... (Ctrl+C로 종료)\n")
    print(f"{'프레임':>6} {'검출수':>6}  {'X(m)':>8} {'Y(m)':>8} {'Z(m)':>8} {'V(m/s)':>8}")
    print("-" * 55)

    try:
        frame_idx = 0
        while True:
            frame = receiver.get_frame(timeout=1.0)
            if frame is None:
                print("[WARN] 1초간 프레임 없음 — 연결 확인 필요")
                continue

            header = frame['header']
            points = frame['points']  # np.ndarray (N, 4)
            num    = len(points)

            if num > 0:
                # 첫 번째 포인트만 콘솔 출력 (전체는 배열로 보유)
                x, y, z, v = points[0]
                print(f"{header['frame_number']:>6} {num:>6}  "
                      f"{x:>8.3f} {y:>8.3f} {z:>8.3f} {v:>8.3f}  ← 1st point")
            else:
                print(f"{header['frame_number']:>6} {num:>6}  (검출 없음)")

            frame_idx += 1
            if args.frames > 0 and frame_idx >= args.frames:
                print(f"\n[INFO] {args.frames}프레임 수신 완료.")
                break

    except KeyboardInterrupt:
        print("\n[INFO] 사용자 중단.")

    finally:
        receiver.stop()
        print(f"[INFO] 총 수신 프레임: {receiver.frame_count}")


if __name__ == '__main__':
    main()
