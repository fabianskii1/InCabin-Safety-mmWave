# Non-Vision 기반 차량 내부 위험 감지 시스템

mmWave 레이더(IWR6843ISK)만으로 카메라 없이 차량 실내의 위험을 감지합니다.
**뒷좌석 점유 → 시트 폴딩 잠금**, **운전자 호흡 이상 → 비상 회피**, **각성도 저하 모니터**를 하나의 디지털 트윈 화면에 연결했습니다.

---

## 1. 프로젝트 배경

### 1.1. 국내외 시장 현황 및 문제점

카메라 없이 차량 실내를 관측해야 하는 이유는 세 가지 위험에서 출발합니다.

| 위험 | 현황 | 기존 방식의 한계 |
|---|---|---|
| 영유아 방치·시트 끼임 | 미국에서 1998년 이후 누적 1,000명 이상의 아동이 차량 안에서 열사병으로 사망했고, 연평균 약 37명으로 감소 추세가 뚜렷하지 않습니다. 이 중 절반 이상이 보호자가 아이를 잊은 경우입니다(noheatstroke.org, National Safety Council 집계). 최근에는 3열 전동 폴딩 시트에 끼인 2세 아동이 질식 사망한 사고도 있었습니다 | 보호자의 기억에 의존. 전동 폴딩 시트는 탑승자 유무를 모른 채 동작 |
| 졸음·각성도 저하 | 2026년 상반기 고속도로 교통사고 사망자 95명 중 졸음운전·주시태만에 의한 사망자가 68명(71.6%)으로, 최근 3년 같은 기간 평균(46명) 대비 47.8% 증가했습니다(한국도로공사 집계) | 음주운전과 달리 사전 단속으로 걸러낼 수 없고, 본인도 자각하기 어려움 |
| 주행 중 급성 의식 소실 | 65세 이상 고령 운전자가 가해자인 교통사고는 2025년 45,873건으로 전년 대비 8.3% 증가했고, 사망자는 761명에서 843명으로 10.8% 늘었습니다(도로교통공단 TAAS) | 움직임이 없는 '정적 응급'은 자세나 조작 이력만으로 조기 포착이 어려움 |

기존 센서의 한계는 다음과 같습니다.

- **카메라:** 얼굴·시선·행동을 영상으로 기록해 데이터 유출 시 사생활 침해 위험이 직접적입니다. 저조도·역광에 취약하고, 선글라스·마스크·담요에 의한 가림에도 성능이 떨어집니다
- **좌석 압력 센서:** 영유아와 화물·카시트 하중을 구분하기 어렵고 생체 유무를 판별하지 못해, 직접 감지를 요구하는 평가 기준을 충족하기 어렵습니다 [1]
- **접촉식 생체 센서:** 착용이나 접촉 유지를 전제로 해 상시 안전 기능의 센서로는 부적합합니다
- **레이더 선행 연구:** 차내 감지는 대체로 존재·부재 판별에 집중되어 있고 [3], 운전자 상태 판별은 대부분 접촉식 ECG를 전제로 수행되어 왔습니다 [6][7]

### 1.2. 필요성과 기대효과

- **탑승자 안전:** 뒷좌석 점유를 카메라 없이 판정하고, 불확실하면 폴딩을 막아 끼임을 예방합니다. 영유아 보호로 확장할 기반이 됩니다.
- **운전자 보호:** 호흡 이상과 각성도 저하를 관측해 경고와 대응 흐름으로 연결합니다.
- **프라이버시:** 영상·착용 없이 관측하므로 사생활 부담이 적고, 조명과 가림의 영향을 덜 받는 방식입니다.
- **평가 요구 대응:** Euro NCAP은 어린이 존재 감지를 탑승자 모니터링 평가에 편입하면서, 도어 개폐 이력 같은 간접 추론이 아니라 움직임·호흡·심박에 근거한 직접 감지를 요구합니다 [1][2]. 본 과제는 이 직접 감지 원칙을 따르며, 상용 mmWave 센서의 출력 범위 안에서 구현했습니다.

