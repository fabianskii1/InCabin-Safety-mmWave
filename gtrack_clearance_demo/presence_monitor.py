# gtrack_clearance_demo / presence_monitor.py
# ---------------------------------------------------------------------------
# presence 폴백 좌석 점유 판정.
#   존 점유 = (존 안에 confirmed 트랙)  OR  (존 안 포인트클라우드 presence)
# 트래커가 정지 승객을 놓쳐도(static death) 포인트-presence 로 점유를 유지해
# fail-safe 를 보강한다. clearance_gtrack.ZoneClearance(비대칭 히스테리시스)를 그대로
# 재사용하고, 그 입력 'occupied' 만 트랙→(트랙 OR presence)로 확장. 기존 파일 무수정.
#
# 튜닝: PRESENCE_MIN_POINTS(존 내 최소 점 수), PRESENCE_MIN_SNR, Z_MIN/Z_MAX(사람 높이대).
# 하드웨어 없이 검증:  py presence_monitor.py
# ---------------------------------------------------------------------------
import math
from collections import deque

import clearance_gtrack as cg

# ---- presence 튜닝 ----
PRESENCE_MIN_POINTS = 3      # 존 내 이 수 이상 포인트면 presence (단발 클러터 억제)
# presence 디바운스. 1 = 디바운스 없음(단발 1프레임도 즉시 점유 = 기존 동작).
#   ZoneClearance 는 occupied 가 한 프레임만 True 여도 holdSec 로 즉시 OCCUPIED 래치하고
#   (occTrig = occSec>=OCC_CONFIRM_SEC OR holdSec>0 -> 뒤 항이 항상 먼저 성립),
#   그 뒤 1.5s hold + 4.0s EMPTY_CONFIRM 이 붙어 55ms 한 프레임이 5.5초 점유로 증폭된다.
#   트랙은 GTRACK 내부 다중프레임 확인(점 21개+ / det2actThre)을 이미 거치므로 즉시 래치로 두고,
#   확인 절차가 없는 presence 에만 슬라이딩 윈도우 디바운스를 걸어 단발 스프레이를 거른다.
#   ±1 누설 적분기는 쓰지 않는다: 50% 듀티로 간헐 검출되는 정지 승객이 임계에 도달하지
#   못해 미검출(위험 방향)이 되기 때문. 대신 '최근 M 프레임 중 K회 이상' 규칙을 쓴다.
PRESENCE_CONFIRM_FRAMES = 1  # K. >=2 로 올리면 presence 단발 억제
PRESENCE_WINDOW_MULT = 4     # M = K * 이 값 (K=3 -> 최근 12프레임 중 3회)
PRESENCE_MIN_SNR = 0.0       # SNR 하한(0=무시). 실측 후 조정.
Z_MIN, Z_MAX = 0.1, 1.9      # 사람 점유 높이대(월드 z, m). 바닥/천장 클러터 배제.


# 폴딩 대상 좌석 접두어. 조수석은 뒷좌 폴딩으로 끼일 수 없으므로 fold 판정에서 제외
# (표시는 유지). eval_score.py 가 'Rear' 접두어로 위험/오차단을 집계하는 것과 일치시킴.
FOLD_ZONE_PREFIX = 'Rear'

# ---- 얼어붙은 트랙 필터 (2026-09-18) ----
# 좌표가 freeze_sec 이상 바뀌지 않은 트랙은 트랙 근거에서 뺀다(점 근거는 그대로).
# 퇴장 후 좌석에 남은 유령 트랙은 가끔 점이 연관돼 GTRACK 이 지우지 않는다(실측 29~42초 생존).
# 비교는 프레임 로그와 같은 mm 반올림 좌표로 한다: 반올림 전 값으로 비교하면 1 mm 미만 갱신도
# '변경'이 되어 유령을 놓치고, 오프라인 분석(analyze_frames)과 결과가 달라진다.
FREEZE_ROUND_DIGITS = 3
# 깨진 트랙: UART 손상 프레임에서 파서가 만든 값(ID 15억, 좌표 1e31 등). 필터 계산에서 제외.
MAX_TRACK_ID = 255
MAX_TRACK_COORD = 100.0


