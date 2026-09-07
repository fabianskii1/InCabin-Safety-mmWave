# official_ti_vs

TI 공식 **Vital Signs with People Tracking** 데모를 이 PC에서 그대로 보기 위한 폴더입니다.
우리 occupancy 펌웨어·`driver_vitals_sep` 과는 UART/트래커/cfg 가 다릅니다. 소스 없는 바이너리 + Industrial Visualizer 조합입니다.

## 한 줄 요약

1. 공식 `.bin` 을 플래시한다 (지금 occupancy 펌웨어는 덮어씀).
2. SOP 를 functional 로 되돌리고 보드를 리셋한다.
3. Industrial Visualizer 에서 device `xWR6843`, config type `Vital Signs with People Tracking` 을 고른다.
4. ISK 근거리면 `chirp_configs/vital_signs_ISK_2m.cfg` 를 보낸다.
5. 앉아 가슴을 센서 쪽으로, **20초 이상 정지**. 트랙이 있어야 바이탈이 나온다.

공식 펌웨어를 유지한 채 Visualizer 없이 트윈만 보려면:

```text
cd computer_GUI\official_ti_vs
python run_twin.py
```

기본 COM4(CLI) / COM5(DATA), cfg 는 `chirp_configs/vital_signs_ISK_rearview.cfg`.
Visualizer 와 occupancy GUI 는 끄세요. COM 이 겹칩니다.
브라우저는 [http://127.0.0.1:8766](http://127.0.0.1:8766). 보드 NRST 후 스크립트가 cfg 를 보냅니다.

Visualizer GUI 가 필요하면:

```text
cd computer_GUI\official_ti_vs
python check_env.py
python run_visualizer.py
```

## 이 폴더

| 경로 | 내용 |
|------|------|
| `chirp_configs/` | toolbox 에서 복사한 공식 cfg (ISK/AOP, 2m/6m) |
| `prebuilt_binaries/` | 공식 `.bin` 을 여기에 둠. git 에 넣지 않음 |
| `check_env.py` | toolbox / COM / PySide2 / `.bin` 확인 |
| `run_visualizer.py` | TI Industrial Visualizer 실행 |
| `setup_visualizer.ps1` | PySide2 용 Python 3.9/3.10 venv |

로컬 가이드:

- `C:\ti\radar_toolbox_4_00_00_05\source\ti\examples\Industrial_and_Personal_Electronics\Vital_Signs\Vital_Signs_With_People_Tracking\docs\vital_signs_with_people_tracking_user_guide.html`
- Visualizer: `C:\ti\radar_toolbox_4_00_00_05\tools\visualizers\Applications_Visualizer\Industrial_Visualizer\gui_main.py`

## 1. 공식 바이너리

ISK 바이너리는 toolbox 에 있습니다.

`C:\ti\radar_toolbox_4_00_00_05\source\ti\examples\Industrial_and_Personal_Electronics\Vital_Signs\Vital_Signs_With_People_Tracking\prebuilt_binaries\vital_signs_tracking_6843ISK_demo.bin`

3D People Tracking 의 `3D_people_track_68xx_demo.bin` 은 **다른 데모**입니다. 쓰지 마세요.

## 2. 플래시 (UniFlash GUI)

이미 공식 `.bin` 이 올라가 있으면 이 단계는 건너뜁니다.
다시 넣을 때는 UniFlash에서 Device `IWR6843`, COM4 Enhanced, Meta Image 1에
`prebuilt_binaries\vital_signs_tracking_6843ISK_demo.bin` 을 올립니다.

## 3. Visualizer (PySide2)

TI GUI 는 **PySide2** 입니다. 지금 기본 Python 은 **3.13** 이라 PySide2 가 설치되지 않습니다.
**Python 3.9 또는 3.10** 을 설치한 뒤:

```powershell
cd computer_GUI\official_ti_vs
.\setup_visualizer.ps1
python run_visualizer.py
```

GUI 에서:

1. Device = `xWR6843`
2. Config type = `Vital Signs with People Tracking`
3. CLI COM = COM4 (Enhanced), DATA COM = COM5 (Standard)
4. cfg: 앉은 사람·근거리 → `chirp_configs/vital_signs_ISK_2m.cfg`
5. Send. cfg 를 다시 보내려면 보드를 리셋합니다.

트랙이 없으면 바이탈은 숨깁니다. 호흡 deviation < 0.02 이면 HOLD.
가만히 **20초**, range bin 이 안정되면 HR/RR 이 맞습니다.

ISK 2m cfg 의 `sensorPosition 2 0 15` 는 높이 2 m, 하향 15°. 실제 장착 높이에 맞춰 첫 숫자를 바꾸세요. TI 권장은 높이 1–1.5 m, 틸트 0–15°.

## occupancy GUI 와 같이 쓰지 말 것

공식 바이너리는 GTRACK + `vitalsign` / `VSRangeIdxCfg` 입니다.
`vod_vs_rearview_mirror.cfg` 와 `driver_vitals_sep` 는 occupancy 펌웨어 전용입니다.
공식 `.bin` 이 올라간 보드에 occupancy cfg 를 보내면 동작하지 않습니다.
