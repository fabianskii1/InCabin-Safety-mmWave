"""Flash official TI Vital Signs with People Tracking binary onto IWR6843ISK.

Standalone ISK uses Silicon Labs CP2105. UniFlash's UART break is not wired
to nRESET here; it just fills RX with NUL bytes, which UniFlash then treats
as a failed ACK. Wait for the ROM bootloader after a real NRST instead.
"""

from __future__ import annotations

import struct
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import serial

from paths import ISK_BIN, UNIFLASH

GEN1 = (
    UNIFLASH
    / "deskdb"
    / "content"
    / "TICloudAgent"
    / "win"
    / "ccs_base"
    / "mmWave"
    / "gen1"
)
sys.path.insert(0, str(GEN1))

import mmWaveProgFlash as mmw  # noqa: E402

CLI_PORT_DEFAULT = "COM4"
ACK = mmw.AWR_BOOTLDR_OPCODE_ACK
NACK = mmw.AWR_BOOTLDR_OPCODE_NACK


def _send_opcode(ser: serial.Serial, opcode: bytes) -> None:
    checksum = sum(opcode) & 0xFF
    ser.write(mmw.AWR_BOOTLDR_SYNC_PATTERN)
    ser.write(struct.pack(">H", len(opcode) + 2))
    ser.write(struct.pack("B", checksum))
    ser.write(opcode)
    ser.flush()


def wait_bootloader(com_port: str, timeout_s: float = 8.0) -> bool:
    """Ping the ROM bootloader. After power-up the hello ACK is easy to miss."""
    try:
        ser = serial.Serial(port=com_port, baudrate=115200, timeout=0.3)
    except serial.SerialException as exc:
        print(f"  COM {com_port} not ready ({exc})")
        return False
    try:
        ser.break_condition = False
        time.sleep(0.05)
        ser.reset_input_buffer()
        deadline = time.time() + timeout_s
        last_ping = 0.0
        buf = b""
        while time.time() < deadline:
            now = time.time()
            if now - last_ping >= 1.5:
                print(f"  COM {com_port} PING")
                _send_opcode(ser, mmw.AWR_BOOTLDR_OPCODE_PING)
                last_ping = now
            try:
                chunk = ser.read(32)
            except serial.SerialException as exc:
                print(f"  COM dropped ({exc})")
                return False
            if not chunk:
                continue
            buf += chunk
            stripped = buf.lstrip(b"\x00\xff")
            if not stripped:
                buf = b""
                continue
            print(f"  RX {stripped[:24].hex()}  {stripped[:24]!r}")
            if ACK in stripped:
                print("  bootloader ACK")
                return True
            if NACK in stripped:
                print("  bootloader NACK")
                return False
            if len(buf) > 128:
                buf = buf[-32:]
        print("  no ACK after PING")
        return False
    finally:
        try:
            ser.close()
        except Exception:
            pass


def flash(bin_path: Path, com_port: str) -> int:
    if not bin_path.is_file():
        print(f"Binary not found: {bin_path}")
        return 1

    print(f"Flashing {bin_path.name} over {com_port}")
    print("Need flashing SOP: SOP0 ON, SOP1 empty, SOP2 ON.")
    print("Then press NRST (or unplug/replug 5V) while this script waits.")
    attempt = 0
    while True:
        attempt += 1
        print(f"[{attempt}] waiting for bootloader")
        if wait_bootloader(com_port):
            break
        print("  retry in 2s")
        time.sleep(2)

    ldr = mmw.BootLdr("", com_port, trace_level=0)
    ldr.setPartNum("IWR6843")
    if not ldr.determinePGVersion():
        print("Could not read device version. Power cycle in flash mode and retry.")
        ldr.disconnect()
        return 3

    images = [SimpleNamespace(path=str(bin_path), order=1)]
    files_list = ldr.copyImagesList(images)
    file_size_sum = 0
    for item in files_list:
        if not ldr.checkFileHeader(item.path, item):
            print("Binary header is not a valid IWR6843 meta image.")
            ldr.disconnect()
            return 4
        file_size_sum += item.fileSize

    ldr.calcProgressValues(files_list, file_size_sum, True)
    print("Erasing SFLASH...")
    ldr.erase_storage()
    for item in files_list:
        prog = ldr.getImageProgCntList(item)
        ok = ldr.download_file(item.path, item.file_id, 0, 0, "SFLASH", prog)
        if not ok:
            print("Download failed.")
            ldr.disconnect()
            return 5
        print(f"Downloaded {item.file_id}")

    ldr.disconnect()
    print("Flash OK. Set SOP2 OFF (functional), press NRST, then run_visualizer.py")
    return 0


if __name__ == "__main__":
    com = sys.argv[1] if len(sys.argv) > 1 else CLI_PORT_DEFAULT
    raise SystemExit(flash(ISK_BIN, com))
