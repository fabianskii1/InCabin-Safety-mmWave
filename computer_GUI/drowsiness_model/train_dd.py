"""
DD-Database(raw ECG, 2시간 연속)로 '실시간 코드와 동일한 20초 창' 기준 졸음 판별 검증.

이전 시도들과의 차이:
  DROZY  - 세션당 라벨 1개 -> 독립 표본 부족, 절대값 분류 실패
  Exp4   - 최소 180초 단위로 이미 뭉개진 특징 -> 20초 창과 스케일 불일치로 모델 배포 불가
  DD(여기) - raw ECG를 우리가 직접 20초 창으로 자름 -> 학습/배포 스케일 일치

검증 결과가 baseline을 넘으면, 이 데이터로 학습한 모델은 스케일이 맞으므로
실시간 코드에 그대로 배포(joblib 저장/로드)해도 안전하다.

실행: python train_dd.py [--limit-sec 1800] [--save-model]
  --limit-sec : 각 세션에서 앞 N초만 사용(빠른 시험용). 미지정시 전체 2시간
  --save-model: 검증 통과 시 모델을 drowsy_hr_model.pkl로 저장
"""
import argparse
import sys

import numpy as np

sys.path.insert(0, '.')
from load_dd import load_dataset, add_baseline_delta_features, FEATURE_NAMES

DD_DIR = '/Users/gimjaehyeog/졸과/학습자료/doi_10_5061_dryad_5tb2rbp9c__v20230825'
CACHE_PATH = 'dd_features_cache.npz'


def stats_compare(X, y, names):
    from scipy.stats import mannwhitneyu
    print('\n=== 특징별 통계비교 (0=평상시, 1=졸림 임박) ===')
    print('%-12s %10s %10s %12s' % ('feature', 'mean(0)', 'mean(1)', 'p-value'))
    for i, name in enumerate(names):
        a, b = X[y == 0, i], X[y == 1, i]
        try:
            _, p = mannwhitneyu(a, b)
        except ValueError:
            p = float('nan')
        print('%-12s %10.3f %10.3f %12.2e%s'
              % (name, a.mean(), b.mean(), p, ' *' if p < 0.05 else ''))


def train_eval(X, y, G, feature_names, save_model=False):
    """subject-wise CV로 평가. class_weight='balanced'를 쓰므로 accuracy를 다수클래스
    baseline과 비교하는 건 불공정하다(불균형 데이터에서 accuracy는 오해 소지가 큼) ->
    f1(macro)을 '무조건 다수클래스만 찍었을 때의 f1(macro)'와 비교해서 판단한다."""
    from sklearn.svm import SVC
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline
    from sklearn.model_selection import GroupKFold, cross_val_score
    from sklearn.metrics import f1_score

    n_groups = len(np.unique(G))
    n_splits = min(5, n_groups)
    gkf = GroupKFold(n_splits=n_splits)
    naive_pred = np.full_like(y, y.mean() >= 0.5, dtype=int)
    naive_f1 = f1_score(y, naive_pred, average='macro')

    print('\n=== subject-wise %d-fold CV (학습에 안 쓴 사람으로 시험) ===' % n_splits)
    best = None
    for name, clf in [
        ('LogisticRegression', make_pipeline(StandardScaler(),
                                             LogisticRegression(class_weight='balanced', max_iter=1000))),
        ('SVM(rbf)', make_pipeline(StandardScaler(),
                                   SVC(kernel='rbf', class_weight='balanced'))),
    ]:
        acc = cross_val_score(clf, X, y, groups=G, cv=gkf, scoring='accuracy')
        f1 = cross_val_score(clf, X, y, groups=G, cv=gkf, scoring='f1_macro')
        print('%-20s acc=%.3f±%.3f  f1(macro)=%.3f±%.3f' % (name, acc.mean(), acc.std(), f1.mean(), f1.std()))
        if best is None or f1.mean() > best[1]:
            best = (name, f1.mean(), clf)
    print('(무조건 다수클래스만 찍었을 때 f1(macro)=%.3f -- 이보다 높아야 의미 있음)' % naive_f1)

    if save_model and best[1] > naive_f1 + 0.03:
        import joblib
        best[2].fit(X, y)
        joblib.dump({'model': best[2], 'features': feature_names,
                     'window_sec': 20.0, 'trained_on': 'DD-Database(raw ECG, 20s window)',
                     'naive_f1_macro': naive_f1, 'model_f1_macro': best[1]},
                    'drowsy_hr_model.pkl')
        print('\n[저장] drowsy_hr_model.pkl (%s, f1(macro)=%.3f vs naive=%.3f)'
              % (best[0], best[1], naive_f1))
    return best[1], naive_f1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit-sec', type=float, default=None, help='세션당 앞 N초만 사용(빠른 시험)')
    ap.add_argument('--save-model', action='store_true', help='검증 통과 시 모델 저장')
    ap.add_argument('--delta', action='store_true',
                    help='절대값 대신 개인 baseline(평상시 창 중앙값) 대비 변화량 특징 사용')
    ap.add_argument('--std-only', action='store_true',
                    help='검증된 hr_std_delta 단독 특징만 사용(배포용 모델은 이걸로 저장)')
    ap.add_argument('--no-cache', action='store_true', help='캐시 무시하고 ECG 재처리')
    args = ap.parse_args()

    X, y, G, names = load_dataset(
        DD_DIR, limit_sec=args.limit_sec,
        cache_path=None if args.no_cache else CACHE_PATH)
    if len(X) == 0:
        print('데이터가 없습니다.'); return

    if args.delta or args.std_only:
        X, names = add_baseline_delta_features(X, G, y, names)
        print('[특징] 개인 baseline 대비 변화량(delta)으로 변환됨')
    if args.std_only:
        i = names.index('hr_std_delta')
        X, names = X[:, [i]], [names[i]]
        print('[특징] hr_std_delta 단독으로 축소(통계적으로 가장 유의했던 특징)')

    print('\n[데이터] 창 %d개, subjects=%d, dim=%d, 클래스 0/1=%d/%d'
          % (len(X), len(np.unique(G)), X.shape[1], (y == 0).sum(), (y == 1).sum()))

    stats_compare(X, y, names)
    f1, naive_f1 = train_eval(X, y, G, names, save_model=args.save_model)

    print('\n' + '=' * 56)
    if f1 > naive_f1 + 0.03:
        print('[결론] naive baseline f1(macro) 상회 -> 20초 창 HR 특징으로 졸림 임박 판별 가능성 확인')
        print('       학습/배포 스케일이 일치하므로 실시간 코드에 모델 배포 검토 가능')
    else:
        print('[결론] naive baseline 근처 -> 이 특징(HR만)으로는 판별 어려움')
        print('       기존 규칙 기반 로직 유지가 타당')


if __name__ == '__main__':
    main()
