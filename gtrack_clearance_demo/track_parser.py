# gtrack_clearance_demo / track_parser.py
# ---------------------------------------------------------------------------
# 3D People Tracking 데모(오픈소스)의 UART 출력에서 '트랙 리스트'만 파싱.
# 포맷(비주얼라이저 parseFrame.py/parseTLVs.py 기준):
#   프레임헤더: 'Q8I' (magic uint64 + version,totalPacketLen,platform,frameNum,
#               cpuCycles,numDetectedObj,numTLVs,subFrameNum)
#   TLV헤더: type(uint32) length(uint32)
#   Target List TLV = type 308, 타깃당 'I27f'(112B):
#       tid(uint32) posX posY posZ velX velY velZ accX accY accZ ec[16] g conf
#   좌표계: X=측방(m), Y=전방/거리(m), Z=높이(m), 직교.
# ---------------------------------------------------------------------------
import struct
import math

MAGIC = b'\x02\x01\x04\x03\x06\x05\x08\x07'
HEADER_FMT = 'Q8I'
HEADER_SIZE = struct.calcsize(HEADER_FMT)          # 40
TLV_HDR_FMT = '2I'
TLV_HDR_SIZE = struct.calcsize(TLV_HDR_FMT)        # 8
# Target List TLV: 3D People Tracking 은 1010(TRACKERPROC_3D_TARGET_LIST),
# EXT 계열은 308 로 보냄 — 둘 다 동일한 'I27f' 타깃 구조. 1021=Presence(cold-start용).
TARGET_LIST_TLVS = (1010, 308)
PRESENCE_TLV = 1021
TARGET_FMT = 'I27f'
TARGET_SIZE = struct.calcsize(TARGET_FMT)          # 112

# 실차 좌우 규약 맞춤: 센서 X 부호가 실제 좌우와 반대 -> X 반전.
# (True 로 두면 X+ = 조수석쪽 이 되어 존 라벨과 실제 좌석이 일치)
FLIP_X = True

# 센서 마운트(월드 좌표 변환) — cfg sensorPosition <height> <azTilt> <elevTilt> 와 일치시킬 것.
# 비주얼라이저처럼 트랙을 틸트-역회전 후 센서높이만큼 z 를 올려 '바닥=z0, 센서=높이H' 로.
SENSOR_HEIGHT = 2.0   # m  (ISK_incabin_multi.cfg: sensorPosition 2 0 15)
AZ_TILT = 0.0         # deg
ELEV_TILT = 15.0      # deg


def euler_rot(x, y, z, elev_deg, az_deg):
    """센서 틸트 역회전 (visualizer graph_utilities.eulerRot 동일)."""
    e = math.radians(elev_deg)
    a = math.radians(az_deg)
    xr = math.cos(a) * x + math.cos(e) * math.sin(a) * y + math.sin(e) * math.sin(a) * z
    yr = -math.sin(a) * x + math.cos(e) * math.cos(a) * y + math.sin(e) * math.cos(a) * z
    zr = -math.sin(e) * y + math.cos(e) * z
    return xr, yr, zr


def to_world(x, y, z):
    """raw 센서프레임 -> 월드(FLIP_X + 틸트역회전 + 센서높이)."""
    if FLIP_X:
        x = -x
    xr, yr, zr = euler_rot(x, y, z, ELEV_TILT, AZ_TILT)
    return xr, yr, zr + SENSOR_HEIGHT


class Track:
    __slots__ = ('id', 'x', 'y', 'z', 'vx', 'vy', 'vz')

    def __init__(self, tid, x, y, z, vx=0.0, vy=0.0, vz=0.0):
        self.id = int(tid)
        self.x, self.y, self.z = x, y, z
        self.vx, self.vy, self.vz = vx, vy, vz

    @property
    def range_m(self):
        return (self.x ** 2 + self.y ** 2) ** 0.5

    def __repr__(self):
        return '#{} ({:.2f},{:.2f},{:.2f})'.format(self.id, self.x, self.y, self.z)


class FrameParser:
    """바이트 스트림을 먹여(feed) 완성된 프레임의 트랙 리스트들을 돌려준다."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        if data:
            self.buf += data
        frames = []
        while True:
            mi = self.buf.find(MAGIC)
            if mi < 0:
                if len(self.buf) > len(MAGIC):
                    self.buf = self.buf[-len(MAGIC):]   # 꼬리 보존(매직 걸침 대비)
                break
            if mi > 0:
                self.buf = self.buf[mi:]
            if len(self.buf) < HEADER_SIZE:
                break
            hdr = struct.unpack(HEADER_FMT, self.buf[:HEADER_SIZE])
            totalLen = hdr[2]      # totalPacketLen (32배수 패딩 포함)
            numTLVs = hdr[7]
            if totalLen < HEADER_SIZE or totalLen > 65536:
                self.buf = self.buf[len(MAGIC):]        # 헤더 이상 → 매직 건너뛰고 재탐색
                continue
            if len(self.buf) < totalLen:
                break                                    # 프레임 미완성 → 더 받기
            frame = bytes(self.buf[:totalLen])
            self.buf = self.buf[totalLen:]
            frames.append(self._parse_tracks(frame[HEADER_SIZE:], numTLVs))
        return frames

    def _parse_tracks(self, data, numTLVs):
        tracks = []
        idx = 0
        for _ in range(numTLVs):
            if idx + TLV_HDR_SIZE > len(data):
                break
            ttype, tlen = struct.unpack(TLV_HDR_FMT, data[idx:idx + TLV_HDR_SIZE])
            idx += TLV_HDR_SIZE
            payload = data[idx:idx + tlen]
            if ttype in TARGET_LIST_TLVS and tlen >= TARGET_SIZE:
                n = tlen // TARGET_SIZE
                for i in range(n):
                    t = struct.unpack(TARGET_FMT, payload[i * TARGET_SIZE:(i + 1) * TARGET_SIZE])
                    wx, wy, wz = to_world(t[1], t[2], t[3])   # 월드 좌표(바닥기준)
                    tracks.append(Track(t[0], wx, wy, wz))
            idx += tlen
        return tracks


if __name__ == '__main__':
    # 합성 프레임 1개로 파서 왕복 검증 (하드웨어 불필요)
    def make_frame(tracks):
        body = b''
        for (tid, x, y, z) in tracks:
            vals = [tid, x, y, z] + [0.0] * 24  # vel,acc,ec,g,conf = 0
            body += struct.pack(TARGET_FMT, *vals)
        tlv = struct.pack(TLV_HDR_FMT, TARGET_LIST_TLVS[0], len(body)) + body
        total = HEADER_SIZE + len(tlv)
        # Q8I: magic,version,totalLen,platform,frameNum,cpu,numObj,numTLVs,subFrame
        hdr = struct.pack(HEADER_FMT, int.from_bytes(MAGIC, 'little'),
                          1, total, 0, 1, 0, 0, 1, 0)
        return hdr + tlv

    fr = make_frame([(7, 0.3, 0.7, 0.0), (9, -0.4, 1.4, 0.1)])
    p = FrameParser()
    out = p.feed(fr[:20]) + p.feed(fr[20:])   # 분할 공급 테스트
    assert len(out) == 1, out
    trs = out[0]
    assert len(trs) == 2 and trs[0].id == 7 and abs(trs[1].y - 1.4) < 1e-5, trs
    print('OK parser:', trs)
