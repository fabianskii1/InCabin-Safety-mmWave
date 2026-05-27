"""
IWR1843 UART TLV 패킷 파서 (3주차)
====================================
TI mmWave SDK Out-of-Box 데모 기준 TLV 패킷 구조 파싱
- UART Data Port에서 수신한 바이너리 스트림을 프레임 단위로 분리
- 프레임 헤더 파싱 → TLV 타입별 데이터 추출 → NumPy 배열 반환

TLV Type 정의:
  Type 1 : 검출된 포인트 클라우드 (x, y, z, velocity)
  Type 2 : Range Profile
  Type 3 : Noise Floor Profile
  Type 5 : Range-Doppler Heatmap
  Type 6 : Stats (처리 시간 등)
  Type 7 : 포인트 Side Info (SNR, Noise)
"""

import struct
import numpy as np

# ────────────────────────────────────────────────
# 상수 정의
# ────────────────────────────────────────────────
MAGIC_WORD = b'\x02\x01\x04\x03\x06\x05\x08\x07'  # 프레임 시작 식별자
MAGIC_WORD_LEN = 8

# 프레임 헤더 포맷 (총 40 bytes)
# magic(8) + version(4) + totalPacketLen(4) + platform(4)
# + frameNumber(4) + timeCpuCycles(4) + numDetectedObj(4)
# + numTLVs(4) + subframeNumber(4)
FRAME_HEADER_FORMAT = '<8sIIIIIIII'  # little-endian
FRAME_HEADER_SIZE = struct.calcsize(FRAME_HEADER_FORMAT)  # 40 bytes

# TLV 헤더 포맷: type(4) + length(4) = 8 bytes
TLV_HEADER_FORMAT = '<II'
TLV_HEADER_SIZE = struct.calcsize(TLV_HEADER_FORMAT)  # 8 bytes

# TLV 타입 상수
TLV_TYPE_DETECTED_POINTS    = 1  # 포인트 클라우드 (x,y,z,v)
TLV_TYPE_RANGE_PROFILE      = 2  # Range Profile
TLV_TYPE_NOISE_PROFILE      = 3  # Noise Floor Profile
TLV_TYPE_RANGE_DOPPLER_MAP  = 5  # Range-Doppler Heatmap
TLV_TYPE_STATS              = 6  # 처리 통계
TLV_TYPE_SIDE_INFO          = 7  # SNR / Noise (포인트별)

# 포인트 1개당 데이터 크기: x(4) + y(4) + z(4) + velocity(4) = 16 bytes
DETECTED_POINT_SIZE = 16


# ────────────────────────────────────────────────
# 프레임 헤더 파싱
# ────────────────────────────────────────────────
def parse_frame_header(data: bytes) -> dict | None:
    """
    40바이트 프레임 헤더를 파싱하여 딕셔너리로 반환.
    magic word 불일치 시 None 반환.

    Parameters
    ----------
    data : bytes
        헤더 40바이트 원시 데이터

    Returns
    -------
    dict | None
        파싱된 헤더 필드 딕셔너리 또는 None
    """
    if len(data) < FRAME_HEADER_SIZE:
        return None

    try:
        fields = struct.unpack(FRAME_HEADER_FORMAT, data[:FRAME_HEADER_SIZE])
    except struct.error as e:
        print(f"[WARN] 헤더 언팩 실패: {e}")
        return None

    magic, version, total_packet_len, platform, frame_number, \
        time_cpu_cycles, num_detected_obj, num_tlvs, subframe_number = fields

    # Magic Word 검증
    if magic != MAGIC_WORD:
        print(f"[WARN] Magic Word 불일치: {magic.hex()}")
        return None

    return {
        'version'          : version,
        'total_packet_len' : total_packet_len,
        'platform'         : platform,
        'frame_number'     : frame_number,
        'time_cpu_cycles'  : time_cpu_cycles,
        'num_detected_obj' : num_detected_obj,
        'num_tlvs'         : num_tlvs,
        'subframe_number'  : subframe_number,
    }


