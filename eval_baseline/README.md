# 베이스라인 성능 (트래킹-only, presence 폴백 적용 전)

- **측정일**: 2026-09-09
- **구성**: 3D People Tracking 펌웨어 + GTRACK 좌석 clearance, **트래킹만**(포인트클라우드 presence 폴백 미적용)
- **트라이얼**: 23개 — empty×3, passenger_solo(static×3·motion×2), rearL/C/R_solo(각 static×3·motion×2)
- **존셋**: vehicle, **프로토콜**: (일부) 콜드스타트 착석 포함 — 이후 라운드는 입장(walk-in) 프로토콜로 통일 예정
- **미측정**: rear_pair / rear_full / rearC_entry(반응지연) → 최종(개선 후) 라운드에서 측정

## 핵심 수치 (before)
| 지표 | 값 |
|---|---|
| 위험 오허용(뒷좌 점유 중 FOLD 허용) | **16.3%** (3775/23174), 최장 ≈20초 |
| 점유 검출 Recall(종합) | 81.7% |
| 정지 착석 검출 유지율 | 83.2% |
| 좌석 Recall | Passenger 78.8 / Rear-L 87.3 / **Rear-C 68.1** / Rear-R 92.6 |
| 좌석 Precision | Passenger 100 / Rear-L 85.7 / Rear-C 90.8 / **Rear-R 78.9** |
| 오차단률(공석인데 차단) | 53.0% |
| 위치오차 RMSE | X 0.070m / Y 0.185m |
| 고스트 트랙률 | 56.5% (평균 +1.0, 1명→최대 11트랙) |

## 진단 요약
- per-frame 점유가 양방향으로 흔들림: 점유 시 과허용(위험 16.3%) + 공석 시 과차단(53%).
- 근본 원인 = **과분할**(원거리 뒷좌 멀티패스). 유령이 인접 존 누유 → FP·과차단, 실제 존 순간 비움 → EMPTY 오판·위험.
- Rear-C 검출 최악(68%), Rear-R 과검출(FP↑).

## 존 정의 비교 (2026-09-09, 동일 데이터 재채점)
`rezone_compare.py`로 baseline의 raw 트랙(tracks_json)을 vehicle/placeholder 두 존셋에 각각 replay(재생 신뢰성 99.9%). **placeholder 존이 전방위 우세** → **`DEFAULT_SEATS`를 placeholder로 전환.**

| 지표 | vehicle | placeholder |
|---|---|---|
| 위험 오허용(뒷좌) | 16.3% | **8.8%** (거의 절반) |
| 점유 Recall(종합) | 81.7% | **89.5%** |
| Rear-C F1 | 77.2 | **87.1** |
| Rear-R F1 | 85.2 | **90.6** |
| 오차단률 | 53.0% | 53.6% (동일) |

원인: vehicle 존(OD-cfg 변환)이 너무 타이트(뒷좌 깊이 0.64m) → Y오차 0.185m에 트랙이 존밖 드리프트 → 놓침·위험. placeholder(깊이 0.80m)가 실제 트랙 위치를 더 잘 담음. **오차단 53%는 존 무관(과분할) → P2 대상.** 상세: `rezone_vehicle_vs_placeholder.txt`.

## 용도
포스터/보고서의 **before(문제 근거)**. 개선(존 재정의 → placeholder, presence 폴백, 누유 억제) 후 재측정값과 before/after 비교에 사용.

## 파일
- `eval_log_baseline.csv` — 원본 프레임 로그(23 trial)
- `report_baseline.txt` — eval_score 전체 리포트
- `baseline_seat_prf.png` / `baseline_safety.png` — plot
