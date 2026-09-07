"""Official IWR6843ISK UART → digital twin. Visualizer 없이 실행."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV_PY = HERE / '.venv' / 'Scripts' / 'python.exe'


def _python() -> Path:
    if VENV_PY.is_file():
        return VENV_PY
    return Path(sys.executable)


def main() -> int:
    py = _python()
    if Path(sys.executable).resolve() != py.resolve():
        return subprocess.call([str(py), str(HERE / 'twin_bridge.py'), *sys.argv[1:]], cwd=str(HERE))
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    from twin_bridge import main as bridge_main
    return bridge_main()


if __name__ == '__main__':
    raise SystemExit(main())
