# gtrack_clearance_demo / pointcloud_parser.py
# ---------------------------------------------------------------------------
# 3D People Tracking UART 프레임에서 '트랙(1010)' + '압축 포인트클라우드(1020)' 를
# 함께 파싱. presence 폴백(정지자 검출)용 — 트래커가 놓친 정지 승객을 포인트클라우드로
# 잡기 위함. 기존 track_parser.py 무수정, 그 상수·변환(to_world)만 import 재사용.
#
# 포맷(비주얼라이저 parseTLVs.parseCompressedSphericalPointCloudTLV 기준):
#   Compressed Spherical Point Cloud TLV = type 1020
#     단위 5f: [elevUnit, azimUnit, dopplerUnit, rangeUnit, snrUnit]
#     점 2bh2H: elevation(int8) azimuth(int8) doppler(int16) range(uint16) snr(uint16)
#   구면→직교(sensor): x=r·sin(az)·cos(el)  y=r·cos(az)·cos(el)  z=r·sin(el)
#   그 후 트랙과 동일한 to_world(FLIP_X·틸트역회전·센서높이)로 월드 좌표 정합.
#
# 하드웨어 없이 검증:  py pointcloud_parser.py
# ---------------------------------------------------------------------------
import struct
import math

from track_parser import (                       # noqa: E402  (무수정 재사용)
    MAGIC, HEADER_FMT, HEADER_SIZE, TLV_HDR_FMT, TLV_HDR_SIZE,
    TARGET_LIST_TLVS, TARGET_FMT, TARGET_SIZE, Track, to_world,
)

# 압축 구면 포인트클라우드 TLV (People Tracking 데모)
POINTCLOUD_TLVS = (1020,)                         # MMWDEMO_OUTPUT_MSG_COMPRESSED_POINTS
PUNIT_FMT = '5f'
PUNIT_SIZE = struct.calcsize(PUNIT_FMT)           # 20
PT_FMT = '2bh2H'
PT_SIZE = struct.calcsize(PT_FMT)                 # 8


class Point:
    """월드 좌표 포인트 (presence 판정용). x=측방, y=전방, z=높이(m)."""
    __slots__ = ('x', 'y', 'z', 'doppler', 'snr')

    def __init__(self, x, y, z, doppler, snr):
        self.x, self.y, self.z = x, y, z
        self.doppler, self.snr = doppler, snr

    def __repr__(self):
        return 'P({:.2f},{:.2f},{:.2f} v={:.2f} snr={:.1f})'.format(
            self.x, self.y, self.z, self.doppler, self.snr)


def _parse_points(payload, tlen):
    pts = []
    if tlen < PUNIT_SIZE:
        return pts
    try:
        eU, aU, dU, rU, sU = struct.unpack(PUNIT_FMT, payload[:PUNIT_SIZE])
    except struct.error:
        return pts
    n = (tlen - PUNIT_SIZE) // PT_SIZE
    off = PUNIT_SIZE
    for _ in range(n):
        try:
            el, az, dop, rng, snr = struct.unpack(PT_FMT, payload[off:off + PT_SIZE])
        except struct.error:
            break
        off += PT_SIZE
        elev = el * eU
        azim = az * aU
        r = rng * rU
        cx = r * math.sin(azim) * math.cos(elev)   # sensor cartesian
        cy = r * math.cos(azim) * math.cos(elev)
        cz = r * math.sin(elev)
        wx, wy, wz = to_world(cx, cy, cz)           # 트랙과 동일 변환
        pts.append(Point(wx, wy, wz, dop * dU, snr * sU))
    return pts


def _parse_tracks(payload, tlen):
    tracks = []
    if tlen < TARGET_SIZE:
        return tracks
    n = tlen // TARGET_SIZE
    for i in range(n):
        t = struct.unpack(TARGET_FMT, payload[i * TARGET_SIZE:(i + 1) * TARGET_SIZE])
        wx, wy, wz = to_world(t[1], t[2], t[3])
        tracks.append(Track(t[0], wx, wy, wz))
    return tracks


