"""
[스캐폴딩 메인] 공개데이터 -> 특징 -> 상태 통계비교 -> SVM 학습·평가.

지금은 drivedb(스트레스, 리허설)로 '파이프라인이 도는지' 확인한다.
UL-DD 승인되면: load_dataset()을 UL-DD 로더로 교체 + 라벨을 alert/drowsy로.
그 외 코드(특징·비교·학습)는 그대로 재사용된다.

실행:
  py train_drowsiness.py                # drivedb 전체(느리면 --records로 일부)
  py train_drowsiness.py --records drive05 drive06 drive07 drive08
"""
import argparse
import sys

import numpy as np

# 같은 폴더 모듈
sys.path.insert(0, '.')
from features import (window_features, normalize_per_subject,
                      normalize_to_baseline, FEATURE_NAMES)


def per_record_features(t, rr, hr, lab, grp):
    """레코드(그룹)별로 윈도우 특징 추출 후, 윈도우 다수결로 라벨 부여."""
    X, y, G = [], [], []
    for gi in np.unique(grp):
        m = grp == gi
        tt, rrr, hhr, ll = t[m], rr[m], hr[m], lab[m]
        Xg, centers = window_features(tt, rrr, hhr, win_sec=60.0, hop_sec=20.0)
        if len(Xg) == 0:
            continue
        # 각 윈도우 중심 시각의 라벨을 최근접으로 부여
        for row, c in zip(Xg, centers):
            idx = np.argmin(np.abs(tt - c))
            if ll[idx] < 0:            # 경계/제외 구간
                continue
            X.append(row); y.append(ll[idx]); G.append(gi)
    return np.array(X), np.array(y), np.array(G)


def stats_compare(X, y, names=('0', '1')):
    """상태 0 vs 1 특징별 통계비교 (Mann-Whitney U)."""
    from scipy.stats import mannwhitneyu
    print('\n=== 상태별 특징 비교 (0=%s, 1=%s) ===' % names)
    print('%-9s %10s %10s %12s' % ('feature', 'mean(0)', 'mean(1)', 'p-value'))
    for i, name in enumerate(FEATURE_NAMES):
        a, b = X[y == 0, i], X[y == 1, i]
        if len(a) < 3 or len(b) < 3:
            continue
        try:
            _, p = mannwhitneyu(a, b)
        except ValueError:
            p = float('nan')
        star = ' *' if p < 0.05 else ''
        print('%-9s %10.3f %10.3f %12.2e%s' % (name, a.mean(), b.mean(), p, star))
    print('(* = p<0.05, 두 상태가 유의하게 갈리는 특징)')


def train_eval(X, y, G):
    """subject 단위 GroupKFold로 SVM 교차검증 (subject 누수 방지)."""
    from sklearn.svm import SVC
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline
    from sklearn.model_selection import GroupKFold, cross_val_score

    n_groups = len(np.unique(G))
    n_splits = min(5, n_groups)
    if n_splits < 2:
        print('\n[학습] subject가 부족해 교차검증 생략 (레코드 더 필요).')
        return
    # class_weight='balanced': 불균형이어도 소수클래스를 무시하지 않게
    clf = make_pipeline(StandardScaler(),
                        SVC(kernel='rbf', C=1.0, class_weight='balanced'))
    gkf = GroupKFold(n_splits=n_splits)
    acc = cross_val_score(clf, X, y, groups=G, cv=gkf, scoring='accuracy')
    f1 = cross_val_score(clf, X, y, groups=G, cv=gkf, scoring='f1_macro')
    print('\n=== SVM (subject-wise %d-fold CV, balanced) ===' % n_splits)
    print('accuracy : %.3f ± %.3f' % (acc.mean(), acc.std()))
    print('f1(macro): %.3f ± %.3f' % (f1.mean(), f1.std()))
    base = max(np.mean(y == 0), np.mean(y == 1))
    print('(다수클래스 baseline=%.3f — 이보다 높아야 의미 있음)' % base)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', choices=['drivedb', 'drozy'], default='drivedb',
                    help='drivedb=스트레스 리허설 / drozy=진짜 졸음 라벨')
    ap.add_argument('--records', nargs='*', default=None, help='drivedb 일부 (예: drive05)')
    ap.add_argument('--local-dir', default=None, help='drivedb 캐시 폴더')
    ap.add_argument('--drozy-dir', default=None, help='DROZY 폴더 경로')
    ap.add_argument('--subjects', type=int, nargs='*', default=None, help='drozy subject 일부')
    ap.add_argument('--per-subject-norm', action='store_true',
                    help='subject별 전체 z-score (주의: 세션단위 라벨엔 부적합)')
    ap.add_argument('--baseline-norm', action='store_true',
                    help='각 subject의 각성 세션을 기준선으로 정규화(실차 보정 방식) [권장]')
    args = ap.parse_args()

    if args.dataset == 'drozy':
        from load_drozy import load_dataset
        if not args.drozy_dir:
            print('drozy는 --drozy-dir <DROZY폴더> 가 필요합니다.'); return
        t, rr, hr, lab, grp = load_dataset(args.drozy_dir, subjects=args.subjects)
        label_names = ('각성', '졸림')
    else:
        from load_drivedb import load_dataset
        t, rr, hr, lab, grp = load_dataset(args.records, local_dir=args.local_dir)
        label_names = ('rest', 'active')
    X, y, G = per_record_features(t, rr, hr, lab, grp)
    print('\n[특징] windows=%d, subjects=%d, dim=%d' %
          (len(X), len(np.unique(G)), X.shape[1] if len(X) else 0))
    if len(X) < 10:
        print('특징 윈도우가 너무 적습니다. --records를 더 넣으세요.')
        return
    if args.baseline_norm:
        X = normalize_to_baseline(X, y, G, baseline_label=0)
        print('[특징] 각성 기준선(baseline) 정규화 적용됨 — 실차 보정 방식')
    elif args.per_subject_norm:
        X = normalize_per_subject(X, G)
        print('[특징] per-subject z-score 적용됨')

    stats_compare(X, y, names=label_names)
    train_eval(X, y, G)
    if args.dataset == 'drozy':
        print('\n[안내] DROZY 진짜 졸음(KSS) 학습 결과입니다.')
    else:
        print('\n[안내] drivedb(스트레스) 리허설입니다. --dataset drozy 로 진짜 졸음 학습.')


if __name__ == '__main__':
    main()
