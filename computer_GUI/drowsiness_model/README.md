# drowsiness_model — 졸음 판별 모델 파이프라인

운전자 HR/RR 특징으로 각성/졸림을 판별하는 오프라인 학습 파이프라인.
공개데이터로 모델을 학습하고, 같은 특징(`features.py`)을 레이더에도 적용해 이어붙인다.

## 파일
- `features.py` — **특징 계약**. RR/HR 시계열 → 윈도우 특징(rr_mean, rr_std, hr_mean, hr_std, cv).
  학습(공개데이터)과 추론(레이더)이 반드시 같은 함수로 특징을 만든다.
- `load_drivedb.py` — PhysioNet drivedb 로더 (스트레스, 파이프라인 리허설용).
- `load_drozy.py` — DROZY 로더 (ECG→HR, EDR→RR, KSS 졸림 라벨). neurokit2·pyedflib 필요.
- `train_drowsiness.py` — 특징추출 → 상태별 통계비교 → SVM(subject-wise CV).
- `../drowsiness_feature_logger.py` — 레이더(IWR6843) HR/RR을 같은 특징으로 실시간 추출.

## 실행
```
pip install wfdb scikit-learn scipy numpy neurokit2 pyedflib
# 리허설(스트레스, 자동 다운로드):
python train_drowsiness.py --dataset drivedb --records drive05 drive06
# 진짜 졸음(DROZY):
python train_drowsiness.py --dataset drozy --drozy-dir <DROZY경로> --baseline-norm
```

## 현재까지의 결론 (2026-07)
- HR/RR 특징은 각성 vs 졸림을 **통계적으로 유의하게 구분**함(rr_mean↓, hr_std↑ 등).
- 그러나 **subject-independent 절대분류는 어려움**(개인차 큼, DROZY는 세션당 라벨 1개로 부족).
- → 방향: **개인 baseline 대비 이탈 감지**(실차 보정 방식) + 대규모 데이터(UL-DD/346명) 확보 후 재학습.