---

## 2. 개발 목표

### 2.1. 목표 및 세부 내용

| 모듈 | 목표 | 세부 내용 |
|---|---|---|
| **모듈 1. 좌석 점유·폴딩 안전** | 뒷좌석에 사람이 있으면 시트 폴딩을 막는다 | 온칩 GTRACK 트랙을 좌석 영역에 매핑, 포인트클라우드 presence로 정지 승객 보완, 불확실하면 차단(fail-safe) |
| **모듈 2. 운전자 바이탈** | 호흡 이상·응급 상태를 감지한다 | 위상 변화에서 호흡수·심박수 추정, Breathing / Hold / Warning / Motion 상태 전이 판정 |
| **모듈 3. 각성도 저하 모니터** | 개인 기준선에서 벗어나는 상태를 관측한다 | HR 변동성(20초 창 표준편차) → 5분 평활 → 개인 z-정규화 → 임계 초과가 연속 4회면 이탈 판정 |
| **통합** | 세 판정을 한 화면에서 본다 | 각 파이프라인이 디지털 트윈 서버에 이벤트를 보내고, 브라우저 3D 화면이 상태를 표시 |

### 2.2. 기존 서비스 대비 차별성

| 항목 | 카메라 기반 | 압력 센서 | 접촉식 웨어러블 | 본 프로젝트 |
|---|---|---|---|---|
| 프라이버시 | 영상 촬영 | 양호 | 양호 | **영상 없음** |
| 저조도·가림 | 취약 | 무관 | 무관 | **영향 적음** |
| 생체 신호 | 간접 | 불가 | 가능 | **비접촉 호흡·심박** |
| 착용 필요 | 없음 | 없음 | 필요 | **없음** |
| 정지한 탑승자 | 가능 | 가능 | 가능 | 트랙 소실 문제를 **포인트클라우드 presence로 보완** |

### 2.3. 사회적 가치 도입 계획

- **공공성:** 차량 내 영유아 방치와 시트 끼임은 사회적 비용이 큰 사고입니다. 카메라 없이 감지해 보호자 기억에 의존하지 않도록 합니다.
- **프라이버시 보호:** 영상이나 착용 장비 없이 동작하므로 승객 동의 부담이 낮습니다.
- **지속 가능성:** 상용 센서와 공개 펌웨어 위에서 동작하도록 설계해 추가 하드웨어 없이 확장할 수 있습니다.

---

## 3. 시스템 설계

### 3.1. 시스템 구성도

```text
[IWR6843ISK #1]  3D People Tracking 펌웨어        [IWR6843ISK #2]  Vital Signs 펌웨어
        │ UART (트랙 TLV 1010 + 포인트 TLV 1020)          │ UART (트랙 + 바이탈 TLV 1040)
        ▼                                                  ▼
 gtrack_clearance_demo/                            vital_signs/
   main_presence_clearance.py                        twin_bridge.py
   - 좌표 변환 → 좌석 영역 매핑                        - 호흡 상태 판정 (Breathing/Hold/Warning)
   - 트랙 OR 포인트 presence                           - 각성도 판정 (drowsiness_judge.py)
   - 멈춘 트랙 필터 → 폴딩 허용 판정
        │ HTTP POST /event (source=gtrack_clearance)      │ HTTP POST /event (source=vital_signs)
        └──────────────┬────────────────────────────────┘
                       ▼
        computer_GUI/digital_twin/server.py   http://127.0.0.1:8766
          /             비상 회피 화면 (HR·RR·경고·각성도)
          /cabin.html   시트 폴딩 화면 (좌석 점유·폴딩 상태)
```

- 두 파이프라인은 **서로 다른 펌웨어**를 쓰므로 센서를 각각 준비해야 합니다. 센서가 하나면 펌웨어를 바꿔가며 한 번에 하나씩 실행합니다.
- 서버는 `clearance` 이벤트가 와도 바이탈 상태(`source`/`status`/`hr`/`rr`)를 덮어쓰지 않습니다.
- 데이터 흐름 그림: `iwr6843_dataflow.svg`, 보고서용 도식: `report_figures/`