# ────────────────────────────────────────────────
# TLV 개별 파싱 함수
# ────────────────────────────────────────────────
def parse_tlv_detected_points(data: bytes, num_obj: int) -> np.ndarray:
    """
    Type 1: 검출된 포인트 클라우드 파싱
    각 포인트 = [x, y, z, velocity] (float32 × 4 = 16 bytes)

    Returns
    -------
    np.ndarray, shape (N, 4), dtype float32
        columns: [x(m), y(m), z(m), velocity(m/s)]
    """
    if num_obj == 0 or len(data) < num_obj * DETECTED_POINT_SIZE:
        return np.zeros((0, 4), dtype=np.float32)

    points = []
    for i in range(num_obj):
        offset = i * DETECTED_POINT_SIZE
        chunk = data[offset: offset + DETECTED_POINT_SIZE]
        if len(chunk) < DETECTED_POINT_SIZE:
            break
        x, y, z, v = struct.unpack('<ffff', chunk)
        points.append([x, y, z, v])

    return np.array(points, dtype=np.float32)


def parse_tlv_side_info(data: bytes, num_obj: int) -> np.ndarray:
    """
    Type 7: 포인트별 Side Info (SNR, Noise)
    각 포인트 = [snr, noise] (uint16 × 2 = 4 bytes)

    Returns
    -------
    np.ndarray, shape (N, 2), dtype uint16
        columns: [snr, noise]
    """
    SIDE_INFO_SIZE = 4  # uint16 × 2
    if num_obj == 0 or len(data) < num_obj * SIDE_INFO_SIZE:
        return np.zeros((0, 2), dtype=np.uint16)

    side_info = []
    for i in range(num_obj):
        offset = i * SIDE_INFO_SIZE
        chunk = data[offset: offset + SIDE_INFO_SIZE]
        if len(chunk) < SIDE_INFO_SIZE:
            break
        snr, noise = struct.unpack('<HH', chunk)
        side_info.append([snr, noise])

    return np.array(side_info, dtype=np.uint16)


def parse_tlv_range_profile(data: bytes) -> np.ndarray:
    """
    Type 2: Range Profile
    각 bin = uint16 진폭값

    Returns
    -------
    np.ndarray, shape (num_range_bins,), dtype uint16
    """
    num_bins = len(data) // 2
    if num_bins == 0:
        return np.array([], dtype=np.uint16)
    return np.frombuffer(data[:num_bins * 2], dtype=np.uint16)


def parse_tlv_range_doppler_map(data: bytes) -> np.ndarray:
    """
    Type 5: Range-Doppler Heatmap
    uint16 배열 → 2D reshape은 chirp 설정(num_range_bins, num_doppler_bins) 필요

    Returns
    -------
    np.ndarray, shape (num_cells,), dtype uint16  (1D; 외부에서 reshape)
    """
    num_cells = len(data) // 2
    if num_cells == 0:
        return np.array([], dtype=np.uint16)
    return np.frombuffer(data[:num_cells * 2], dtype=np.uint16)


def parse_tlv_stats(data: bytes) -> dict:
    """
    Type 6: 처리 통계 (CPU 사이클 정보 등)
    6개 uint32 필드 = 24 bytes

    Returns
    -------
    dict
    """
    STATS_FORMAT = '<IIIIII'
    STATS_SIZE = struct.calcsize(STATS_FORMAT)
    if len(data) < STATS_SIZE:
        return {}

    fields = struct.unpack(STATS_FORMAT, data[:STATS_SIZE])
    keys = [
        'interframe_processing_time',
        'transmit_output_time',
        'interframe_processing_margin',
        'interchirp_processing_margin',
        'active_frame_cpu_load',
        'interframe_cpu_load',
    ]
    return dict(zip(keys, fields))


