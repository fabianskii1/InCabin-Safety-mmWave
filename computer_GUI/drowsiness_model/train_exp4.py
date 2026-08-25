"""
Exp4(수면박탈, 63명) 데이터로 '레이더로 가능한 특징(HR/RR 변화량)'만 써서
졸림 증가(KSS 상승) 여부를 판별할 수 있는지 검증.

DROZY(14명)에서 실패했던 것과 같은 방법론(변화량 특징 + subject-wise CV)을
사람 수만 늘려서(63명) 재시도. 원리: 절대값이 아니라 '그 사람의 baseline
대비 변화량(_Dr-Bl)'을 쓰므로 개인별 기저치 차이는 이미 제거된 입력이다.

실행: python train_exp4.py [--rich]
  --rich : HRV_RMSSD 등 레이더로는 불가능한 정밀 HRV까지 포함해 상한선 비교
"""
import argparse
import sys

import numpy as np

sys.path.insert(0, '.')
from load_exp4 import load_dataset, load_trend_dataset

EXP4_DIR = '/Users/gimjaehyeog/졸과/학습자료/Exp4_extracted/Exp4'


def stats_compare(X, y, names):
    from scipy.stats import mannwhitneyu
    print('\n=== 특징별 통계비교 (0=졸림 비증가, 1=졸림 증가) ===')
    print('%-28s %10s %10s %12s' % ('feature(변화량)', 'mean(0)', 'mean(1)', 'p-value'))
    for i, name in enumerate(names):
        a, b = X[y == 0, i], X[y == 1, i]
        try:
            _, p = mannwhitneyu(a, b)
        except ValueError:
            p = float('nan')
        star = ' *' if p < 0.05 else ''
        print('%-28s %10.3f %10.3f %12.2e%s' % (name, a.mean(), b.mean(), p, star))


def train_eval(X, y, G):
    from sklearn.svm import SVC
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline
    from sklearn.model_selection import GroupKFold, cross_val_score

    n_groups = len(np.unique(G))
    n_splits = min(5, n_groups)
    clf = make_pipeline(StandardScaler(), SVC(kernel='rbf', class_weight='balanced'))
    gkf = GroupKFold(n_splits=n_splits)
    acc = cross_val_score(clf, X, y, groups=G, cv=gkf, scoring='accuracy')
    f1 = cross_val_score(clf, X, y, groups=G, cv=gkf, scoring='f1_macro')
    base = max(np.mean(y == 0), np.mean(y == 1))
    print('\n=== SVM (subject-wise %d-fold CV, balanced) ===' % n_splits)
    print('accuracy : %.3f ± %.3f   (baseline=%.3f)' % (acc.mean(), acc.std(), base))
    print('f1(macro): %.3f ± %.3f' % (f1.mean(), f1.std()))
    return acc.mean(), base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rich', action='store_true', help='HRV 정밀지표 포함(레이더 불가, 상한선 비교용)')
    ap.add_argument('--trend', action='store_true',
                    help='30분 평균 대신 3분 창 시계열의 기울기(추세)를 특징으로 사용')
    args = ap.parse_args()

    fset = 'rich' if args.rich else 'radar'
    print('[설정] feature_set=%s, trend=%s' % (fset, args.trend))
    loader = load_trend_dataset if args.trend else load_dataset
    X, y, G, names, deltas = loader(EXP4_DIR, feature_set=fset)
    print('[데이터] n=%d, subjects=%d, dim=%d, 클래스분포 0/1=%d/%d'
          % (len(X), len(np.unique(G)), X.shape[1], (y == 0).sum(), (y == 1).sum()))
    print('[deltaKSS] min=%d max=%d mean=%.2f' % (deltas.min(), deltas.max(), deltas.mean()))

    stats_compare(X, y, names)
    acc, base = train_eval(X, y, G)

    print('\n' + '=' * 50)
    if acc > base + 0.03:
        print('[결론] baseline을 유의하게 상회 -> HR/RR 변화량만으로 졸림 증가 판별 가능성 있음')
    else:
        print('[결론] baseline 근처 -> 이 특징셋/샘플수로는 아직 신뢰할 만한 판별 어려움')


if __name__ == '__main__':
    main()