### 3.2. 사용 기술

| 구분 | 내용 |
|---|---|
| 센서 | TI IWR6843ISK (60~64 GHz FMCW mmWave) |
| 펌웨어 | 3D People Tracking (좌석 점유), 공식 Vital Signs with People Tracking (바이탈) |
| 온칩 처리 | GTRACK 추적, Mahalanobis 거리 기반 게이팅 |
| 호스트 | Python 3.12, pyserial, numpy, pyqtgraph, PyOpenGL (3D 뷰) |
| 서버 | Python 표준 라이브러리 HTTP 서버 (`/state`, `/event`), 포트 8766 |
| 프론트엔드 | HTML + JavaScript + Three.js 기반 3D 캐빈 화면 |
| 도구 | TI Radar Toolbox 4.0, Industrial Visualizer, UniFlash |

### 3.3. 하드웨어 구성

| 구성 | 내용 |
|---|---|
| 센서 | TI IWR6843ISK 2대 (60~64 GHz FMCW, 안테나 일체형 ISK 보드) |
| 연결 | 보드당 CP2105 듀얼 UART 2포트 — 설정용 CLI(115200 bps), 데이터 수신용 Data(921600 bps) |
| 장착 | **두 대 모두 룸미러 부근**에 동일한 각도로 실내를 향해 설치. 좌석 영역 정의도 이 설치 위치를 원점으로 함 |
| 호스트 | 노트북 1대에서 두 파이프라인과 트윈 서버를 함께 실행 |

**센서별 펌웨어와 설정**

| 용도 | 펌웨어 | cfg |
|---|---|---|
| 좌석 점유·폴딩 | 3D People Tracking (`3D_people_track_6843_demo.bin`) | `chirp_configs/ISK_incabin_tightgate.cfg` |
| 운전자 바이탈·각성도 | 공식 Vital Signs with People Tracking | `vital_signs/chirp_configs/vital_signs_ISK_rearview.cfg` |

두 펌웨어는 서로 다른 이미지이므로 한 보드에서 동시에 쓸 수 없습니다. 센서가 한 대뿐이면 펌웨어를 바꿔가며 기능을 하나씩 실행합니다.

**좌석 영역 정의** (센서 원점 기준, X = 측방, Y = 전방 거리, 단위 m)

| 좌석 | X 범위 | Y 범위 | 폴딩 판정 |
|---|---|---|---|
| 조수석 | 0.10 ~ 0.70 | 0.40 ~ 1.00 | 제외 |
| 뒷좌석 좌 | −0.85 ~ −0.20 | 1.10 ~ 1.90 | 포함 |
| 뒷좌석 중앙 | −0.25 ~ 0.25 | 1.10 ~ 1.90 | 포함 |
| 뒷좌석 우 | 0.20 ~ 0.85 | 1.10 ~ 1.90 | 포함 |

좌석 영역은 센서 설치 위치를 원점으로 정의하므로, 센서를 옮기면 영역도 다시 맞춰야 합니다.

### 3.4. 안전 설계 원칙 (Fail-safe)

좌석 점유 판정은 놓치면 곧 사고로 이어지므로, 판단이 애매한 구간에서는 항상 차단 쪽으로 동작하도록 설계했습니다.

| 원칙 | 동작 |
|---|---|
| 불확실하면 차단 | 근거가 불충분한 상태(`UNKNOWN`)에서는 폴딩을 허용하지 않음 |
| 시작 직후 차단 | 실행 후 5초는 무조건 차단. 시동 시 이미 앉아 있는 승객을 놓치지 않기 위함 |
| 해제는 늦게 | 근거가 끊겨도 3초를 더 확인한 뒤에야 빈 좌석으로 확정 |
| 정지 승객 보완 | 트랙이 끊기거나 좌표가 멈춰도 포인트클라우드 근거로 점유를 유지 |
| 판정 대상 한정 | 폴딩은 뒷좌석 3개 좌석만 보고 판정. 조수석 승객은 끼임 대상이 아니므로 제외 |
| 표시와 판정 일치 | 디지털 트윈에 보내는 폴딩 허용 값은 판정기의 출력을 그대로 사용 |

