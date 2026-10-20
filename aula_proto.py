"""AULA/AJAZZ receiver GET_DEVICE_INFO, limited to documented dongle IDs."""

from dataclasses import dataclass
import time
import hid_win as hw
from hid_transport import serialized

PIDS = frozenset((0xFEF8, 0xFEF9, 0xFEFA, 0xFEFC, 0xFEFD, 0xFEFE))


def matches(items):
    return any(d.vid == 0x0C45 and d.pid in PIDS for d in items)


@dataclass(frozen=True)
class Status:
    level: int
    charging: bool | None


def request(length, command, total):
    if length not in (33, 65) or (command, total) not in (
        (0x10, 56),
        (0x10, 48),
        (0xA0, 64),
    ):
        raise ValueError("仅允许 GET_DEVICE_INFO 查询")
    # Battery fields fit in the first chunk, so no further addresses are read.
    count = min(length - 9, total)
    packet = bytearray(length)
    packet[1:4] = bytes([0xAA, command, count])
    packet[7] = int(command == 0x10 and count == total)
    return bytes(packet)


def parse(raw, command, count):
    if (
        not raw
        or len(raw) < 28
        or raw[:3] != bytes([0, 0x55, command])
        or raw[3] < 19
        or raw[3] > count
        or raw[4:6] != b"\0\0"
        or len(raw) < 9 + raw[3]
    ):
        raise hw.HidError("AULA 回复头、地址或长度不匹配")
    payload = raw[9 : 9 + raw[3]]
    # An empty device-info block is a sleeping receiver, not an empty battery.
    vid, pid = (
        int.from_bytes(payload[4:6], "little"),
        int.from_bytes(payload[6:8], "little"),
    )
    if not vid or not pid or vid == 0xFFFF or pid == 0xFFFF or payload[17] > 100:
        raise hw.HidError("AULA 身份为空或电量无效")
    if payload[16] == 0:
        raise hw.HidError("键盘报告有线模式，此模式的电量字段不作无线电量使用")
    return Status(payload[17], bool(payload[18]) if payload[18] in (0, 1) else None)


def read_status(items):
    errors = []
    with serialized(items):
        for iface in items:
            if (
                not matches([iface])
                or iface.usage_page < 0xFF00
                or iface.output_len not in (33, 65)
            ):
                continue
            if iface.input_len != iface.output_len:
                continue
            if iface.pid == 0xFEFC and (iface.usage_page, iface.usage) != (0xFF80, 1):
                continue
            try:
                with hw.HidDevice(iface.path, write=True) as dev:
                    ids = {c.ReportID for c in dev.value_caps(hw.HidP_Output)}
                    ids.update(c.ReportID for c in dev.button_caps(hw.HidP_Output))
                    if 0 not in ids:
                        continue
                    for command, total in ((0x10, 56), (0x10, 48), (0xA0, 64)):
                        packet = request(iface.output_len, command, total)
                        dev.drain()
                        dev.write_output(packet, use_hidd=False)
                        end = time.monotonic() + 0.4
                        while time.monotonic() < end:
                            raw = dev.read_input(timeout_ms=30)
                            try:
                                return parse(raw, command, packet[3])
                            except hw.HidError:
                                continue
            except hw.HidError as exc:
                errors.append(str(exc))
    raise hw.HidError(errors[-1] if errors else "键盘休眠、未回复或此固件协议未适配")
