"""Read-only ASUS mice and VGN/ATK protocol families.

Independent implementation of wire formats documented in docs/PROTOCOLS.md.
Identity, endpoint, report ID and reply command must all match. No VID sweeps.
"""

from dataclasses import dataclass
import time
import hid_win as hw
from hid_transport import serialized


@dataclass(frozen=True)
class Profile:
    family: str
    name: str
    receiver: bool
    kind: str = "mouse"
    report_id: int = 0
    interface: int = 0
    offset: int = 4  # payload offset (excludes Windows report ID)
    coarse: bool = False


PROFILES = {}
for wired, wireless, name, coarse in (
    (0x1A18, 0x1A1A, "ROG Chakram X", False),
    (0x1A70, 0x1A72, "ROG Gladius III AimPoint", False),
    (0x195E, 0x1960, "ROG Keris Wireless", True),
    (0x1A57, 0x1A59, "ROG Keris Wireless EVA", True),
    (0x1947, 0x1949, "ROG Strix Impact II Wireless", True),
    (0x197D, 0x197F, "ROG Gladius III Wireless", False),
    (0x1B0A, 0x1B0C, "ROG Gladius III EVA-02", False),
    (0x1A92, 0x1A94, "ROG Harpe Ace Aim Lab", False),
    (0x1A66, 0x1A68, "ROG Keris Wireless AimPoint", False),
    (0x1906, 0x1908, "ROG Pugio II", True),
    (0x18E3, 0x18E5, "ROG Chakram", True),
    (0x1977, 0x1979, "ROG Spatha X", False),
):
    for pid, receiver in ((wired, False), (wireless, True)):
        PROFILES[0x0B05, pid] = Profile("asus-mouse", name, receiver, coarse=coarse)
for pid, name, interface, offset, coarse in (
    (0x18A0, "ROG Gladius II Wireless", 2, 4, True),
    (0x18B4, "ROG Strix Carry", 1, 6, True),
    (0x1A24, "ASUS MD200", 2, 4, False),
    (0x19F4, "TUF Gaming M4 Wireless", 0, 4, False),
    (0x1A8D, "TUF Gaming M4 Wireless", 0, 4, False),
):
    PROFILES[0x0B05, pid] = Profile(
        "asus-mouse", name, True, interface=interface, offset=offset, coarse=coarse
    )

# MouseEnc protocol identities. Unknown connection modes stay unconfirmed.
for vid, pids, rid in (
    (
        0x3554,
        (
            0xFB3D,
            0xFB3E,
            0xFB53,
            0xFB52,
            0xFB55,
            0xFB54,
            0xFB57,
            0xFB56,
            0xFB49,
            0xFB4A,
            0xFB4B,
            0xF502,
            0xF50A,
            0xF50B,
            0xF503,
            0xF505,
            0xF506,
            0xFB51,
            0xFB50,
            0xFB59,
            0xFB58,
        ),
        8,
    ),
    (0x391D, (0x1005, 0x1A05, 0x101D, 0x1A1A, 0x1034, 0x1A2F, 0x1032, 0x102D), 8),
    (0x9981, (0x6002, 0x7002), 32),
    (0x372E, (0x10AC, 0x10AD), 32),
):
    for pid in pids:
        PROFILES[vid, pid] = Profile(
            "mouse-enc",
            "VGN / VXE 无线鼠标",
            (vid, pid)
            in {
                (0x3554, p)
                for p in (0xFB3E, 0xFB52, 0xFB54, 0xFB49, 0xF503, 0xF505, 0xFB58)
            }
            or (vid, pid) == (0x391D, 0x1A05),
            report_id=rid,
        )
for vid, wired, wireless in ((0x320F, 0x5055, 0x5088), (0x373B, 0x1028, 0x1029)):
    for pid in (wired, wireless):
        PROFILES[vid, pid] = Profile(
            "weisheng", "VGN / ATK 无线键盘", pid == wireless, "keyboard", 4
        )


@dataclass(frozen=True)
class Status:
    level: int | None = None
    charging: bool | None = None
    band: str = ""
    voltage: float | None = None
    raw: int | None = None


