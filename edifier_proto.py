"""HECATE G5 MAX wireless (35BB:A00B) read-only status query.

Observed against installed HECATE 1.1.42 and a real G5 MAX receiver.
Wireless request: D0 35 BB 01 00 (Report ID D0, padded to 64 bytes).
Wired request:    E0 35 BB 01 00 (Report ID E0).
Reply: ID 35 BB <length> 00 <online> <percent> <charging> <fw major/minor/patch>.
Only command 00 is sent. No EQ, lighting, pairing or firmware commands.
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Optional
import hid_win as hw

VID, PID = 0x35BB, 0xA00B
STATUS_QUERY = bytes.fromhex("D0 35 BB 01 00").ljust(64, b"\0")

@dataclass(frozen=True)
class HeadsetStatus:
    level: Optional[int]
    online: bool
    charging: bool
    firmware: str = ""
    raw: bytes = b""

def parse_status(report: bytes) -> Optional[HeadsetStatus]:
    if len(report) < 11 or report[0] not in (0xD0, 0xE0) or report[1:3] != bytes.fromhex("35 BB"):
        return None
    size = report[3]
    if size < 7 or len(report) < 4 + size or report[4] != 0:
        return None
    if report[5] not in (0, 1) or report[7] not in (0, 1):
        return None
    online = report[5] == 1
    if online and report[6] > 100:
        return None
    return HeadsetStatus(report[6] if online else None, online,
                         report[7] == 1, ".".join(map(str, report[8:11])), report)

def find_interface(interfaces=None):
    interfaces = hw.enumerate_hid() if interfaces is None else interfaces
    for pid, collection in [(PID, "mi_03&col03"), (0xA211, "mi_03&col02")]:
        iface = next((d for d in interfaces if (d.vid, d.pid) == (VID, pid)
                      and collection in d.path.lower()
                      and d.usage_page == 0xFF02 and d.input_len == 64
                      and d.output_len == 64), None)
        if iface:
            return iface
    return None

def read_status(iface=None, timeout: float = 0.8) -> HeadsetStatus:
    iface = iface or find_interface()
    if iface is None:
        raise hw.HidError("未找到 G5 MAX 无线接收器")
    with hw.HidDevice(iface.path, write=True) as dev:
        dev.drain()  # Opens the independent overlapped reader before writing.
        query = STATUS_QUERY if iface.pid == PID else bytes([0xE0]) + STATUS_QUERY[1:]
        dev.write_output(query, use_hidd=False)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            report = dev.read_input(timeout_ms=100)
            if report:
                status = parse_status(report)
                if status is not None and report[0] == query[0]:
                    return status
    raise hw.HidError("接收器未回复，请确认耳机已开机并处于 2.4G 模式")

if __name__ == "__main__":
    print(read_status())