그 결과 최종 측정에서 **탑승자가 있는 동안 폴딩이 허용된 경우는 0%**입니다(4.5절). 대신 빈 좌석을 잠시 더 막는 쪽의 오차가 남습니다.

---

## 4. 개발 결과

### 4.1. 전체 시스템 흐름도

```text
좌석 점유                                   운전자 바이탈
─────────                                   ─────────────
포인트클라우드 + 트랙                        흉부 미세 움직임(위상)
   ↓                                            ↓
좌석 영역 매핑                               호흡수·심박수 추정
   ↓                                            ↓
근거 판정 (트랙 OR 점 presence)              호흡 상태 전이
 · 점: 최근 8프레임 중 2프레임 이상            Breathing → Hold → Warning
 · 트랙: 좌표가 3초 넘게 멈추면 제외             ↓
   ↓                                         각성도: HR 변동성 → 개인 기준선 이탈
근거 소실 후 3초 → 빈 좌석 확정                  ↓
   ↓                                         경고 확인창 → 미확인 시 비상 정차(시뮬레이션)
뒷좌석 전부 빈 좌석 + 워밍업 경과 → 폴딩 허용
   ↓
디지털 트윈 화면 표시
```

### 4.2. 기능 설명 및 주요 기능 명세서

| 기능 | 입력 | 출력 | 설명 |
|---|---|---|---|
| 좌석 점유 판정 | 트랙 좌표(x, y, z), 포인트클라우드 | 좌석별 `UNKNOWN`/`EMPTY`/`OCCUPIED` | 좌석 영역 안의 트랙 또는 점 근거로 판정. 근거가 끊기면 3초 확인 후 빈 좌석 |
| 멈춘 트랙 필터 | 트랙 좌표 이력 | 근거에서 제외할 트랙 | 좌표가 3초 넘게 변하지 않으면 제외. 퇴장 후 남는 유령 트랙을 약 6초 안에 해제 |
| 폴딩 허용 판정 | 뒷좌석 3개 상태, 경과 시간 | `fold_permit` (true/false) | 뒷좌석이 모두 빈 좌석으로 확정되고 워밍업(5초)이 지나야 허용. 조수석은 판정에서 제외 |
| 호흡 상태 판정 | 칩 출력 호흡 변동 지표 | `BREATHING`/`HOLD`/`WARNING`/`MOTION` | Hold가 10초 지속되면 Warning. 확인창 미응답 시 비상 정차 시뮬레이션 |
| 각성도 모니터 | HR 시계열 | `CALIB`/`NORMAL`/`DEVIATED`, z값 | 세션 시작 후 약 11분 보정 뒤 판정. z ≥ 1.5가 연속 4회면 이탈 |

### 4.3. 디렉토리 구조


```text
InCabin-Safety-mmWave/
├─ gtrack_clearance_demo/        좌석 점유·폴딩 판정 (모듈 1)
│  ├─ main_presence_clearance.py   실행 파일 (센서 → 판정 → 트윈·로그)
│  ├─ track_parser.py              트랙 TLV 파싱
│  ├─ pointcloud_parser.py         트랙 + 포인트클라우드 파싱
│  ├─ clearance_gtrack.py          좌석 영역 정의·점유 상태머신
│  ├─ presence_monitor.py          presence 보완·멈춘 트랙 필터
│  ├─ plot_presence_3d.py          3D 캐빈 뷰
│  └─ clearance_publisher.py       트윈 서버로 좌석 상태 전송
├─ vital_signs/                  바이탈·각성도 (모듈 2·3)
│  ├─ run_twin.py                  공식 펌웨어 UART → 트윈
│  ├─ twin_bridge.py               파싱·호흡 상태 판정·전송
│  ├─ drowsiness_judge.py          각성도 저하 판정
│  ├─ run_visualizer.py            TI Industrial Visualizer 실행
│  └─ chirp_configs/               공식 cfg
├─ computer_GUI/
│  ├─ digital_twin/                트윈 서버 + 브라우저 3D 화면
│  └─ serialhelper.py              시리얼 공통 모듈
├─ chirp_configs/                 좌석 판정용 cfg
└─ report_figures/                보고서용 도식
```

