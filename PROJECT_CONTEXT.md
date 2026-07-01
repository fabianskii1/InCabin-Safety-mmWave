# PROJECT_CONTEXT.md — 인수인계 문서

## 프로젝트 개요
Non-Vision(mmWave 레이더) 기반 차량 내부 위험 감지 시스템 (부산대 졸업과제).
IWR1843BOOST + DCA1000EVM으로 raw ADC를 수집해, (1) 탑승자 인식(point cloud)과
(2) 운전자 상태 모니터링(vital sign: 호흡/심박/HRV, 눈깜빡임/머리동작)을 구현한다.
프라이버시 보호를 위해 영상이 아닌 point cloud/phase 데이터만 사용.

## 하드웨어 / 환경 (검증 완료)
- 레이더: IWR1843BOOST (3TX / 4RX MIMO, 77GHz FMCW)
- 캡처보드: DCA1000EVM (raw ADC를 UDP로 이더넷 스트리밍)
- mmWave Studio 2.1.1.0 (Windows), COM_PORT=5
- PC 이더넷 고정 IP 192.168.33.30 / 255.255.255.0, DCA=192.168.33.180
- data port 4098(UDP), config port 4096(UDP)
- OS: Windows 한 대에서 실시간까지 동작 확인 (Ubuntu/듀얼부팅 불필요)
- Python 3.12 (python.org 설치본, `py` 런처 사용). numpy는 2.x로 동작하게 코드 수정됨.

## 캡처 파라미터 (configuration.py 기준, mmMesh 설정)
NUM_TX=3, NUM_RX=4, ADC_SAMPLES=256, LOOPS_PER_FRAME=128,
START_FREQ=77, FREQ_SLOPE=60.012, SAMPLE_RATE=4400, IDLE_TIME=7, RAMP_END_TIME=65,
NUM_FRAMES=0(무한 스트리밍), periodicity=100ms(=10FPS).
한 frame = 256*128*3*4*2(IQ)*2(byte) = 1,572,864 bytes (약 1.5MB).

## 현재까지 완료된 것 (동작 검증됨)
1. mmWave Studio에서 IWR1843 캡처 성공 (SPI 연결, 펌웨어 로드, FrameConfig 8인자 수정).
   - 중요 이슈 해결: S2(SPI/CAN) 스위치 SPI 위치, FrameConfig 인자 7->8개,
     bitoperations.lua 동봉 필요, lua는 Studio Scripts 폴더에서 Run.
2. 표준 캡처: mmWave Studio가 adc_data_Raw_*.bin(1GB 단위 분할) 저장 확인.
3. pc_generation.py: raw bin -> point cloud (frame당 (128,6)=[x,y,z,V,energy,R]) 생성 성공.
4. 실시간 수신 성공 (Windows 한 대, 포트 충돌 해결):
   - 핵심: mmWave Studio는 레이더 송출만, DCA 스트리밍 시작은 Python이 직접.
   - lua에서 DCA 관련 블록(SelectCaptureDevice/EthInit/Mode/PacketDelay/StartRecord)을
     --[[ ]] 로 전체 주석, ar1.StartFrame()만 남김.
   - dca1000_control.py가 DCA에 config 명령 전송 -> steaming.py가 4098 수신 -> point cloud.
   - 실행: `py realtime_main.py` (RECORD_START OK 뜨고 실시간 point cloud 동작 확인).
   - 참고: READ_FPGA_VERSION status=1154는 FAIL 아님(버전 2.9 값이 status자리에 온 것). 무시 가능.

## 파일 구성 (work 폴더)
- configuration.py       : 캡처 파라미터 상수. 캡처 설정과 반드시 일치해야 함.
- steaming.py            : UDP(4098) 수신 + 프레임 조립 (mmMesh 원본). adcCapThread 클래스.
- capture.py             : steaming을 파일로 저장하는 mmMesh 원본 메인 (py capture.py <분>).
- pc_generation.py       : 프레임 -> point cloud. bin2np_frame/frameReshape/rangeFFT/
                           clutter_removal/dopplerFFT/naive_xyz/frame2pointcloud/reg_data.
                           (원본의 빈frame 버그 수정 + npy 저장 + numpy 호환 수정 반영본)
