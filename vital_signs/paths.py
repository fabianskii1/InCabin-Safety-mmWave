"""Local TI Radar Toolbox paths for the official Vital Signs with People Tracking demo."""

from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent

TOOLBOX = Path(r"C:\ti\radar_toolbox_4_00_00_05")
UNIFLASH = Path(r"C:\ti\uniflash_9.4.1")
SDK = Path(r"C:\ti\mmwave_sdk_03_06_02_00-LTS")

LAB = (
    TOOLBOX
    / "source"
    / "ti"
    / "examples"
    / "Industrial_and_Personal_Electronics"
    / "Vital_Signs"
    / "Vital_Signs_With_People_Tracking"
)
VISUALIZER = (
    TOOLBOX
    / "tools"
    / "visualizers"
    / "Applications_Visualizer"
    / "Industrial_Visualizer"
)
UNIFLASH_LNK = UNIFLASH / "CCStudio UniFlash.lnk"
UNIFLASH_DSLITE = UNIFLASH / "dslite.bat"
CFG_DIR = HERE / "chirp_configs"
BIN_DIR = HERE / "prebuilt_binaries"
VENV_DIR = HERE / ".venv"
LAB_BIN_DIR = LAB / "prebuilt_binaries"
ISK_BIN = LAB_BIN_DIR / "vital_signs_tracking_6843ISK_demo.bin"
AOP_BIN = LAB_BIN_DIR / "vital_signs_tracking_6843AOP_demo.bin"

ISK_2M_CFG = CFG_DIR / "vital_signs_ISK_2m.cfg"
ISK_REARVIEW_CFG = CFG_DIR / "vital_signs_ISK_rearview.cfg"
ISK_6M_CFG = CFG_DIR / "vital_signs_ISK_6m.cfg"
AOP_2M_CFG = CFG_DIR / "vital_signs_AOP_2m.cfg"
AOP_6M_CFG = CFG_DIR / "vital_signs_AOP_6m.cfg"

DEMO_NAME = "Vital Signs with People Tracking"
DEVICE_NAME = "xWR6843"
USER_GUIDE = LAB / "docs" / "vital_signs_with_people_tracking_user_guide.html"
SOP_GUIDE = TOOLBOX / "hardware_docs" / "evm_setup_operational_modes.html"
FLASH_GUIDE = TOOLBOX / "software_docs" / "using_uniflash_with_mmwave.html"