# ────────────────────────────────────────────────
# 단일 프레임 파싱 (메인 진입점)
# ────────────────────────────────────────────────
def parse_frame(raw_frame: bytes) -> dict | None:
    """
    Magic Word부터 시작하는 원시 바이트 1프레임을 받아 모든 TLV를 파싱.

    Parameters
    ----------
    raw_frame : bytes
        Magic Word를 포함한 1개 프레임의 전체 바이트

    Returns
    -------
    dict | None
        {
            'header'      : dict,            # 프레임 헤더
            'points'      : np.ndarray,      # (N,4) [x,y,z,v]
            'side_info'   : np.ndarray,      # (N,2) [snr,noise]
            'range_prof'  : np.ndarray,      # range profile
            'rd_map'      : np.ndarray,      # range-doppler map (1D)
            'stats'       : dict,            # 처리 통계
        }
    """
    # 1) 헤더 파싱
    header = parse_frame_header(raw_frame)
    if header is None:
        return None

    result = {
        'header'    : header,
        'points'    : np.zeros((0, 4), dtype=np.float32),
        'side_info' : np.zeros((0, 2), dtype=np.uint16),
        'range_prof': np.array([], dtype=np.uint16),
        'rd_map'    : np.array([], dtype=np.uint16),
        'stats'     : {},
    }

    # 2) TLV 순회
    offset = FRAME_HEADER_SIZE
    num_detected_obj = header['num_detected_obj']

    for _ in range(header['num_tlvs']):
        # TLV 헤더 (type + length)
        if offset + TLV_HEADER_SIZE > len(raw_frame):
            print(f"[WARN] TLV 헤더 읽기 오버플로우 (offset={offset})")
            break

        tlv_type, tlv_length = struct.unpack(
            TLV_HEADER_FORMAT,
            raw_frame[offset: offset + TLV_HEADER_SIZE]
        )
        offset += TLV_HEADER_SIZE

        # TLV 데이터
        tlv_data = raw_frame[offset: offset + tlv_length]
        offset += tlv_length

        # 타입별 분기
        if tlv_type == TLV_TYPE_DETECTED_POINTS:
            result['points'] = parse_tlv_detected_points(tlv_data, num_detected_obj)

        elif tlv_type == TLV_TYPE_RANGE_PROFILE:
            result['range_prof'] = parse_tlv_range_profile(tlv_data)

        elif tlv_type == TLV_TYPE_RANGE_DOPPLER_MAP:
            result['rd_map'] = parse_tlv_range_doppler_map(tlv_data)

        elif tlv_type == TLV_TYPE_STATS:
            result['stats'] = parse_tlv_stats(tlv_data)

        elif tlv_type == TLV_TYPE_SIDE_INFO:
            result['side_info'] = parse_tlv_side_info(tlv_data, num_detected_obj)

        else:
            # 미지원 TLV 타입 — 스킵
            pass

    return result


# ────────────────────────────────────────────────
# 바이트 스트림에서 Magic Word 기준으로 프레임 분리
# ────────────────────────────────────────────────
def extract_frame_from_buffer(buffer: bytearray) -> tuple[bytes | None, bytearray]:
    """
    수신 버퍼에서 Magic Word를 찾아 완전한 1프레임을 추출.

    Parameters
    ----------
    buffer : bytearray
        UART에서 누적된 수신 버퍼

    Returns
    -------
    (frame_bytes | None, remaining_buffer)
        frame_bytes : 완전한 프레임 바이트 (없으면 None)
        remaining_buffer : 프레임 추출 후 남은 버퍼
    """
    # Magic Word 위치 탐색
    start_idx = buffer.find(MAGIC_WORD)
    if start_idx == -1:
        # Magic Word 없음 — 앞 쓰레기 데이터 제거 (단, 마지막 7바이트는 보존)
        leftover = buffer[-(MAGIC_WORD_LEN - 1):] if len(buffer) >= MAGIC_WORD_LEN else buffer
        return None, bytearray(leftover)

    # Magic Word 앞 쓰레기 제거
    buffer = buffer[start_idx:]

    # 헤더를 읽어서 total_packet_len 획득
    if len(buffer) < FRAME_HEADER_SIZE:
        return None, buffer  # 헤더 아직 미수신

    header = parse_frame_header(bytes(buffer))
    if header is None:
        # Magic Word는 찾았지만 헤더 파싱 실패 → 다음 Magic Word 탐색
        buffer = buffer[MAGIC_WORD_LEN:]
        return None, buffer

    total_len = header['total_packet_len']

    # 프레임 전체 수신 확인
    if len(buffer) < total_len:
        return None, buffer  # 아직 미수신

    frame_bytes = bytes(buffer[:total_len])
    remaining   = bytearray(buffer[total_len:])
    return frame_bytes, remaining
