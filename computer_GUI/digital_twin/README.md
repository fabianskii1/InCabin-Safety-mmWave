# Digital Twin

IWR6843ISK occupancy 펌웨어가 뽑은 운전자 상태를 브라우저 3D 화면에 붙이는 모듈입니다.
공식 TI Vital Signs Visualizer와는 연결되지 않습니다.

두 화면이 있습니다.

| 페이지 | 역할 | 실측 연동 |
|---|---|---|
| [`/`](index.html) 비상 회피 | 고속도로에서 운전자 위험 시 감속 → 우측 차선 → 갓길 정차 | `driver_vitals` / `driver_vitals_sep` |
| [`/cabin.html`](cabin.html) 시트 폴딩 잠금 | 뒷좌석 탑승 시 등받이 폴딩을 막는 시연 | 버튼/키 데모만. occupancy 실시간 연동 없음 |

---

## 실행

`computer_GUI`에서 서버를 켭니다. 추가 패키지는 없습니다. 표준 라이브러리만 씁니다.

```text
python digital_twin/server.py
```

브라우저: [http://127.0.0.1:8766](http://127.0.0.1:8766)

서버만 켜면 화면은 데모 모드입니다. HUD 배지에 `DEMO`가 뜹니다.
긴급/정상/리셋은 버튼 또는 `D` / `N` / `R`입니다.

실측을 붙이려면 서버를 켠 채로 occupancy GUI를 같이 실행합니다.

```text
python driver_vitals/main_driver.py COM4 COM5 driver_vitals/vod_vs_rearview_mirror.cfg
```

또는

```text
python driver_vitals_sep/main_sep.py COM4 COM5 ...
```

COM 번호는 PC에 맞게 바꿉니다. Enhanced(CLI) / Standard(DATA) 한 쌍입니다.
GUI가 매 프레임 `TwinPublisher`로 상태를 밀어 넣고, HUD 배지가 `LIVE`로 바뀝니다.

서버가 꺼져 있으면 GUI는 한 번만 안내를 찍고 2초마다 재시도합니다. 측정 루프는 멈추지 않습니다.

---

## 연결

```text
IWR6843ISK
  UART CLI  115200
  UART DATA 921600
        │
        ▼
driver_vitals / driver_vitals_sep
  TLV 파싱 → PlotHelper → DriverUiState
        │
        │  TwinPublisher
        │  POST http://127.0.0.1:8766/event
        │  timeout 50 ms, 실패 시 2초 재시도
        ▼
digital_twin/server.py   (ThreadingHTTPServer, 127.0.0.1:8766)
  메모리 _state 갱신
  status == DANGER  → cmd = EMERGENCY_START
  status == NORMAL / SEARCHING → cmd = None
        │
        │  브라우저 GET /state  160 ms 폴링
        ▼
js/twin.js
  applySensor()
  DANGER + EMERGENCY_START 이면 갓길 시나리오 시작
```

폴링을 쓰는 이유입니다. 시각화는 로컬 데모이고, 센서는 가끔 붙습니다. WebSocket을 넣지 않고 HTTP GET/POST만 썼습니다. CORS는 `*`입니다.

시트 폴딩 화면(`cabin.html`)은 이 파이프를 읽지 않습니다. occupancy zone 결과를 아직 받지 않습니다.

---

## 상태 JSON

`POST /event`와 `GET /state`가 같은 키를 씁니다.

| 키 | 의미 | 예 |
|---|---|---|
| `source` | 출처 | `driver_vitals`, `mock`, `idle` |
| `status` | 운전자 상태 | `SEARCHING`, `NORMAL`, `DANGER` |
| `present` | 운전석에 사람 있는지. `SEARCHING`이면 false | `true` / `false` |
| `rr` | 호흡수 (회/분) | `14` |
| `hr` | 심박수 (회/분) | `72` |
| `apnea_sec` | 무호흡으로 본 누적 시간 | `3.5` |
| `detail` | GUI가 붙인 한글 설명 | `무호흡 3.5초` |
| `cmd` | 트윈 명령. 비우면 서버가 status로 채움 | `EMERGENCY_START` 또는 `null` |

occupancy GUI의 내부 호흡 라벨과 트윈 status 매핑입니다.

| GUI 호흡 판정 | `DriverUiState.status` | 트윈 동작 |
|---|---|---|
| 운전자 없음 | `SEARCHING` | 시나리오 유지, cmd 없음 |
| `BREATHING` / `HOLD`, 움직임 보류 | `NORMAL` | 갓길에 이미 서 있으면 명령만 해제 |
| `EMERGENCY` (무호흡) | `DANGER` | 비상 회피 시작 |

`HOLD`는 트윈에서 위험으로 보지 않습니다. 무호흡이 `EMERGENCY`로 올라간 뒤에만 갓길로 갑니다.

---

## 화면 기술

### 비상 회피 (`js/twin.js`)

- Three.js r160, import map + unpkg CDN. 로컬 npm 번들은 없습니다.
- 3차로 + 갓길, 타일 도로를 자차 진행 방향으로 재사용합니다.
- 주변 차는 차선별 목표 속도, 앞차 간격, 감속으로 움직입니다.
- 자차 상태: `CRUISE` → `EMERGENCY`(감속, 우측 차선 변경, 갓길 합류) → `STOPPED`.
- 차선 변경 전 `gapClear()`로 옆 차 간격을 보고, 막히면 대기합니다.
- HUD: 속도(km/h), 전방 간격, RR, HR, 무호흡 초, `LIVE`/`DEMO`.

데모 버튼은 같은 `/event`로 서버 상태를 맞춥니다. 센서 GUI와 브라우저를 같이 켜도 마지막 POST가 이깁니다.

### 시트 폴딩 (`js/cabin.js`)

- Three.js + OrbitControls + ColladaLoader.
- 기본 모델: SketchUp Collada `models/car_interior/model.dae`.
- `Seat_6` 등받이 메시를 힌지 기준으로 잘라 `rearFoldPivot`에 붙입니다.
- 뒷좌-좌/중/우 중 한 명이라도 있으면 폴딩을 막고 잠금 표시를 켭니다.
- 모델이 없으면 박스 캐빈으로 떨어집니다.

모델 출처는 [`models/ATTRIBUTION.txt`](models/ATTRIBUTION.txt)에 있습니다.

---

## 파일

```text
digital_twin/
  server.py      정적 파일 + /state + /event
  bridge.py      occupancy GUI → POST /event
  index.html     비상 회피
  cabin.html     시트 폴딩
  js/twin.js
  js/cabin.js
  css/twin.css
  models/        실내 Collada
```

---

## 범위

되는 것: occupancy 펌웨어 UART → 운전자 `SEARCHING`/`NORMAL`/`DANGER` → 비상 회피 화면.

아직 아닌 것:

- 공식 TI Vital Signs Visualizer, GTRACK, `official_ti_vs` CSV 로그
- 주행 중 생체신호 복원. 트윈은 GUI가 준 상태를 재생만 합니다
- 뒷좌석 occupancy → `cabin.html` 실시간 잠금
- WebSocket, 원격 호스트. 기본은 `127.0.0.1`만 듣습니다
