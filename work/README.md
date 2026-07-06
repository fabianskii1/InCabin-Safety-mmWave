# mmMesh Point Cloud 생성 + 시각화 작업 폴더

IWR1843BOOST + DCA1000EVM으로 캡처한 raw ADC bin(`adc_data.bin`)을
mmMesh 방식으로 point cloud로 변환하고 3D로 시각화하는 코드 모음.

## 파일 구성

- `configuration.py`      : 캡처 파라미터 (3TX / 256샘플 / 128 loops 등). **캡처 설정과 반드시 일치해야 함**
- `pc_generation.py`      : bin -> point cloud 변환. 결과를 `pointcloud_output.npy`로 저장
- `visualize_pc.py`       : 저장된 npy에서 frame 1개를 3D 산점도로 보기
- `visualize_pc_anim.py`  : frame 순서대로 3D 애니메이션 재생 (gif 저장 가능)
- `requirements.txt`      : 필요 패키지

## 설치

```bash
pip install -r requirements.txt
```

> numpy는 1.24 미만을 권장(원본 코드가 구버전 별칭 사용). 코드 자체는
> `np.complex128`로 고쳐두어 최신 numpy에서도 대체로 동작하지만, 안전하게
> `numpy<1.24`를 권장.

## 사용 순서

### 1) point cloud 생성

```bash
python pc_generation.py adc_data.bin 10
```

- 두 번째 인자(10)는 처리할 frame 수.
- bin에 들어 있는 frame 수보다 크면 자동으로 줄여서 처리.
- 한 frame 크기 = 256(samples) x 128(loops) x 3(TX) x 4(RX) x 2(I,Q) x 2(16bit)
  = 1,572,864 bytes (약 1.5 MB).
- 정상 출력 예:
  ```
  Frame 0: (128, 6)
  Frame 1: (128, 6)
  ...
  Saved pointcloud_output.npy with shape (10, 128, 6)
  ```
- `(128, 6)`의 6 = [x, y, z, V(속도), energy(SNR), R(거리)]

### 2) 한 frame 보기

```bash
python visualize_pc.py pointcloud_output.npy 0          # frame 0, 색=속도
python visualize_pc.py pointcloud_output.npy 0 e        # 색=energy
python visualize_pc.py pointcloud_output.npy 5 r        # frame 5, 색=range
```

### 3) 애니메이션

```bash
python visualize_pc_anim.py pointcloud_output.npy
python visualize_pc_anim.py pointcloud_output.npy --save pointcloud.gif
```

## 자주 나는 문제

- **reshape size mismatch** : 캡처 설정이 configuration.py(3TX/256/128)와 다름.
  -> mmMesh 캡처 설정대로 다시 캡처하거나, configuration.py를 캡처값에 맞게 수정.
- **np.complex AttributeError** : 최신 numpy. `numpy<1.24` 설치 권장.
- **점이 너무 적거나 없음** : configuration.py가 아니라 pc_generation.py의
  RangeCut 범위(`:25`, `125:`)나 EnergyTop128 임계값 조정.
- **frames 값을 모름** : 일단 작은 값(10)으로 실행. 코드가 파일 크기로
  최대 frame 수를 자동 점검해 줄여 줌.