- dca1000_control.py     : Python이 DCA1000에 직접 명령(SYSTEM_CONNECT/CONFIG_FPGA/
                           CONFIG_PACKET_DELAY/RECORD_START). DCA User Guide(SPRUIJ4A) 5장 기반.
- realtime_main.py       : DCA 제어 + 수신 + point cloud + 실시간 3D 표시 통합.
- visualize_pc.py        : 저장된 npy에서 한 frame 3D 산점도.
- visualize_pc_anim.py   : frame 순서 3D 애니메이션.
- DataCaptureDemo_1843new.lua : mmWave Studio 캡처 스크립트(Studio Scripts 폴더에 위치).

## 신호처리 자료구조 메모
- raw bin은 int16 인터리브. bin2np_frame이 (I,Q) 복원.
- frameReshape: (loops, tx, rx, samples) -> transpose (tx, rx, loops, samples).
- rangeFFT: 마지막 축(samples)에 FFT -> range bin.
- 가상 안테나 12개(3TX*4RX). naive_xyz가 azimuth/elevation FFT로 x,y,z 추정.
- point cloud 경로: rangeFFT -> clutter_removal -> dopplerFFT -> CFAR(EnergyTop128) ->
  naive_xyz -> (x,y,z,V,energy,R).

## 다음 목표 (우선순위)
착수보고서 기준, raw ADC에서 추출해야 할 지표:
- [탑승자 인식/point cloud 경로] point cloud -> DBSCAN 클러스터링 -> 탑승자/영유아 판별.
- [운전자 모니터링/phase 경로, 신규] 흉부 range bin phase -> unwrap ->
  호흡수(RR, 0.1~0.5Hz) / 심박수(HR, 0.8~2Hz) / HRV(RMSSD).
  안면 range bin phase -> 눈깜빡임(PERCLOS, HPF 0.2~3Hz) / 머리동작(Range-Doppler 추적).
  Huber-Kalman 필터로 진동/이상치 억제, R-wave 피크 -> IPI, 3초 윈도우 autocorrelation.

### 착수 단계 (권장 순서)
1) 흉부 phase -> 호흡수(RR) : vital sign 파이프라인의 기반. 위상 추출 검증.
2) 같은 phase에서 심박(HR) 분리 (호흡보다 난이도 높음, harmonic 주의).
3) point cloud + DBSCAN (탑승자 인식).
4) 안면 phase -> 눈깜빡임/머리동작.
5) Huber-Kalman / HRV / 상태분류.

## vital sign 구현 시 핵심 원리(중요)
- 호흡/심박은 point cloud가 아니라 rangeFFT 단계의 phase에서 뽑는다.
- 흐름: raw -> rangeFFT -> 타깃 range bin 선택(에너지 최대 또는 알려진 거리) ->
  np.angle -> np.unwrap -> (선택)np.diff -> 밴드패스 분리 -> FFT 피크 -> ×60 = BPM.
- 캡처가 point cloud용과 다를 수 있음: vital sign은 안정적 frame rate(~20Hz)와
  긴 관측(30초+)이 필요. 현재 10FPS/128loops가 애매하면 캡처 설정 재검토.
- 목표 KPI(보고서): 호흡 RMSE<=2bpm, 생체지표 오차 5% 이내.

## 미해결/주의
- pc_generation.py는 3TX/256/128 캡처 전제. 캡처 설정 다르면 frameReshape에서 size mismatch.
- steaming.py는 패킷 손실 시 exit(0)로 종료. 손실 잦으면 packet delay 25->50 등으로 상향.
- vital sign용 실측 데이터(사람 30초 정지)가 아직 없으면 먼저 캡처 필요.
- READ_FPGA_VERSION 파싱은 버전값을 status로 오인(표시상 FAIL). 기능 영향 없음.
