# GTRACK 기반 좌석 Clearance 데모

> 3D People Tracking 펌웨어(GTRACK)가 주는 **트랙 좌표** 위에, 호스트에서
> **좌석 존(3D 박스)** 점유를 판정해 시트 폴딩 clearance를 결정한다.
> 기존 OD-demo clearance의 난제(멀티패스·정적클러터·약반사·identity)를
> GTRACK이 이미 처리하므로, 호스트는 "트랙이 존 안에 있나"만 보면 된다.

## 전제
- 센서에 **3D People Tracking 펌웨어**(`3D_people_track_6843_demo.bin`)가 플래시돼 있어야 함
- cfg: `ISK_incabin_multi.cfg` (근거리 캐빈 튜닝본)

## 실행
```bash
# 하드웨어 없이 로직 검증
py track_parser.py       # UART 파서 왕복 테스트
py clearance_gtrack.py   # 존 점유 상태머신 self-test

# 센서 (Industrial Visualizer 를 먼저 닫아 COM 포트 해제)
py main_gtrack_clearance.py <userCOM> <dataCOM> \
   "E:/radar_toolbox_4_00_00_05/source/ti/examples/Industrial_and_Personal_Electronics/People_Tracking/3D_People_Tracking/chirp_configs/ISK_incabin_multi.cfg" --plot

# 비주얼라이저가 이미 cfg 보내 스트리밍 중이면 (config 재전송 없이)
py main_gtrack_clearance.py <userCOM> <dataCOM> <cfg> --no-config --plot
```

## 파일
| 파일 | 역할 |
|---|---|
| `track_parser.py` | 3D People Tracking UART → Target List(트랙 x,y,z) 파싱. 매직/헤더 `Q8I`, TLV 308 `I27f` |
| `clearance_gtrack.py` | 좌석 존 정의 + 존 점유 비대칭 히스테리시스 상태머신 + fold 허용 + self-test |
| `main_gtrack_clearance.py` | 센서 스트림 → 파서 → 존 clearance → 콘솔/로그. `serialhelper` read-only 재사용 |
| `plot_gtrack_clearance.py` | 톱다운 캐빈 맵(존 점유색 + 트랙 + FOLD 배너), `--plot` |

## ★ 캘리브레이션 (필수)
`clearance_gtrack.py`의 `DEFAULT_SEATS` 좌석 존 박스는 **플레이스홀더**다.
실제로는 각 좌석에 앉았을 때 **트랙의 (x,y) 좌표를 콘솔에서 보고** 박스를 맞춰야 한다:
```python
Zone('Passenger', xmin, xmax, ymin, ymax)   # 센서 좌표 m, X=측방 Y=거리
```
`sensorPosition`(cfg)이 실제 마운트와 맞아야 좌표가 정확하다.

## fail-safe 주의 (실차 적용 시)
- **cold-start 정지자**: 시작부터 완전 정지한 사람은 트랙이 안 생겨 존이 "빈 좌석"으로
  오판될 수 있음. 완화책 내장: ① `WARMUP_BLOCK_SEC` 시작 차단 ② `EMPTY_CONFIRM_SEC`
  조용함 요구. **추가로 point cloud presence 병용을 켜는 것**을 권장(향후 v2).
- fold 허용은 **전 존 EMPTY 확정 + 워밍업 경과**일 때만.

## 디지털 트윈

`main_gtrack_clearance.py` 가 `http://127.0.0.1:8766/event` 로 좌석 점유를 보냅니다.
트윈 서버를 먼저 켜 두세요.

```text
python computer_GUI/digital_twin/server.py
```

브라우저: [http://127.0.0.1:8766/cabin.html](http://127.0.0.1:8766/cabin.html)

이 POST는 clearance만 갱신합니다. 비상 회피 페이지의 바이탈 상태는 유지됩니다.
공식 Vital Signs `.bin` 과 3D People Tracking `.bin` 은 동시에 쓸 수 없습니다.
`serialhelper.py` 는 `computer_GUI/` 에 있습니다.