### 4.4. 산업체 멘토링 의견 및 반영 사항

**자문:** 샌드버그 이사 이세진 (서면 자문의견서, 2026-08-07)

**종합 평가:** 카메라의 프라이버시 문제를 해결하면서 mmWave 레이더로 차내 생체 신호와 다중 탑승자를 감지하는 실용성 높은 연구. 하드웨어 한계를 조기에 파악해 센서를 교체하고, 인명 사고 방지를 위한 안전 중심(Zero-Trust) 알고리즘을 도입한 점이 우수.

**반영 사항**

| 자문 의견 | 반영 내용 |
|---|---|
| Non-vision(mmWave) 채택이 독창적 | 영상·착용 없이 관측하는 설계를 유지 |
| 약반사 탑승자를 위한 펌웨어 임계값 조정 | 연관 게이트를 캐빈 규모로 축소하고 표적 크기 파라미터를 차량 환경에 맞게 조정. 트랙이 끊겨도 포인트클라우드 presence(존 안 점 3개 이상)로 보완 |
| 실차 환경에서 자세 변화에 따른 정확도 실측 | 실차 23회 × 2회차(개선 전·최종), 좌석 4곳 × 정지·동작 조건으로 측정. 기대앉은 자세·똑바로 앉은 자세의 장시간 정지(120초)도 별도 측정 |
| 위험 알림 반응 시간(목표 1초 이내) 실측 | 최종 구성에서 **착석 → 점유 인식까지 평균 0.76초 / 최대 2.36초**(실차 20회, 문 열기 직전을 기준 시각으로 기록). 하차 → 빈 좌석 확정은 약 5.3초로, 오판을 막기 위해 둔 확인 시간 |
| 스마트워치 비교를 통한 심박·호흡 오차율 정량화 | **Apple Watch를 기준으로 비교 평가.** 정지 착석에서 1분 평균 오차 HR 4 bpm · RR 9회/분, 숨참기 인지 약 15초 |
| 안전 중심(Zero-Trust) 알고리즘 | 근거가 불확실하면 항상 점유(차단)로 판정, 시작 직후 5초 무조건 차단, 근거 소실 후 3초 확인 뒤 빈 좌석 확정 → 최종 측정에서 **착석 중 폴딩 허용 0%** |

> 주행 중 진동 보정, 영유아 체구 조건은 아직 반영하지 못했습니다. 현재 한계는 4.2절에 정리했습니다.

### 4.5. 분석 결과

| 모듈 | 평가 조건 | 결과 |
|---|---|---|
| 좌석 점유·폴딩 | 실차 23회 × 2회차(2026-09-09 / 2026-09-19), 좌석별 정답 라벨, 착석 인식 이후 기준 | 탑승자 존재 중 폴딩 허용 9.02% → **0%**, 점유 검출 Recall 89.1% → **99.9%**, 정지 착석 유지율 83.2% → **99.9%**, 공석 오차단 53.0% → **17.3%** |
| 좌석 반응 시간 | 위와 동일 | 착석 → 점유 인식 평균 **0.76초** / 최대 2.36초, 하차 → 빈 좌석 확정 약 5.3초 |
| 운전자 바이탈 | 정지 착석, Apple Watch 기준 비교 | 1분 평균 오차 HR **4 bpm** · RR **9회/분**, 숨참기 인지 약 **15초** |
| 각성도 | 공개 ECG DB 3종·84명, 최종 검증 7명·14세션 | ROC **AUC 0.689**, Recall 0.764 |

