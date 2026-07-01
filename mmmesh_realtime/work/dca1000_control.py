"""
DCA1000EVM 제어 모듈 (mmWave Studio 없이 Python이 직접 제어).

DCA1000 User's Guide(SPRUIJ4A) 5장 Command Format 기반.
- 설정 명령은 config 포트(4096)로 보내고 같은 포트로 응답을 받음.
- 명령 패킷 구조: [Header 2B][Command code 2B][Data size 2B][Data ...][Footer 2B]
  Header = 0xA55A, Footer = 0xEEAA (little-endian)
- 응답 패킷: [Header][Command code][Status 2B][Footer], Status 0=성공 1=실패

이 모듈로 EthInit/FPGA config/packet delay/record start 를 보내면,
DCA1000이 LVDS 데이터를 data 포트(4098)로 UDP 스트리밍하기 시작한다.
"""
import socket
import struct
import time

# 기본 네트워크 설정 (DCA1000 기본값)
DCA_CMD_HEADER = 0xA55A
DCA_CMD_FOOTER = 0xEEAA

# Command codes (Table 12)
CMD_RESET_FPGA = 0x01
CMD_RESET_AR_DEV = 0x02
CMD_CONFIG_FPGA_GEN = 0x03
CMD_CONFIG_EEPROM = 0x04
CMD_RECORD_START = 0x05
CMD_RECORD_STOP = 0x06
CMD_PLAYBACK_START = 0x07
CMD_PLAYBACK_STOP = 0x08
CMD_SYSTEM_CONNECT = 0x09
CMD_SYSTEM_ERROR = 0x0A
CMD_CONFIG_PACKET_DATA = 0x0B
CMD_CONFIG_DATA_MODE_AR = 0x0C
CMD_INIT_FPGA_PLAYBACK = 0x0D
CMD_READ_FPGA_VERSION = 0x0E


class DCA1000Control:
    def __init__(self, static_ip='192.168.33.30', dca_ip='192.168.33.180',
                 config_port=4096):
        self.dca_cfg_addr = (dca_ip, config_port)
        self.cfg_recv = (static_ip, config_port)

        # config 소켓 (4096) — 명령 송수신용
        self.cfg_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.cfg_socket.bind(self.cfg_recv)
        self.cfg_socket.settimeout(3)

    def _build_command(self, cmd_code, data=b''):
        """[Header][cmd][size][data][Footer] 패킷 생성 (all little-endian)."""
        size = len(data)
        packet = struct.pack('<HHH', DCA_CMD_HEADER, cmd_code, size)
        packet += data
        packet += struct.pack('<H', DCA_CMD_FOOTER)
        return packet

    def _send_command(self, cmd_code, data=b'', name=''):
        """명령 전송 후 응답(status) 수신. status 0이면 성공."""
        packet = self._build_command(cmd_code, data)
        self.cfg_socket.sendto(packet, self.dca_cfg_addr)
        try:
            resp, _ = self.cfg_socket.recvfrom(2048)
            # 응답: [Header 2][cmd 2][status 2][Footer 2]
            if len(resp) >= 8:
                header, rcmd, status = struct.unpack('<HHH', resp[:6])
                ok = (status == 0)
                print('[DCA] %-18s -> status=%d (%s)' % (name or hex(cmd_code), status, 'OK' if ok else 'FAIL'))
                return ok, status
            else:
                print('[DCA] %-18s -> short response (%d bytes)' % (name, len(resp)))
                return False, -1
        except socket.timeout:
            print('[DCA] %-18s -> TIMEOUT (no response)' % (name or hex(cmd_code)))
            return False, -2

    # ---- 개별 명령 ----
    def system_connect(self):
        return self._send_command(CMD_SYSTEM_CONNECT, name='SYSTEM_CONNECT')

    def read_fpga_version(self):
        return self._send_command(CMD_READ_FPGA_VERSION, name='READ_FPGA_VERSION')

    def reset_fpga(self):
        return self._send_command(CMD_RESET_FPGA, name='RESET_FPGA')

    def config_fpga(self):
        """
        CONFIG_FPGA_GEN (0x03). data 6바이트:
        [logMode, lvdsMode, dataXfer, dataCapture, dataFormat, timer]
        mmWave Studio의 CaptureCardConfig_Mode(1,2,1,2,3,30) 에 대응:
          logMode=1(raw), lvdsMode=2(2-lane, 1843), dataXfer=1(capture),
          dataCapture=2(ethernet), dataFormat=3(16bit), timer=30
        """
        data = struct.pack('<BBBBBB', 1, 2, 1, 2, 3, 30)
        return self._send_command(CMD_CONFIG_FPGA_GEN, data, name='CONFIG_FPGA')

    def config_packet_delay(self, delay_us=25):
        """
        CONFIG_PACKET_DATA (0x0B). data 6바이트:
        [packetSize(2B)=1456, delay(2B), 0(2B)]
        """
        data = struct.pack('<HHH', 1456, delay_us, 0)
        return self._send_command(CMD_CONFIG_PACKET_DATA, data, name='CONFIG_PACKET_DELAY')

    def record_start(self):
        return self._send_command(CMD_RECORD_START, name='RECORD_START')

    def record_stop(self):
        return self._send_command(CMD_RECORD_STOP, name='RECORD_STOP')

    def start_streaming(self):
        """DCA가 데이터를 UDP로 흘려보내도록 시작시키는 전체 시퀀스."""
        print('--- DCA1000 streaming start sequence ---')
        self.system_connect()
        time.sleep(0.1)
        self.read_fpga_version()
        time.sleep(0.1)
        self.config_fpga()
        time.sleep(0.1)
        self.config_packet_delay(25)
        time.sleep(0.1)
        ok, _ = self.record_start()
        time.sleep(0.1)
        print('--- sequence done ---')
        return ok

    def close(self):
        self.cfg_socket.close()


if __name__ == '__main__':
    # 단독 테스트: DCA에 연결해서 FPGA 버전만 읽어봄
    dca = DCA1000Control()
    dca.system_connect()
    dca.read_fpga_version()
    dca.close()
