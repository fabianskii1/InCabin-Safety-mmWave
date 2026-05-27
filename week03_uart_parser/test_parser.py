"""
TLV 파서 단위 테스트 — 실제 보드 없이 로컬에서 파싱 로직 검증
=================================================================
가상의 TLV 패킷 바이트를 직접 조립하여 파싱 결과가 올바른지 확인.

실행 방법:
  python test_parser.py
"""

import struct
import numpy as np
from tlv_parser import (
    MAGIC_WORD, FRAME_HEADER_FORMAT, FRAME_HEADER_SIZE,
    TLV_HEADER_FORMAT, TLV_HEADER_SIZE,
    TLV_TYPE_DETECTED_POINTS, TLV_TYPE_SIDE_INFO,
    parse_frame, extract_frame_from_buffer,
)


# ────────────────────────────────────────────────
# 가상 프레임 패킷 조립 헬퍼
# ────────────────────────────────────────────────
def build_fake_frame(points: list[tuple], side_infos: list[tuple] | None = None) -> bytes:
    """
    테스트용 가짜 TLV 프레임 바이트 생성.

    Parameters
    ----------
    points : list of (x, y, z, v) tuples
        삽입할 포인트 클라우드 데이터
    side_infos : list of (snr, noise) tuples, optional
        포인트별 Side Info (없으면 생략)

    Returns
    -------
    bytes
        완전한 1프레임 바이트
    """
    num_obj = len(points)
    num_tlvs = 1 + (1 if side_infos else 0)

    # ── TLV 1: Detected Points ─────────────────
    point_data = b''
    for (x, y, z, v) in points:
        point_data += struct.pack('<ffff', x, y, z, v)

    tlv1_header = struct.pack(TLV_HEADER_FORMAT,
                              TLV_TYPE_DETECTED_POINTS, len(point_data))
    tlv1 = tlv1_header + point_data

    tlv_payload = tlv1

    # ── TLV 7: Side Info (optional) ────────────
    if side_infos:
        side_data = b''
        for (snr, noise) in side_infos:
            side_data += struct.pack('<HH', snr, noise)
        tlv7_header = struct.pack(TLV_HEADER_FORMAT,
                                  TLV_TYPE_SIDE_INFO, len(side_data))
        tlv_payload += tlv7_header + side_data

    # ── 프레임 헤더 ────────────────────────────
    total_len = FRAME_HEADER_SIZE + len(tlv_payload)
    header = struct.pack(
        FRAME_HEADER_FORMAT,
        MAGIC_WORD,   # magic
        0x03040102,   # version
        total_len,    # totalPacketLen
        0x4900000,    # platform (IWR1843)
        1,            # frameNumber
        1000000,      # timeCpuCycles
        num_obj,      # numDetectedObj
        num_tlvs,     # numTLVs
        0,            # subframeNumber
    )

    return header + tlv_payload


# ────────────────────────────────────────────────
# 테스트 케이스
# ────────────────────────────────────────────────
def test_empty_frame():
    """포인트가 0개인 프레임 파싱"""
    raw = build_fake_frame([])
    result = parse_frame(raw)

    assert result is not None, "파싱 결과가 None이면 안 됨"
    assert result['header']['num_detected_obj'] == 0
    assert len(result['points']) == 0
    print("[PASS] test_empty_frame")


def test_single_point():
    """포인트 1개 정상 파싱"""
    raw = build_fake_frame([(1.5, 2.0, 0.5, -0.3)])
    result = parse_frame(raw)

    assert result is not None
    assert len(result['points']) == 1

    x, y, z, v = result['points'][0]
    assert abs(x - 1.5) < 1e-5, f"x 불일치: {x}"
    assert abs(y - 2.0) < 1e-5, f"y 불일치: {y}"
    assert abs(z - 0.5) < 1e-5, f"z 불일치: {z}"
    assert abs(v - (-0.3)) < 1e-5, f"v 불일치: {v}"
    print("[PASS] test_single_point")


def test_multi_points():
    """포인트 3개 파싱 및 NumPy shape 검증"""
    pts = [(0.5, 1.0, 0.1, 0.2),
           (1.5, 2.0, 0.3, -0.5),
           (2.5, 3.0, 0.0, 1.0)]
    raw = build_fake_frame(pts)
    result = parse_frame(raw)

    assert result is not None
    arr = result['points']
    assert arr.shape == (3, 4), f"shape 불일치: {arr.shape}"
    assert arr.dtype == np.float32

    for i, (ex, ey, ez, ev) in enumerate(pts):
        assert abs(arr[i, 0] - ex) < 1e-5
        assert abs(arr[i, 1] - ey) < 1e-5
    print("[PASS] test_multi_points")


def test_side_info():
    """Side Info TLV 파싱 검증"""
    pts      = [(1.0, 2.0, 0.0, 0.5), (3.0, 4.0, 0.1, -0.2)]
    si       = [(120, 30), (80, 25)]
    raw      = build_fake_frame(pts, side_infos=si)
    result   = parse_frame(raw)

    assert result is not None
    sinfo = result['side_info']
    assert sinfo.shape == (2, 2), f"side_info shape 불일치: {sinfo.shape}"
    assert sinfo[0, 0] == 120  # snr
    assert sinfo[0, 1] == 30   # noise
    assert sinfo[1, 0] == 80
    print("[PASS] test_side_info")


def test_magic_word_detection():
    """버퍼 앞에 쓰레기 데이터가 있어도 Magic Word로 올바르게 프레임 분리"""
    junk = b'\x00\xFF\xAB\xCD' * 10          # 40바이트 쓰레기
    raw  = build_fake_frame([(1.0, 2.0, 0.0, 0.0)])
    raw2 = build_fake_frame([(5.0, 6.0, 0.0, 0.0)])

    buf = bytearray(junk + raw + raw2)

    # 첫 번째 프레임 추출
    frame1_bytes, buf = extract_frame_from_buffer(buf)
    assert frame1_bytes is not None, "첫 번째 프레임 추출 실패"
    r1 = parse_frame(frame1_bytes)
    assert abs(r1['points'][0, 0] - 1.0) < 1e-5, "첫 번째 프레임 x값 불일치"

    # 두 번째 프레임 추출
    frame2_bytes, buf = extract_frame_from_buffer(buf)
    assert frame2_bytes is not None, "두 번째 프레임 추출 실패"
    r2 = parse_frame(frame2_bytes)
    assert abs(r2['points'][0, 0] - 5.0) < 1e-5, "두 번째 프레임 x값 불일치"

    print("[PASS] test_magic_word_detection")


def test_corrupted_frame():
    """손상된 프레임(Magic Word 불일치) 처리 — None 반환 확인"""
    raw = bytearray(build_fake_frame([(1.0, 1.0, 0.0, 0.0)]))
    raw[0] = 0xFF  # Magic Word 첫 바이트 손상
    result = parse_frame(bytes(raw))
    assert result is None, "손상 프레임은 None이어야 함"
    print("[PASS] test_corrupted_frame")


# ────────────────────────────────────────────────
# 실행
# ────────────────────────────────────────────────
if __name__ == '__main__':
    print("=" * 50)
    print("IWR1843 TLV 파서 단위 테스트")
    print("=" * 50)

    tests = [
        test_empty_frame,
        test_single_point,
        test_multi_points,
        test_side_info,
        test_magic_word_detection,
        test_corrupted_frame,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"[ERROR] {t.__name__}: {e}")
            failed += 1

    print("=" * 50)
    print(f"결과: {passed}개 통과 / {failed}개 실패")
    print("=" * 50)