**한계**

- 좌석 구분 정확도는 낮아졌습니다(뒷좌석 우측 F1 85.2 → 64.0). 옆 좌석을 점유로 표시하는 경우가 늘었습니다. 폴딩은 뒷좌석 전체를 보고 판정하므로 안전에는 영향이 없습니다.
- 남은 공석 오차단 17.3%는 전부 조수석 탑승 상황입니다. 조수석 승객의 반사가 뒷좌석 우측 영역에 잡힙니다.
- 두 명이 함께 정지해 있는 조건은 재측정 대기 상태이고, 영유아 탑승과 주행 중 조건은 미검증입니다.
- 각성도는 공개 ECG 데이터셋으로 평가했고, 레이더로 직접 측정한 졸음 데이터는 아직 없습니다.

### 4.6. 향후 과제

| 과제 | 내용 |
|---|---|
| 다인 탑승 검증 | 두 명이 함께 정지해 있는 조건 재측정, 뒷좌석 만석(3인) 조건 추가 |
| 영유아 조건 | 카시트 착좌·담요 가림 상태에서의 검출 성능 확인. 현재는 성인만 검증 |
| 주행 중 측정 | 차량 진동·노면 환경에서의 판정 안정성과 진동 보정 방안 |
| 조수석 간섭 완화 | 남은 공석 오차단의 원인인 조수석 반사의 뒷좌석 영역 유입 억제 |
| 좌석 구분 정확도 | 인접 좌석 오점유를 줄여 좌석별 표시 정확도 회복 |
| 레이더 기반 각성도 | 공개 ECG가 아닌 레이더 실측 졸음 데이터 수집 후 재평가 |

---

## 5. 설치 및 실행 방법

### 5.1. 설치절차 및 실행 방법

**준비물**

- IWR6843ISK 보드 (동시 시연 시 2대), USB 케이블
- Python 3.12 (`pip install pyserial numpy pyqtgraph PyOpenGL`)
- TI Radar Toolbox 4.0 (Visualizer·펌웨어 바이너리), UniFlash (펌웨어 플래시용)

**펌웨어**

| 용도 | 이미지 |
|---|---|
| 시트 폴딩 | `3D_people_track_6843_demo.bin` |
| 비상 회피·각성도 | `vital_signs_tracking_6843ISK_demo.bin` |

UniFlash에서 Device `IWR6843`, Enhanced COM 포트, Meta Image 1에 해당 `.bin`을 올립니다. 플래시할 때는 SOP를 flashing 모드로, 끝나면 functional 모드로 되돌리고 리셋합니다.

**실행 (터미널 3개, 포트 번호는 장치 관리자에서 확인)**

```bash
# 1) 디지털 트윈 서버 (포트 8766)
python computer_GUI/digital_twin/server.py

# 2) 바이탈·각성도  (CLI COM4 / DATA COM3 예시)
python vital_signs/run_twin.py COM4 COM3 --no-serve

# 3) 좌석 점유·폴딩 (CLI COM10 / DATA COM11 이 기본값)
python gtrack_clearance_demo/main_presence_clearance.py
```

- 브라우저: 비상 회피 `http://127.0.0.1:8766/`, 시트 폴딩 `http://127.0.0.1:8766/cabin.html`
- 센서가 하나뿐이면 2)와 3)을 동시에 실행할 수 없습니다. 펌웨어를 바꿔가며 하나씩 실행합니다.
- 좌석 판정 설정(K=2, F=3초, E=3초)과 실행 옵션은 `gtrack_clearance_demo/README.md` 참고.

**하드웨어 없이 로직 검증**

```bash
python gtrack_clearance_demo/track_parser.py
python gtrack_clearance_demo/clearance_gtrack.py
python gtrack_clearance_demo/presence_monitor.py
```

### 5.2. 오류 발생 시 해결 방법

