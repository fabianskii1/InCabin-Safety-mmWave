# vital_sign

IWR6843ISK mmWave로 **운전자 바이탈 → 비상 회피**와 **뒷좌석 점유 → 시트 폴딩 잠금**을
같은 디지털 트윈 서버에 붙입니다. 두 파이프라인은 펌웨어가 다르므로 **같은 COM에서 동시에 돌릴 수 없습니다**.

```text
vital_signs/run_twin.py          공식 Vital Signs .bin
        │  POST source=vital_signs
        ▼
digital_twin/server.py           http://127.0.0.1:8766
  /              비상 회피 (HR, RR, Warning 확인창)
  /cabin.html    시트 폴딩
        ▲
        │  POST source=gtrack_clearance  (clearance만 갱신, 바이탈 유지)
gtrack_clearance_demo/
```

## 폴더

```text
vital_sign/
  vital_signs/                 공식 Vital Signs UART → 트윈
  computer_GUI/
    digital_twin/              브라우저 3D + /state /event
    serialhelper.py            gtrack 데모가 재사용
  gtrack_clearance_demo/       3D People Tracking → 좌석 clearance
```

## 1) 비상 회피 (Vital Signs)

보드에 공식 `vital_signs_tracking_6843ISK_demo.bin` 이 올라가 있어야 합니다.
Visualizer는 끄세요. COM이 겹칩니다.

```text
python vital_signs/run_twin.py
```

기본값: COM4 / COM5, cfg `vital_signs/chirp_configs/vital_signs_ISK_rearview.cfg`.
트윈 서버가 꺼져 있으면 이 스크립트가 `8766`을 같이 켭니다.

브라우저: [http://127.0.0.1:8766](http://127.0.0.1:8766)

HUD 배지가 `LIVE`이고 HR/RR이 움직이면 연동된 것입니다.

| 칩/PC 판정 | 트윈 | 화면 |
|---|---|---|
| 트랙 없음 | `SEARCHING` | 검색 |
| Breathing | `BREATHING` | 정상 주행 |
| Hold | `HOLD` | 무호흡 초만 증가. 갓길 안 감 |
| Warning (Hold 10초) | `WARNING` | 5초 뒤 확인창 |
| 확인 누름 | 이번 Warning 취소 | 계속 주행 |
| 확인 안 함 5초 | `DANGER` | 갓길 정차 |
| Motion | `MOTION` | 움직임. 갓길 안 감 |

Hold 기준은 칩 `breathDeviation` < 0.025 입니다.

선택: `python vital_signs/run_visualizer.py` — `run_twin`과 동시에 켜지 마세요.

## 2) 시트 폴딩 (GTRACK clearance)

보드에 **3D People Tracking** (`3D_people_track_6843_demo.bin`) 이 올라가 있어야 합니다.
Vital Signs `.bin` 과는 다른 이미지입니다. 플래시를 바꾼 뒤에만 이 데모를 켭니다.

트윈 서버를 먼저 켜 두세요.

```text
python computer_GUI/digital_twin/server.py
```

그다음 (Industrial Visualizer는 닫고):

```text
python gtrack_clearance_demo/main_gtrack_clearance.py COM4 COM5 "<ISK_incabin_multi.cfg 경로>" --plot
```

이미 스트리밍 중이면 `--no-config` 를 붙입니다.

브라우저: [http://127.0.0.1:8766/cabin.html](http://127.0.0.1:8766/cabin.html)

뒷좌석 L/C/R 이 비면 폴딩이 허용되고, 사람이 있으면 잠깁니다.
서버는 clearance POST가 와도 비상 페이지의 `source`/`status`/`hr`/`rr` 을 덮어쓰지 않습니다.

하드웨어 없이 로직만 보려면:

```text
python gtrack_clearance_demo/track_parser.py
python gtrack_clearance_demo/clearance_gtrack.py
```

좌석 박스 캘리브레이션은 `gtrack_clearance_demo/README.md` 를 보세요.

## 펌웨어

| 용도 | 이미지 | 같이 쓰면 안 되는 것 |
|---|---|---|
| 비상 회피 | `vital_signs_tracking_6843ISK_demo.bin` | 3D People Tracking, occupancy |
| 시트 폴딩 | `3D_people_track_6843_demo.bin` | 공식 Vital Signs |

이미 올라가 있으면 다시 플래시하지 않아도 됩니다.
다시 넣을 때는 UniFlash에서 Device `IWR6843`, COM4 Enhanced, Meta Image 1에 해당 `.bin`을 올립니다.
