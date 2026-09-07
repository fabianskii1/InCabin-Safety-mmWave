"""Industrial Visualizer with patched VitalSigns status labels."""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
from paths import VISUALIZER

COMMON = VISUALIZER.parent / "common"
PATCH = HERE / "visualizer_patch" / "vital_signs.py"


def _inject_patched_vitals() -> None:
    import Demo_Classes

    spec = importlib.util.spec_from_file_location("Demo_Classes.vital_signs", PATCH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["Demo_Classes.vital_signs"] = mod
    Demo_Classes.vital_signs = mod
    spec.loader.exec_module(mod)


def main() -> int:
    if not (VISUALIZER / "gui_main.py").is_file():
        print(f"Visualizer not found: {VISUALIZER / 'gui_main.py'}")
        return 1
    if not PATCH.is_file():
        print(f"Patch not found: {PATCH}")
        return 1

    os.chdir(VISUALIZER)
    sys.path.insert(1, str(COMMON))
    sys.path.insert(1, "../common")

    import Demo_Classes.people_tracking  # noqa: F401 — load TI Demo_Classes package

    _inject_patched_vitals()

    from PySide2.QtCore import Qt
    from PySide2.QtWidgets import QApplication
    from PySide2.QtGui import QPalette, QColor
    from gui_core import Window, Core
    from demo_defines import DEVICE_DEMO_DICT, BUSINESS_DEMOS

    # Korean Windows defaults to cp949; rearview cfg comments were UTF-8.
    _orig_parse_cfg = Core.parseCfg

    def _parse_cfg_utf8(self, fname):
        import builtins

        real_open = builtins.open

        def cfg_open(file, mode="r", *args, **kwargs):
            if file == fname and isinstance(mode, str) and "b" not in mode:
                kwargs.setdefault("encoding", "utf-8")
            return real_open(file, mode, *args, **kwargs)

        builtins.open = cfg_open
        try:
            return _orig_parse_cfg(self, fname)
        finally:
            builtins.open = real_open

    Core.parseCfg = _parse_cfg_utf8

    logging.basicConfig(
        format="%(levelname)-8s [%(filename)s:%(lineno)d] %(message)s",
        level=logging.INFO,
    )

    for key in DEVICE_DEMO_DICT.keys():
        DEVICE_DEMO_DICT[key]["demos"] = [
            x for x in DEVICE_DEMO_DICT[key]["demos"] if x in BUSINESS_DEMOS["Industrial"]
        ]

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    app = QApplication(sys.argv)

    if len(sys.argv) >= 2 and sys.argv[1] == "dark":
        app.setStyle("Fusion")
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(53, 53, 53))
        palette.setColor(QPalette.WindowText, Qt.white)
        palette.setColor(QPalette.Base, QColor(25, 25, 25))
        palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
        palette.setColor(QPalette.ToolTipBase, Qt.black)
        palette.setColor(QPalette.ToolTipText, Qt.white)
        palette.setColor(QPalette.Text, Qt.white)
        palette.setColor(QPalette.Button, QColor(53, 53, 53))
        palette.setColor(QPalette.ButtonText, Qt.white)
        palette.setColor(QPalette.BrightText, Qt.red)
        palette.setColor(QPalette.Link, QColor(42, 130, 218))
        palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
        palette.setColor(QPalette.HighlightedText, Qt.black)
        app.setPalette(palette)

    screen = app.primaryScreen()
    size = screen.size()
    main = Window(size=size, title="Industrial Visualizer")
    main.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