| 증상 | 원인·해결 |
|---|---|
| COM 열기 실패 | Industrial Visualizer나 다른 실행 창이 포트를 잡고 있습니다. 모두 닫고 다시 실행하세요 |
| 수신 프레임 수가 0에서 멈춤 | CLI/DATA 포트가 바뀌었거나 다른 보드를 가리키고 있습니다. Enhanced=CLI, Standard=DATA 입니다. 보드 NRST 후 다시 실행하세요 |
| 트랙은 잡히는데 계속 `SEARCHING` | 사람이 센서를 향해 앉아 20초 이상 정지해야 바이탈이 나옵니다 |
| 트윈 화면이 갱신되지 않음 | 서버(`server.py`)가 먼저 켜져 있어야 합니다. 바이탈 쪽은 `--no-serve`로 실행하세요 |
| 3D 창이 열리지 않음 | PyOpenGL 미설치. `--no-plot`으로 콘솔만 실행할 수 있습니다 |
| 각성도가 계속 "보정 중" | 세션 시작 후 약 11분이 지나야 판정이 나옵니다 |

---

## 6. 소개 자료 및 시연 영상

### 6.1. 프로젝트 소개 자료

발표 자료(PPT) 최신본 링크 및 파일 등록 **추후 작성 예정**.

### 6.2. 시연 영상

**https://youtu.be/bLk3_OS6USk**

주요 장면

1. 빈 뒷좌석에서 폴딩 허용 상태
2. 뒷좌석 탑승 → 폴딩 잠금, 정지 착석에서도 잠금 유지
3. 하차 후 약 5초 뒤 폴딩 허용으로 복귀
4. 운전자 정상 호흡 상태 표시
5. 무호흡 → Hold → Warning → 확인창 → 비상 정차 시뮬레이션

> 화면의 비상 정차와 시트 동작은 시뮬레이션입니다.

---

## 7. 팀 구성

### 7.1. 팀원별 소개 및 역할 분담

팀명: **쭌혁뀬** (38조) · 지도교수: 백윤주 · 소속: 부산대학교 정보컴퓨터공학부

| 학번 | 이름 | 역할 |
|---|---|---|
| 202155597 | 임도균 | 운전자 바이탈 파이프라인(호흡수·심박수 추출, 무호흡 판정), 디지털 트윈 서버·3D 화면, 발표 |
| 202155535 | 김재혁 | 각성도 저하 판정 로직(HR 변동성·개인 기준선 이탈), 공개 ECG 데이터셋 검증, 트윈 각성도 표시 연동 |
| 202155591 | 이준수 | 좌석 점유·폴딩 안전 판정 모듈 설계·구현, 실차 검증 |

### 7.2. 팀원 별 참여 후기

| 이름 | 참여 후기 |
|---|---|
| 임도균 |  |
| 김재혁 |  |
| 이준수 |  |

---

## 8. 참고 문헌 및 출처

**평가 제도**

[1] Euro NCAP, *Child Presence Detection Test and Assessment Protocol*, Version 2.0, Jul. 2025.
[2] Euro NCAP, *Safe Driving — Occupant Monitoring Protocol*, Version 1.1, Oct. 2025.

**mmWave 레이더 센싱**

[3] A. Caddemi and E. Cardillo, "Automotive Anti-Abandon Systems: a Millimeter-Wave Radar Sensor for the Detection of Child Presence," TELSIKS, pp. 94–97, 2019.
[4] M. Alizadeh, G. Shaker, J. C. M. De Almeida, P. P. Morita, and S. Safavi-Naeini, "Remote Monitoring of Human Vital Signs Using mm-Wave FMCW Radar," *IEEE Access*, vol. 7, pp. 54958–54968, 2019.
[5] C. Li, V. M. Lubecke, O. Boric-Lubecke, and J. Lin, "A Review on Recent Advances in Doppler Radar Sensors for Noncontact Healthcare Monitoring," *IEEE Transactions on Microwave Theory and Techniques*, vol. 61, no. 5, pp. 2046–2060, 2013.

