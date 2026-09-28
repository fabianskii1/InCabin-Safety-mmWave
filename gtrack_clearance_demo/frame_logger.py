# gtrack_clearance_demo / frame_logger.py
# ---------------------------------------------------------------------------
# presence clearance 프레임 단위 로거 (CSV).
#
# 목적: 해제 시간·디바운스 같은 파라미터를 '재측정 없이' 오프라인에서 비교하기 위함.
#   1Hz 콘솔 출력으로는 1초 미만의 근거 끊김을 볼 수 없어 WEAK 해제 시간을 근거 있게
#   정할 수 없었다. 이 로그는 레이더 프레임마다 존별 원시 근거(트랙 유무, 존 내 점 수,
#   SNR)를 남기고, analyze_frames.py 가 이를 상태머신에 재생한다.
#
# 재생 정확도를 위해 '재호출'도 기록한다:
#   main 루프는 새 프레임이 없는 회전에도 직전 근거로 monitor.update() 를 다시 부른다.
#   상태머신은 경과 시간으로 누적하므로, 두 프레임 사이 재호출의 첫/마지막 시각만 알면
#   (근거가 그 사이 일정하므로) 라이브 결과를 그대로 재현할 수 있다.
#
# 파일 형식: '# key=json값' 메타 줄들 + CSV 헤더 + 프레임당 1행.
#
# 속도 0 점 수 (2026-09-18 추가, 이전 로그에는 없음):
#   n_points0 = 프레임 전체 점 중 속도가 정확히 0 인 점 수, <존>_n0 = 존 안 유효 점 중 속도 0 점 수.
#   펌웨어는 fineMotion 프레임에서 저속 점의 속도를 0 으로 강제하므로, 프레임 번호(fn) 패턴에
#   기대지 않고 점 단위로 fineMotion 점을 구분하기 위함.
#
# 얼어붙은 트랙 필터 열 (2026-09-18 추가):
#   <존>_trk  = 존 안 트랙 유무 (필터 적용 전, 이전 로그와 같은 의미)
#   <존>_trkf = 판정에 쓴 트랙 근거 (필터 적용 후). 필터를 끈 실행에서는 _trk 와 같다.
#   메타 freeze_sec / empty_confirm_sec 로 실행 설정을 남겨 오프라인 재생이 라이브와 일치하는지 확인한다.
# ---------------------------------------------------------------------------
import csv
import io
import json

STATE_CODE = {'EMPTY': 'E', 'OCCUPIED': 'O', 'UNKNOWN': 'U'}


class FrameLogger:
    def __init__(self, path, zone_names, meta):
        self.path = path
        self.zone_names = list(zone_names)
        self._f = io.open(path, 'w', encoding='utf-8', newline='')
        for k, v in meta.items():
            self._f.write('# {}={}\n'.format(k, json.dumps(v, ensure_ascii=False)))
        self._w = csv.writer(self._f)
        header = ['t', 'fn', 'stale_n', 'stale_t0', 'stale_t1', 'marker',
                  'n_tracks', 'n_points', 'n_points0', 'fold']
        for z in self.zone_names:
            header += [z + '_trk', z + '_trkf', z + '_tid', z + '_n', z + '_n0', z + '_snrmed', z + '_snrmax',
                       z + '_state', z + '_src', z + '_strong']
        header.append('tracks')
        self._w.writerow(header)
        self._stale_n = 0
        self._stale_t0 = None
        self._stale_t1 = None
        self._marker = ''
        self.rows = 0

    # 새 프레임 없이 monitor.update() 가 불렸을 때마다 호출
    def note_stale(self, now):
        if self._stale_n == 0:
            self._stale_t0 = now
        self._stale_t1 = now
        self._stale_n += 1

    # 입장/퇴장 마커. 다음 프레임 행에 붙는다.
    def mark(self, label):
        self._marker = label if not self._marker else self._marker + '|' + label

    def log_frame(self, now, fn, tracks, points, results, fold):
        rmap = {r['zone']: r for r in results}
        row = [round(now, 4), fn if fn is not None else '',
               self._stale_n,
               '' if self._stale_t0 is None else round(self._stale_t0, 4),
               '' if self._stale_t1 is None else round(self._stale_t1, 4),
               self._marker, len(tracks), len(points),
               sum(1 for p in points if p.doppler == 0), 1 if fold else 0]
        for z in self.zone_names:
            r = rmap.get(z, {})
            tid = r.get('tid')
            row += [1 if r.get('track_in_raw', r.get('track_in')) else 0,
                    1 if r.get('track_in') else 0,
                    -1 if tid is None else tid,
                    r.get('npts', 0),
                    r.get('npts0', 0),
                    round(r.get('snr_med', 0.0), 2),
                    round(r.get('snr_max', 0.0), 2),
                    STATE_CODE.get(r.get('state'), '?'),
                    r.get('source', ''),
                    1 if r.get('strong') else 0]
        row.append(json.dumps([[t.id, round(t.x, 3), round(t.y, 3), round(t.z, 3)]
                               for t in tracks], separators=(',', ':')))
        self._w.writerow(row)
        self._stale_n = 0
        self._stale_t0 = None
        self._stale_t1 = None
        self._marker = ''
        self.rows += 1

    def close(self):
        try:
            self._f.flush()
            self._f.close()
        except Exception:
            pass