class FrameParser2:
    """바이트 스트림 -> 완성 프레임마다 (tracks, points) 반환.
       tlv_seen(set) 에 관측된 TLV 타입 누적(첫 하드웨어 실행 시 1020 유무 확인용)."""

    def __init__(self):
        self.buf = bytearray()
        self.tlv_seen = set()
        self.frame_nums = []     # 직전 feed() 가 반환한 프레임들의 헤더 frameNumber

    def feed(self, data):
        if data:
            self.buf += data
        frames = []
        self.frame_nums = []
        while True:
            mi = self.buf.find(MAGIC)
            if mi < 0:
                if len(self.buf) > len(MAGIC):
                    self.buf = self.buf[-len(MAGIC):]
                break
            if mi > 0:
                self.buf = self.buf[mi:]
            if len(self.buf) < HEADER_SIZE:
                break
            hdr = struct.unpack(HEADER_FMT, self.buf[:HEADER_SIZE])
            totalLen = hdr[2]
            numTLVs = hdr[7]
            if totalLen < HEADER_SIZE or totalLen > 65536:
                self.buf = self.buf[len(MAGIC):]
                continue
            if len(self.buf) < totalLen:
                break
            frame = bytes(self.buf[:totalLen])
            self.buf = self.buf[totalLen:]
            frames.append(self._parse(frame[HEADER_SIZE:], numTLVs))
            self.frame_nums.append(hdr[4])    # HEADER_FMT 'Q8I' 의 frameNumber
        return frames

    def _parse(self, data, numTLVs):
        tracks, points = [], []
        idx = 0
        for _ in range(numTLVs):
            if idx + TLV_HDR_SIZE > len(data):
                break
            ttype, tlen = struct.unpack(TLV_HDR_FMT, data[idx:idx + TLV_HDR_SIZE])
            idx += TLV_HDR_SIZE
            payload = data[idx:idx + tlen]
            self.tlv_seen.add(ttype)
            if ttype in TARGET_LIST_TLVS:
                tracks = _parse_tracks(payload, tlen)
            elif ttype in POINTCLOUD_TLVS:
                points = _parse_points(payload, tlen)
            idx += tlen
        return tracks, points


if __name__ == '__main__':
    # 합성 프레임: 트랙 1 + 포인트 3개 왕복 검증 (하드웨어 불필요)
    def make_frame(tracks, pts):
        body = b''
        # 트랙 TLV
        tbody = b''
        for (tid, x, y, z) in tracks:
            tbody += struct.pack(TARGET_FMT, tid, x, y, z, *([0.0] * 24))
        body += struct.pack(TLV_HDR_FMT, TARGET_LIST_TLVS[0], len(tbody)) + tbody
        # 포인트 TLV (단위=1.0 이면 압축값=실값)
        pbody = struct.pack(PUNIT_FMT, 1.0, 1.0, 1.0, 1.0, 1.0)
        for (el, az, dop, rng, snr) in pts:
            pbody += struct.pack(PT_FMT, el, az, dop, rng, snr)
        body += struct.pack(TLV_HDR_FMT, POINTCLOUD_TLVS[0], len(pbody)) + pbody
        total = HEADER_SIZE + len(body)
        hdr = struct.pack(HEADER_FMT, int.from_bytes(MAGIC, 'little'),
                          1, total, 0, 1, 0, 0, 2, 0)
        return hdr + body

    # elev=0, az=0, range=... -> y축 전방. (단위 1.0)
    fr = make_frame([(7, 0.3, 0.7, 0.0)],
                    [(0, 0, 0, 1, 100), (0, 0, 0, 1, 90), (0, 0, 0, 1, 80)])
    p = FrameParser2()
    out = p.feed(fr[:25]) + p.feed(fr[25:])   # 분할 공급
    assert len(out) == 1, out
    tracks, points = out[0]
    assert len(tracks) == 1 and tracks[0].id == 7, tracks
    assert len(points) == 3, points
    print('OK tracks:', tracks)
    print('OK points:', points)
    print('   TLV seen:', sorted(p.tlv_seen))
