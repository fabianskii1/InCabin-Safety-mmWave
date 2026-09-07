"""Launch TI Industrial Visualizer for Vital Signs with People Tracking.

The official GUI does not take a demo name on the command line. After it
opens, pick device xWR6843 and config type 'Vital Signs with People Tracking'.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from paths import DEMO_NAME, DEVICE_NAME, ISK_REARVIEW_CFG, VENV_DIR, VISUALIZER


def _visualizer_python() -> Path:
    venv_py = VENV_DIR / "Scripts" / "python.exe"
    if venv_py.is_file():
        return venv_py
    return Path(sys.executable)


def main() -> int:
    gui = Path(__file__).resolve().parent / "gui_launch.py"
    if not gui.is_file():
        print(f"Launcher not found: {gui}")
        return 1
    if not (VISUALIZER / "gui_main.py").is_file():
        print(f"Visualizer not found: {VISUALIZER / 'gui_main.py'}")
        return 1

    py = _visualizer_python()
    extra = sys.argv[1:]
    print("Official TI Industrial Visualizer (status: Breathing / Hold / Warning / Motion)")
    print(f"  python : {py}")
    print(f"  device : {DEVICE_NAME}")
    print(f"  demo   : {DEMO_NAME}")
    print(f"  cfg    : {ISK_REARVIEW_CFG}  (ISK rearview mirror)")
    print()
    print("In the GUI:")
    print(f"  1. Device = {DEVICE_NAME}")
    print(f"  2. Config type = {DEMO_NAME}")
    print("  3. CLI COM = Enhanced COM Port (standalone) or Application/User UART (XDS110)")
    print("  4. DATA COM = Standard COM Port (standalone) or Auxiliary Data Port (XDS110)")
    print("  5. Load the cfg above, then Send.")
    print("  6. Sit still ~20 s. Tracks must exist before vitals are shown.")
    print()
    print("If PySide2 import fails, this Python is too new. Use setup_visualizer.ps1")
    print("with Python 3.9 or 3.10, then run this script again.")
    print()

    env = os.environ.copy()
    return subprocess.call([str(py), str(gui), *extra], cwd=str(gui.parent), env=env)


if __name__ == "__main__":
    raise SystemExit(main())
