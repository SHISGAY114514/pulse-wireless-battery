"""MCHOSE V9 Turbo+ battery query, independent of the MCHOSE mouse protocol.

Official M HUB V9_TURBO profile: report 55, payload 65 01, 63 bytes.
Reply: 55 65 <percent> <state>; 02 discharge, 03 charge, 04 full, 1A asleep.
Only the battery query is implemented; no audio/settings/firmware commands.
"""
from dataclasses import dataclass
import time

import hid_win as hw
from hid_transport import serialized
from device_identity import physical_groups

VID, PID = 0x3837, 0x600A
USAGE_PAGE, REPORT_ID, REPORT_LENGTH = 0xFF90, 0x55, 64
BATTERY_QUERY = bytes.fromhex("55 65 01").ljust(REPORT_LENGTH, b"\0")


@dataclass(frozen=True)
class Status:
    level: int | None
    online: bool
    charging: bool | None
    full: bool = False


def matches(interfaces):
    # 3837:6008 is the lighting dock, not a second battery receiver.
    return any((d.vid, d.pid) == (VID, PID) for d in interfaces)


def parse_reply(packet):
    if len(packet) != REPORT_LENGTH or packet[:2] != b"\x55\x65":
        raise hw.HidError("V9 Turbo+ 电量回复长度或命令不匹配")
    level, state = packet[2:4]
    if state == 0x1A:
        return Status(None, False, None)
    # State 00 is also the query's padding: never accept a request echo.
    if state not in (2, 3, 4) or level > 100:
        raise hw.HidError("V9 Turbo+ 电量或状态字段无效")
    return Status(level, True, state == 3, state == 4)


def read_status(interfaces):
    items = [d for d in interfaces if (d.vid, d.pid) == (VID, PID)]
    candidates = [d for d in items if d.usage_page == USAGE_PAGE and d.usage == 1
                  and d.input_len == REPORT_LENGTH and d.output_len == REPORT_LENGTH]
    if len(physical_groups(items)) != 1 or len(candidates) != 1:
        raise hw.HidError("需要唯一的 V9 Turbo+ FF90 / 64 字节接口")
    with serialized(items), hw.HidDevice(candidates[0].path, write=True) as dev:
        # The device's output value/button caps are empty despite a declared
        # 64-byte Output report. Its Input caps identify report 55 explicitly.
        report_ids = {cap.ReportID for cap in dev.value_caps(hw.HidP_Input)
                      + dev.button_caps(hw.HidP_Input)}
        if REPORT_ID not in report_ids:
            raise hw.HidError("V9 Turbo+ 接口未声明输入报告 55")
        dev.drain()
        dev.write_output(BATTERY_QUERY, use_hidd=False, timeout_ms=800)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            remaining = max(1, min(100, int((deadline - time.monotonic()) * 1000)))
            packet = dev.read_input(timeout_ms=remaining)
            if not packet:
                continue
            try:
                return parse_reply(packet)
            except hw.HidError:
                # Other notifications can share report 55; wait for battery.
                continue
    raise hw.HidError("V9 Turbo+ 未返回有效电量；耳机可能休眠或未连接")