def valid_track_values(tid, x, y, z):
    return 0 <= tid <= MAX_TRACK_ID and all(
        math.isfinite(v) and abs(v) < MAX_TRACK_COORD for v in (x, y, z))


def _point_in_zone(zone, p):
    return (zone.xmin <= p.x <= zone.xmax and zone.ymin <= p.y <= zone.ymax and
            Z_MIN <= p.z <= Z_MAX and p.snr >= PRESENCE_MIN_SNR)


def zone_point_stats(zone, points):
    """존 안 유효 포인트의 (개수, SNR 중앙값, SNR 최대). 없으면 (0, 0, 0).

    다중경로 유령 점은 직접 반사보다 SNR 이 낮은 경향이 있어, SNR 게이트를
    쓸지 판단하려면 분포를 먼저 봐야 한다. PRESENCE_MIN_SNR 튜닝 근거용.
    """
    snrs = []
    for p in points:
        if _point_in_zone(zone, p):
            snrs.append(p.snr)
    if not snrs:
        return 0, 0.0, 0.0
    snrs.sort()
    return len(snrs), snrs[len(snrs) // 2], snrs[-1]


def zone_zero_doppler_points(zone, points):
    """존 안 유효 포인트 중 속도가 정확히 0 인 점 수 (zone_point_stats 와 같은 조건).

    펌웨어는 fineMotion 프레임에서 저속 점의 도플러 인덱스를 0 으로 강제한다
    (radarProcess.c:580-586). 압축 단위가 0.00028 m/s(mss_main.c:1742)라 0 이 아닌
    도플러 칸의 점은 0 으로 반올림되지 않는다. 일반 프레임에서도 속도 0 점이 나오는지는
    이 값을 프레임 번호와 함께 기록해 실측으로 확인한다.
    """
    return sum(1 for p in points if p.doppler == 0 and _point_in_zone(zone, p))


def points_in_zone(zone, points):
    """존(x,y 사각) 안 + 높이대 + SNR 조건을 만족하는 포인트 수."""
    return zone_point_stats(zone, points)[0]


class PresenceClearanceMonitor:
    """전 존 관리: 트랙 OR presence 로 점유 -> ZoneClearance 히스테리시스 -> fold 판정."""

    def __init__(self, seats=None, min_points=PRESENCE_MIN_POINTS,
                 presence_confirm=PRESENCE_CONFIRM_FRAMES, adaptive=False,
                 weak_release=None, freeze_sec=None):
        """freeze_sec: 얼어붙은 트랙 필터(초). None = 끔(기존 동작)."""
        seats = seats or cg.DEFAULT_SEATS
        self.freeze_sec = None if freeze_sec is None else float(freeze_sec)
        self._last_pos = {}      # tid -> (mm 반올림 좌표, 그 좌표가 처음 나온 시각). 사라져도 지우지 않음
        self._trk_cache = None   # 직전 새 프레임의 존별 트랙 근거 (재호출 때 그대로 사용)
        self.adaptive = adaptive
        if adaptive:
            from adaptive_clearance import AdaptiveZoneClearance
            self.zones = [AdaptiveZoneClearance(z, weak_release=weak_release) for z in seats]
        else:
            self.zones = [cg.ZoneClearance(z) for z in seats]
        self.min_points = min_points
        self.presence_confirm = max(1, int(presence_confirm))
        self.presence_window = self.presence_confirm * PRESENCE_WINDOW_MULT
        # 존별 최근 프레임의 presence 여부 큐 (최근 M 중 K회 이상이면 인정)
        self._pres_hist = {zc.zone.name: deque(maxlen=self.presence_window)
                           for zc in self.zones}
        self._prev = {z.zone.name: cg.STATE_UNKNOWN for z in self.zones}
        self._startT = None
        self.transitions = []

    def update(self, tracks, points, now, new_frame=True):
        """new_frame=False: 새 레이더 프레임 없이 직전 근거로 시간만 진행시키는 재호출.
        이때 presence 창(최근 M 프레임)에는 추가하지 않는다. 추가하면 한 프레임이 루프
        회전 수만큼 반복 저장돼 디바운스가 무력화된다(2026-09-17 발견한 버그)."""
        if self._startT is None:
            self._startT = now
        # 트랙 근거는 새 프레임에서만 계산한다. 재호출에서 다시 계산하면 얼어붙은 시간이
        # 프레임 사이에 F 를 넘어 근거가 바뀔 수 있는데, 오프라인 재생은 프레임 단위로만 본다.
        if new_frame or self._trk_cache is None:
            self._trk_cache = self._track_evidence(tracks, now)
        results = []
        for zc in self.zones:
            track_in_raw, tid, track_in, n_frozen = self._trk_cache[zc.zone.name]
            npts, snr_med, snr_max = zone_point_stats(zc.zone, points)
            pres_raw = npts >= self.min_points
            # 최근 M 프레임 중 K회 이상이면 presence 인정 (간헐 검출 허용, 단발 배제)
            hist = self._pres_hist[zc.zone.name]
            if new_frame:
                hist.append(pres_raw)
            score = sum(hist)
            pres_in = score >= self.presence_confirm
            occupied = track_in or pres_in

            if self.adaptive:
                r = zc.update(occupied, tid, now, track_in)
            else:
                r = zc.update(occupied, tid, now)
            # 점유 근거 라벨(분석·시연용).
            # 주의: OCCUPIED 는 히스테리시스(OCC_HOLD/EMPTY_CONFIRM) 때문에 이번 프레임에
            # 트랙도 포인트도 없어도 유지된다. 그 구간을 'presence' 로 찍으면 폴백이
            # 실제로 기여한 것처럼 보이는 계측 오류가 되므로 'hold' 로 분리한다.
            if r['state'] == cg.STATE_OCCUPIED:
                if track_in and pres_in:
                    src = 'both'
                elif track_in:
                    src = 'track'
                elif pres_in:
                    src = 'presence'
                else:
                    src = 'hold'      # 이번 프레임 근거 없음 = 히스테리시스 잔류
            else:
                src = 'none'
            r['source'] = src
            r['npts'] = npts
            r['npts0'] = zone_zero_doppler_points(zc.zone, points)
            r['snr_med'] = snr_med
            r['snr_max'] = snr_max
            r['pres_score'] = score
            r['track_in'] = track_in            # 판정에 쓴 트랙 근거 (필터 적용 후)
            r['track_in_raw'] = track_in_raw    # 존 안 트랙 유무 (필터 적용 전)
            r['frozen_excluded'] = n_frozen     # 얼어붙어 근거에서 뺀 존 안 트랙 수
            r['tid'] = tid
            r['pres_raw'] = pres_raw
            results.append(r)

            if r['state'] != self._prev[r['zone']]:
                self.transitions.append((now, r['zone'], self._prev[r['zone']], r['state'], src))
                print('[presence] t={:.1f}s {} {} -> {} ({}{})'.format(
                    now, r['zone'], self._prev[r['zone']], r['state'], src,
                    ' n={}'.format(npts) if pres_in else ''))
                self._prev[r['zone']] = r['state']
        return results

    def _track_evidence(self, tracks, now):
        """존별 (필터 전 트랙 유무, 판정용 tid, 필터 후 트랙 근거, 얼어붙어 뺀 트랙 수).
        얼어붙은 시간 = mm 반올림 좌표가 마지막으로 바뀐 뒤 지난 시간. 깨진 트랙은 계산에서 제외.
        analyze_frames.frozen_tracks / track_evidence 와 같은 규칙이어야 오프라인 재생이 일치한다."""
        dur = {}
        for t in tracks:
            if not valid_track_values(t.id, t.x, t.y, t.z):
                continue
            pos = (round(t.x, FREEZE_ROUND_DIGITS), round(t.y, FREEZE_ROUND_DIGITS),
                   round(t.z, FREEZE_ROUND_DIGITS))
            last = self._last_pos.get(t.id)
            if last is None or last[0] != pos:
                self._last_pos[t.id] = (pos, now)
            dur[t.id] = now - self._last_pos[t.id][1]
        ev = {}
        for zc in self.zones:
            inz = [t for t in tracks if t.id in dur and zc.zone.contains(t)]
            if self.freeze_sec is None:
                keep = inz
            else:
                keep = [t for t in inz if dur[t.id] < self.freeze_sec]
            ev[zc.zone.name] = (bool(inz), keep[0].id if keep else None, bool(keep),
                                len(inz) - len(keep))
        return ev

    def fold_permitted(self, now):
        """뒷좌 폴딩 허용 = 폴딩 대상(Rear*) 존이 모두 EMPTY 확정 + 워밍업 경과.

        조수석은 뒷좌 폴딩으로 끼일 수 없으므로 판정에서 제외한다(표시는 유지).
        eval_score.py 의 위험/오차단 집계가 'Rear' 접두어 기준인 것과 일치.
        """
        if self._startT is None or (now - self._startT) < cg.WARMUP_BLOCK_SEC:
            return False
        fold_zones = [zc for zc in self.zones
                      if zc.zone.name.startswith(FOLD_ZONE_PREFIX)]
        if not fold_zones:                      # 접두어가 안 맞으면 보수적으로 전체
            fold_zones = self.zones
        return all(zc.state == cg.STATE_EMPTY for zc in fold_zones)


# ===========================================================================
def _selftest():
    from track_parser import Track
    from pointcloud_parser import Point
    seats = cg.SEAT_SETS['vehicle']
    fps = 10.0

    def center(name):
        z = [q for q in seats if q.name == name][0]
        return (z.xmin + z.xmax) / 2, (z.ymin + z.ymax) / 2

    def zc_of(mon, name):
        return [z for z in mon.zones if z.zone.name == name][0]

    def run(mon, sec, tracks, points, t0):
        t = t0
        for k in range(int(sec * fps)):
            t = t0 + k / fps
            mon.update(tracks, points, t)
        return t

    # ---- A. 조수석은 폴딩 판정에서 제외된다(표시만) ----
    px, py = center('Passenger')
    mon = PresenceClearanceMonitor(seats)
    t = run(mon, cg.WARMUP_BLOCK_SEC + 1.0, [Track(1, px, py, 0.9)], [], 0.0)
    assert zc_of(mon, 'Passenger').state == cg.STATE_OCCUPIED
    assert mon.fold_permitted(t), '조수석 점유가 뒷좌 폴딩을 막으면 안 됨'
    print('OK: 조수석 OCCUPIED 표시되지만 폴딩은 허용(끼임 대상 아님)')

    # ---- B. 뒷좌 트랙은 폴딩을 차단한다 ----
    rx, ry = center('Rear-C')
    mon = PresenceClearanceMonitor(seats)
    t = run(mon, cg.WARMUP_BLOCK_SEC + 1.0, [Track(2, rx, ry, 0.9)], [], 0.0)
    rzc = zc_of(mon, 'Rear-C')
    assert rzc.state == cg.STATE_OCCUPIED, rzc.state
    assert not mon.fold_permitted(t), '뒷좌 점유 중엔 차단돼야 함'
    print('OK: 뒷좌 트랙 점유 -> 폴딩 차단')

    # ---- C. static death: 트랙 소실해도 presence 로 뒷좌 점유 유지 ----
    pts = [Point(rx, ry, 0.8, 0.0, 100) for _ in range(4)]
    t = run(mon, cg.EMPTY_CONFIRM_SEC + 3.0, [], pts, t)
    assert rzc.state == cg.STATE_OCCUPIED, ('presence 유지 실패', rzc.state)
    assert not mon.fold_permitted(t)
    print('OK: 트랙 소실에도 presence 로 뒷좌 OCCUPIED 유지 (static death 보완)')

    # ---- D. 실제 하차: 트랙도 포인트도 없으면 EMPTY 확정 -> 허용 ----
    t = run(mon, cg.EMPTY_CONFIRM_SEC + 1.0, [], [], t)
    assert rzc.state == cg.STATE_EMPTY, rzc.state
    assert mon.fold_permitted(t)
    print('OK: 트랙·포인트 모두 없으면 EMPTY 확정 -> 폴딩 허용')

    # ---- E. min_points 미만 클러터는 presence 로 안 뜬다 ----
    mon2 = PresenceClearanceMonitor(seats)
    run(mon2, cg.WARMUP_BLOCK_SEC + cg.EMPTY_CONFIRM_SEC + 2.0, [],
        [Point(rx, ry, 0.8, 0.0, 50), Point(rx, ry, 0.8, 0.0, 50)], 0.0)
    assert zc_of(mon2, 'Rear-C').state == cg.STATE_EMPTY, '2점 클러터 오검출'
    print('OK: min_points 미만 클러터는 presence 무시')

    # ---- F. SNR 통계가 결과에 실린다(튜닝 근거용) ----
    mon3 = PresenceClearanceMonitor(seats)
    res = mon3.update([], [Point(rx, ry, 0.8, 0.0, v) for v in (10.0, 30.0, 90.0)], 0.0)
    r = [q for q in res if q['zone'] == 'Rear-C'][0]
    assert r['npts'] == 3 and r['snr_med'] == 30.0 and r['snr_max'] == 90.0, r
    print('OK: 존별 SNR 중앙값/최대 보고 (npts=3 med=30 max=90)')

    # ---- F2. 존 안 속도 0 점 수가 결과에 실린다(fineMotion 점 직접 확인용) ----
    mixed = [Point(rx, ry, 0.8, 0.0, 20) for _ in range(2)] + \
            [Point(rx, ry, 0.8, v, 20) for v in (0.3, -0.19, 0.00028)] + \
            [Point(px, py, 0.8, 0.0, 20)]                     # 다른 존의 속도 0 점
    res = mon3.update([], mixed, 0.1)
    r = [q for q in res if q['zone'] == 'Rear-C'][0]
    assert r['npts'] == 5 and r['npts0'] == 2, r
    print('OK: 존 안 속도 0 점 수 보고 (npts=5 중 npts0=2, 다른 존 점 제외)')

    # ---- G. presence 디바운스: 단발 스프레이는 걸러지고 지속 검출은 통과 ----
    blip = [Point(rx, ry, 0.8, 0.0, 20) for _ in range(5)]      # n=5 >= min_points

    monA = PresenceClearanceMonitor(seats, presence_confirm=1)   # 기존 동작
    run(monA, cg.WARMUP_BLOCK_SEC + 1.0, [], [], 0.0)
    monA.update([], blip, 10.0)                                  # 단 한 프레임
    assert zc_of(monA, 'Rear-C').state == cg.STATE_OCCUPIED, '기존 동작은 단발도 래치'
    print('OK: confirm=1 이면 단발 1프레임이 즉시 OCCUPIED (기존 증폭 재현)')

    monB = PresenceClearanceMonitor(seats, presence_confirm=3)
    run(monB, cg.WARMUP_BLOCK_SEC + 1.0, [], [], 0.0)
    monB.update([], blip, 10.0)
    assert zc_of(monB, 'Rear-C').state == cg.STATE_EMPTY, '단발이 걸러져야 함'
    print('OK: confirm=3 이면 단발 스프레이 무시')

    for k in range(3):                                           # 3프레임 지속되면
        monB.update([], blip, 10.1 + k / fps)
    assert zc_of(monB, 'Rear-C').state == cg.STATE_OCCUPIED, '지속 검출은 통과해야 함'
    print('OK: confirm=3 이어도 3프레임(약 0.2s) 지속되면 OCCUPIED')

    # 간헐 검출(있음/없음 반복)도 누적되어 통과 = 정지 승객 보호
    monC = PresenceClearanceMonitor(seats, presence_confirm=3)
    run(monC, cg.WARMUP_BLOCK_SEC + 1.0, [], [], 0.0)
    t3 = 10.0
    for k in range(12):
        monC.update([], blip if k % 2 == 0 else [], t3 + k / fps)
    assert zc_of(monC, 'Rear-C').state == cg.STATE_OCCUPIED, '간헐 검출도 누적돼야 함'
    print('OK: 간헐 검출(50% 듀티)도 누적되어 OCCUPIED (정지 승객 보호)')

    # ---- H. 재호출(new_frame=False)은 presence 창에 쌓이지 않는다 ----
    monH = PresenceClearanceMonitor(seats, presence_confirm=3)
    run(monH, cg.WARMUP_BLOCK_SEC + 1.0, [], [], 0.0)
    tH = 10.0
    monH.update([], blip, tH)                         # 실제 프레임 1개
    for k in range(20):                               # 새 프레임 없이 20회 재호출
        tH += 0.003
        monH.update([], blip, tH, new_frame=False)
    assert zc_of(monH, 'Rear-C').state == cg.STATE_EMPTY, '재호출이 창을 채우면 안 됨'
    print('OK: 프레임 1개 + 재호출 20회는 여전히 단발로 취급 (디바운스 버그 수정)')

    # ---- I. 얼어붙은 트랙 필터 ----
    #   0~6s 좌표가 계속 바뀌는 트랙, 6s 부터 같은 좌표로 멈춤(점 없음), 9.0s 에 깨진 프레임 1개
    F = 2.0

    def frame_tracks(t):
        if t < 6.0 - 1e-9:
            k = int(round(t * fps))
            return [Track(4, rx + 0.001 * (k % 7), ry, 0.9)]
        if abs(t - 9.0) < 1e-6:
            return [Track(1589318287, 0.0, -1.57e31, 2.0)]          # UART 손상 프레임
        return [Track(4, rx + 0.0305, ry, 0.9)]                     # 멈춘 좌표

    monI = PresenceClearanceMonitor(seats, freeze_sec=F)
    monN = PresenceClearanceMonitor(seats)                          # 필터 끔
    rI = {}
    for k in range(int(20 * fps)):
        t = k / fps
        trs = frame_tracks(t)
        res = monI.update(trs, [], t)
        monN.update(trs, [], t)
        rI[round(t, 2)] = [q for q in res if q['zone'] == 'Rear-C'][0]
        if abs(t - 7.9) < 1e-6:                                     # 7.9s 프레임 뒤 재호출 2번(8.02, 8.04s)
            for tt in (8.02, 8.04):
                rr = [q for q in monI.update(trs, [], tt, new_frame=False) if q['zone'] == 'Rear-C'][0]
                assert rr['track_in'] and rr['frozen_excluded'] == 0, rr
    assert rI[7.9]['track_in'] and not rI[8.0]['track_in'] and rI[8.0]['frozen_excluded'] == 1, (rI[7.9], rI[8.0])
    print('OK: 좌표가 F=2s 이상 멈춘 트랙은 근거에서 제외 (7.9s 인정 -> 8.0s 제외), 재호출은 직전 프레임 판정 유지')
    assert not rI[9.1]['track_in'] and rI[9.1]['frozen_excluded'] == 1, rI[9.1]
    print('OK: 깨진 프레임 뒤 같은 좌표로 돌아온 트랙은 갱신이 아님 (멈춘 시간 유지)')
    assert rI[11.9]['state'] == cg.STATE_OCCUPIED and rI[12.1]['state'] == cg.STATE_EMPTY, (
        rI[11.9]['state'], rI[12.1]['state'])
    assert zc_of(monN, 'Rear-C').state == cg.STATE_OCCUPIED
    print('OK: 점 없는 멈춘 트랙 -> 마지막 좌표 변경 + F + EMPTY_CONFIRM({}s) 에 EMPTY, 필터 끄면 계속 OCCUPIED'.format(
        cg.EMPTY_CONFIRM_SEC))

    print('')
    print('all presence selftests passed')


if __name__ == '__main__':
    _selftest()