**각성도·생체 신호 기반 상태 판별**

[6] J. Vicente, P. Laguna, A. Bartra, and R. Bailon, "Drowsiness detection using heart rate variability," *Medical & Biological Engineering & Computing*, vol. 54, no. 6, pp. 927–937, 2016.
[7] G. Lenis et al., "Detection of microsleep events in a car driving simulation study using electrocardiographic features," *Current Directions in Biomedical Engineering*, vol. 2, no. 1, pp. 283–287, 2016.
[8] F. Guede-Fernandez, M. Fernandez-Chimeno, J. Ramos-Castro, and M. A. Garcia-Gonzalez, "Driver Drowsiness Detection Based on Respiratory Signal Analysis," *IEEE Access*, vol. 7, pp. 81826–81838, 2019.
[9] S. H. Jo, J. M. Kim, and D. K. Kim, "Heart Rate Change While Drowsy Driving," *Journal of Korean Medical Science*, vol. 34, no. 8, e56, 2019.
[10] A. El Abbaoui, D. Sodoyer, and F. Elbahhar, "Contactless Heart and Respiration Rates Estimation and Classification of Driver Physiological States Using CW Radar and Temporal Neural Networks," *Sensors*, vol. 23, no. 23, 9457, 2023.

**검증에 사용한 공개 데이터셋**

[11] *Drivers Drowsiness Database (DD-Database)*, Dryad, doi:10.5061/dryad.5tb2rbp9c, 2023.
[12] Q. Massoz, T. Langohr, C. Francois, and J. G. Verly, "The ULg Multimodality Drowsiness Database (DROZY) and Examples of Use," IEEE WACV, 2016.
[13] Q. Meteier et al., "Influence of Mild Sleep Deprivation and Driving Environment on Driver State in Monotonous Conditionally Automated Driving," *Transportation Research Part F*, 2022.

**센서·펌웨어 문서**

[14] Texas Instruments, *IWR6843 Single-Chip 60-GHz to 64-GHz Intelligent mmWave Sensor*, Datasheet, 2021.
[15] Texas Instruments, *3D People Counting / Tracking User's Guide (GTRACK)*, Radar Toolbox.
[16] Texas Instruments, *Vital Signs with People Tracking User's Guide*, Radar Toolbox.

**추적 이론**

[17] S. S. Blackman, *Multiple-Target Tracking with Radar Applications*, Artech House, 1986.
[18] Y. Bar-Shalom, X.-R. Li, and T. Kirubarajan, *Estimation with Applications to Tracking and Navigation*, John Wiley & Sons, 2001.

> 통계 수치의 출처(noheatstroke.org, National Safety Council, 한국도로공사, 도로교통공단 TAAS 등)와 전체 문헌 목록은 최종 보고서 6장에 있습니다.

---

## 9. 라이선스 및 고지

- 본 저장소의 코드는 부산대학교 정보컴퓨터공학부 졸업과제(2026 전기, 38조) 결과물입니다. 별도 라이선스 표기 전까지는 학술·교육 목적의 참고 용도로만 사용해 주세요.
- TI 펌웨어 바이너리(`3D_people_track_6843_demo.bin`, 공식 Vital Signs 데모)와 TI Radar Toolbox·Industrial Visualizer는 **Texas Instruments의 배포 조건**을 따릅니다. 본 저장소는 이를 재배포하지 않으며, TI Resource Explorer에서 직접 내려받아야 합니다.
- 각성도 검증에 사용한 공개 데이터셋(DD-Database, DROZY 등)은 각 데이터셋의 이용 약관을 따릅니다. 본 저장소에는 원본 데이터를 포함하지 않습니다.
- 디지털 트윈 화면의 비상 정차와 시트 폴딩 동작은 **시뮬레이션**이며, 실제 차량 액추에이터를 제어하지 않습니다.
- 본 시스템은 연구·검증 목적의 시제품으로, 안전 인증을 받은 제품이 아닙니다.
