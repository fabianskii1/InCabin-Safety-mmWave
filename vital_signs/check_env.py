"""Check toolbox, COM ports, visualizer deps, and prebuilt binaries."""

from __future__ import annotations

import sys
from pathlib import Path

from paths import (
    BIN_DIR,
    DEMO_NAME,
    DEVICE_NAME,
    ISK_2M_CFG,
    ISK_BIN,
    LAB,
    LAB_BIN_DIR,
    TOOLBOX,
    UNIFLASH_DSLITE,
    UNIFLASH_LNK,
    VENV_DIR,
    VISUALIZER,
)


def _ok(ok: bool) -> str:
    return "OK" if ok else "MISSING"


def _list_bins() -> list[Path]:
    found: list[Path] = []
    for folder in (BIN_DIR, LAB_BIN_DIR):
        if folder.is_dir():
            found.extend(sorted(folder.glob("*.bin")))
    return found


def _com_ports() -> list[str]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    lines = []
    for p in list_ports.comports():
        lines.append(f"  {p.device}: {p.description}")
    return lines


def main() -> int:
    print(f"Toolbox     {_ok(TOOLBOX.is_dir())}  {TOOLBOX}")
    print(f"Lab         {_ok(LAB.is_dir())}  {LAB}")
    print(f"Visualizer  {_ok((VISUALIZER / 'gui_main.py').is_file())}  {VISUALIZER}")
    print(f"ISK 2m cfg  {_ok(ISK_2M_CFG.is_file())}  {ISK_2M_CFG}")
    print(f"ISK .bin    {_ok(ISK_BIN.is_file())}  {ISK_BIN}")
    print(f"UniFlash    {_ok(UNIFLASH_LNK.is_file() or UNIFLASH_DSLITE.is_file())}  {UNIFLASH_LNK}")
    print(f"Venv        {_ok((VENV_DIR / 'Scripts' / 'python.exe').is_file())}  {VENV_DIR}")
    print()
    print(f"GUI picks: device = {DEVICE_NAME}, config type = {DEMO_NAME}")
    print()

    bins = _list_bins()
    if bins:
        print("Prebuilt binaries:")
        for b in bins:
            print(f"  {b}")
    else:
        print("Prebuilt binaries: MISSING")
        print("  This PC's radar_toolbox_4_00_00_05 install has no prebuilt_binaries/.")
        print("  Download the ISK .bin from TI Resource Explorer and drop it in:")
        print(f"  {BIN_DIR}")

    print()
    print(f"This Python: {sys.version.split()[0]}  {sys.executable}")
    for mod in ("PySide2", "OpenGL", "pyqtgraph", "serial", "numpy"):
        name = "PyOpenGL" if mod == "OpenGL" else mod
        try:
            __import__(mod)
            print(f"  {name}: OK")
        except ImportError:
            print(f"  {name}: MISSING")

    print()
    ports = _com_ports()
    if ports:
        print("COM ports:")
        print("\n".join(ports))
        print("  standalone ISK: CLI = Enhanced COM Port, DATA = Standard COM Port")
        print("  ICBOOST/XDS110: CLI = Application/User UART, DATA = Auxiliary Data Port")
    else:
        print("COM ports: none listed (board unplugged, or pyserial missing)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
