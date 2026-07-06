# 실시간 수신 (Windows 한 대, mmMesh)

mmWave Studio가 포트(4098)를 점유하면 Python이 못 받으므로,
**lua는 레이더 송출만 시키고, DCA 스트리밍 시작은 Python이 직접** 한다.

## 파일

- `dca1000_control.py` : Python이 DCA1000에 직접 config/record 명령 전송 (포트 4096)
- `realtime_main.py`   : DCA 제어 + UDP 수신(steaming) + point cloud + 실시간 3D 표시
- `steaming.py`        : UDP 수신·프레임 조립 (mmMesh 원본)
- `pc_generation.py`   : 프레임 -> point cloud
- `configuration.py`   : 파라미터

## 준비 — lua 수정 (중요)

`DataCaptureDemo_1843new.lua`에서 **DCA 관련 줄을 모두 주석 처리**한다.
레이더 설정과 StartFrame만 남기고, DCA는 Python이 제어하게 둔다:

```lua
-- ar1.SelectCaptureDevice("DCA1000")
-- ar1.CaptureCardConfig_EthInit(...)
-- ar1.CaptureCardConfig_Mode(...)
-- ar1.CaptureCardConfig_PacketDelay(25)
-- ar1.CaptureCardConfig_StartRecord(...)
```

단, `ar1.StartFrame()` 은 **반드시 살린다** (레이더가 chirp를 쏴야 함).

## 실행 순서

```
1. PC 이더넷 고정 IP 192.168.33.30 / 255.255.255.0 확인
2. mmWave Studio에서 (수정한) lua Run
   -> 레이더가 무한 chirp 송출 시작 (DCA는 아직 스트리밍 X)
3. py realtime_main.py
   -> Python이 DCA에 스트리밍 시작 명령 -> 4098에서 수신 -> 실시간 3D 창
```

종료는 Ctrl+C.

## 옵션

```
py realtime_main.py --no-plot          # 시각화 없이 콘솔 로그만 (가볍게 테스트)
py realtime_main.py --no-dca-control   # Python이 DCA 제어 안 함 (lua가 DCA 설정한 경우)
```

## 단계별 디버깅 권장

한 번에 안 되면 작은 것부터:

1. **DCA 통신만 확인**
   ```
   py dca1000_control.py
   ```
   -> READ_FPGA_VERSION 에 status=0(OK)이 오면 Python<->DCA 통신 정상.
   TIMEOUT 이면 IP/포트/방화벽 또는 mmWave Studio가 포트 점유 중.

2. **수신만 확인** (lua가 DCA까지 설정하게 두고)
   ```
   py realtime_main.py --no-dca-control --no-plot
   ```
   -> "frame N, points=..." 가 찍히면 수신 정상.

3. **전체**
   ```
   py realtime_main.py
   ```

## 자주 나는 문제

- `dca1000_control` TIMEOUT : mmWave Studio가 아직 4096/4098 점유 중.
  lua에서 DCA 줄을 주석 처리했는지 확인. Studio 재시작.
- 수신 timeout (steaming) : 레이더가 송출 안 함(lua StartFrame 확인) 또는
  DCA 스트리밍 시작 명령 실패.
- "Packet Lost" 후 종료 : 패킷 손실. mmWave Studio packet delay를 늘리거나
  (이 경로에선 dca1000_control.config_packet_delay 값을 25->50 등으로),
  PC 부하를 줄이고 다른 네트워크 어댑터를 끈다.
- 좌표가 이상 : configuration.py가 실제 캡처 설정(256/128/3TX)과 맞는지 확인.

## 참고

DCA 명령 포맷은 DCA1000 User's Guide(SPRUIJ4A) 5장 기준이며,
config_fpga / config_packet_delay 의 인자는 mmWave Studio의
CaptureCardConfig_Mode(1,2,1,2,3,30) / PacketDelay(25) 에 대응하도록 맞춤.
DCA 펌웨어 버전에 따라 데이터 필드가 미묘하게 다를 수 있어, 1번 단계
(dca1000_control.py 단독 실행)로 통신부터 검증하는 것을 권장.
```