def detect(items):
    return next(
        (PROFILES[d.vid, d.pid] for d in items if (d.vid, d.pid) in PROFILES), None
    )


def request(profile, command=None):
    if profile.family == "asus-mouse":
        return bytes([0, 0x12, 7]).ljust(65, b"\0")
    if profile.family == "weisheng":
        return bytes([4, 0, 0, 26, 6, 0, 0, 0])
    if profile.family == "mouse-enc" and command in (3, 4):
        data = bytearray(17)
        data[0:2] = bytes([profile.report_id, command])
        data[-1] = (0x55 - sum(data[:-1])) & 255
        return bytes(data)
    raise ValueError("未允许的电量查询")


def parse(profile, raw, command=None):
    if not raw or raw[0] != profile.report_id:
        raise hw.HidError("电量报告 ID 不匹配")
    data = raw[1:]
    if profile.family == "asus-mouse":
        if len(data) < 10 or data[:2] != b"\x12\x07":
            raise hw.HidError("ASUS 电量命令不匹配")
        value = data[profile.offset]
        if value > (4 if profile.coarse else 100):
            raise hw.HidError("ASUS 电量超出范围")
        return Status(
            None if profile.coarse else value,
            bool(data[9]) if data[9] in (0, 1) else None,
            {0: "低档", 1: "低档", 2: "中档", 3: "高档", 4: "高档"}[value]
            if profile.coarse
            else "",
            raw=value,
        )
    if profile.family == "weisheng":
        if len(data) < 9 or data[2] != 26 or data[7] > 100:
            raise hw.HidError("键盘电量回复无效")
        return Status(
            data[7], bool(data[8]) if data[8] in (0, 1) else None, raw=data[7]
        )
    if profile.family == "mouse-enc":
        if len(data) != 16 or data[:2] != bytes([command, 0]):
            raise hw.HidError("MouseEnc 命令回复无效")
        if command == 3:
            return data[5] > 0
        if command != 4 or data[5] > 100:
            raise hw.HidError("MouseEnc 电量超出范围")
        mv = int.from_bytes(data[7:9], "big")
        return Status(
            data[5],
            bool(data[6]) if data[6] in (0, 1) else None,
            voltage=mv / 1000 if 2500 <= mv <= 5000 else None,
            raw=data[5],
        )
    raise hw.HidError("未适配的协议")


def read_status(items, profile):
    errors = []
    with serialized(items):
        for iface in items:
            if PROFILES.get((iface.vid, iface.pid)) != profile:
                continue
            size = len(request(profile, 3 if profile.family == "mouse-enc" else None))
            if iface.output_len != size or iface.input_len < (
                10 if profile.family == "weisheng" else size
            ):
                continue
            if profile.family == "asus-mouse":
                if f"mi_{profile.interface:02x}" not in iface.path.lower():
                    continue
            elif iface.usage_page < 0xFF00:
                continue
            try:
                with hw.HidDevice(iface.path, write=True) as dev:
                    declared = {c.ReportID for c in dev.value_caps(hw.HidP_Output)}
                    declared.update(c.ReportID for c in dev.button_caps(hw.HidP_Output))
                    if profile.report_id not in declared:
                        continue

                    def exchange(command=None):
                        dev.drain()
                        packet = request(profile, command)
                        dev.write_output(packet, use_hidd=False)
                        end = time.monotonic() + 0.5
                        while time.monotonic() < end:
                            raw = dev.read_input(timeout_ms=30)
                            if raw == packet:
                                continue
                            try:
                                return parse(profile, raw, command)
                            except hw.HidError:
                                continue
                        raise hw.HidError("外设未返回匹配的电量报文")

                    if profile.family == "mouse-enc":
                        if not exchange(3):
                            raise hw.HidError("鼠标休眠或断开")
                        return exchange(4)
                    return exchange()
            except hw.HidError as exc:
                errors.append(str(exc))
    raise hw.HidError(errors[-1] if errors else "未找到此型号声明的电量接口")
