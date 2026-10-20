# -*- coding: utf-8 -*-
"""
battery.py —— 电量读取核心

对每个 HID 接口依次尝试多条策略，第一条成功的即可用：

  A. 标准 HID 电量字段（Usage Page 0x0006 / Usage 0x0020 Battery Strength，
     以及数字化仪页 0x000D/0x003B、电池系统页 0x0085 等）—— 读 Feature Report
  B. 已收录的厂商协议（Endgame Gear / VAXEE / Nordic nRF ...）
  C. 罗技 HID++ 2.0（Feature 0x1000 / 0x1004 / 0x1001）
  D. 用户自定义协议（protocols_user.json，由界面上的“手动标定”生成）
  E. 监听 Input Report 里上报的电量字段
  F. 原始 Feature Report 全量转储（给人看的，配合手动标定）
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import darkrim_proto as drp
from hid_transport import serialized
from hid_win import HidDevice, HidError, HidInterface, HidP_Feature, HidP_Input

USER_PROTOCOL_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "protocols_user.json"
)


# --------------------------------------------------------------------------
# 结果
# --------------------------------------------------------------------------
STATUS_TEXT = {
    "discharging": "放电中",
    "charging": "充电中",
    "full": "已充满",
    "unknown": "未知",
}


@dataclass
class BatteryResult:
    ok: bool
    level: Optional[int] = None  # 0-100
    raw_level: Optional[int] = None  # 原始数值
    status: str = "unknown"
    voltage_mv: Optional[int] = None
    source: str = ""  # 命中的策略
    detail: str = ""
    raw: bytes = b""
    extra: dict = field(default_factory=dict)

    @property
    def status_text(self) -> str:
        return STATUS_TEXT.get(self.status, self.status or "未知")


def _scale(value: int, logical_max: int = 100, logical_min: int = 0):
    """Debug-display helper; invalid and null values remain unknown."""
    if (
        value is None
        or logical_max <= logical_min
        or not logical_min <= value <= logical_max
    ):
        return None
    return round((value - logical_min) * 100 / (logical_max - logical_min))


# --------------------------------------------------------------------------
# 厂商协议表（均来自公开的协议文档，见文件末尾注释）
# --------------------------------------------------------------------------
@dataclass
class ProtocolSpec:
    name: str
    vid: int
    usage_page: int
    report_id: int
    payload: list  # 紧跟 report id 之后的请求字节
    length: int  # 报文总长度（含 report id）
    level_offset: int  # 从报文第 0 字节算起的电量字节下标
    wait_ms: int = 120
    scale: int = 1  # level = raw * scale
    charging_offset: Optional[int] = None
    charging_nonzero: bool = True
    voltage_offset: Optional[int] = None  # 小端 16 位
    level_max: Optional[int] = None  # 超过该值视为无效
    mode: str = ""  # "feature" / "output"，留空则自动判断
    expect: tuple = ()  # ((字节下标, 期望值), ...) 用于校验回复
    retries: int = 2  # 重试次数（第一次常被当作唤醒）
    notes: str = ""

    @property
    def kind(self) -> str:
        return self.mode or "feature"


BUILTIN_PROTOCOLS: list[ProtocolSpec] = [
    # ---- ATK / VXE / VGN 无线鼠标（VID 0x373B 为 ATK，0x3554 为 VXE/VGN）----
    # 协议 2：64 字节 Output Report，Report ID 0x08，命令 0x72；Nordic 54L15
    ProtocolSpec(
        name="ATK / VXE 无线鼠标（协议 2）",
        vid=0x373B,
        usage_page=0xFF05,
        report_id=0x08,
        payload=[0x7D, 0x72, 0x02, 0x00, 0x01, 0x07, 0x01],
        length=64,
        level_offset=7,
        wait_ms=100,
        scale=1,
        level_max=100,
        mode="output",
        expect=((1, 0x72), (5, 0x07)),
        retries=3,
        notes="电量在回复第 7 字节；第 1 字节固定 0x72 为有效回复标志",
    ),
    # 协议 1：17 字节 Output Report，Report ID 0x08；Nordic 52840
    ProtocolSpec(
        name="ATK / VXE / VGN 无线鼠标（协议 1）",
        vid=0x373B,
        usage_page=0xFF02,
        report_id=0x08,
        payload=[0x04] + [0x00] * 14 + [0x49],
        length=17,
        level_offset=6,
        wait_ms=100,
        scale=1,
        level_max=100,
        mode="output",
        expect=((1, 0x04), (2, 0x00)),
        retries=3,
        notes="电量在回复第 6 字节，第 7 字节非 0 表示有线连接",
    ),
    ProtocolSpec(
        name="VXE / VGN 无线鼠标（协议 1）",
        vid=0x3554,
        usage_page=0xFF02,
        report_id=0x08,
        payload=[0x04] + [0x00] * 14 + [0x49],
        length=17,
        level_offset=6,
        wait_ms=100,
        scale=1,
        level_max=100,
        mode="output",
        expect=((1, 0x04), (2, 0x00)),
        retries=3,
        notes="VXE R1 Pro Max / R1 SE+ / VGN F1 Pro",
    ),
    # Endgame Gear：VID 0x3367，usage page 0xFF01，Report ID 0xA1，64 字节 Feature
    ProtocolSpec(
        name="Endgame Gear（XM2w / OP1W / 无线接收器）",
        vid=0x3367,
        usage_page=0xFF01,
        report_id=0xA1,
        payload=[0xB4],
        length=64,
        level_offset=16,
        wait_ms=350,
        scale=1,
        voltage_offset=17,
        mode="feature",
        expect=((1, 0x01),),
        retries=3,
        notes="byte1 状态：0x01 有效 / 0x03 忙 / 0x07 不支持 / 0x08 鼠标休眠；"
        "需连读两次，第一次是唤醒",
    ),
    # VAXEE / ZYGEN：VID 0x3057，usage page 0xFF05，Report ID 0x0E，64 字节 Feature
    ProtocolSpec(
        name="VAXEE / ZYGEN（含 4K 接收器）",
        vid=0x3057,
        usage_page=0xFF05,
        report_id=0x0E,
        payload=[0xA5, 0x0B, 0x01, 0x01],
        length=64,
        level_offset=5,
        wait_ms=120,
        scale=5,
        level_max=20,
        mode="feature",
        notes="以 5% 为步长上报；充电状态用命令 0x10",
    ),
    # Nordic nRF 方案（VID 0x1915）：usage page 0xFFA0，Feature Report 5，32 字节
    ProtocolSpec(
        name="Nordic nRF 方案 2.4G 接收器",
        vid=0x1915,
        usage_page=0xFFA0,
        report_id=0x05,
        payload=[0x15, 0x00, 0x00, 0x01],
        length=32,
        level_offset=9,
        wait_ms=90,
        scale=1,
        mode="feature",
        notes="请求 0x15 读取电量",
    ),
]


def load_user_protocols() -> list[ProtocolSpec]:
    """读取用户通过“手动标定”保存的协议。"""
    if not os.path.exists(USER_PROTOCOL_FILE):
        return []
    try:
        with open(USER_PROTOCOL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        try:
            out.append(
                ProtocolSpec(
                    name=item.get("name", "自定义协议"),
                    vid=int(str(item["vid"]), 16)
                    if isinstance(item["vid"], str)
                    else int(item["vid"]),
                    usage_page=int(str(item["usage_page"]), 16)
                    if isinstance(item["usage_page"], str)
                    else int(item["usage_page"]),
                    report_id=int(str(item["report_id"]), 16)
                    if isinstance(item["report_id"], str)
                    else int(item["report_id"]),
                    payload=list(item.get("payload", [])),
                    length=int(item.get("length", 64)),
                    level_offset=int(item.get("level_offset", 0)),
                    wait_ms=int(item.get("wait_ms", 150)),
                    scale=int(item.get("scale", 1)),
                    charging_offset=item.get("charging_offset"),
                    voltage_offset=item.get("voltage_offset"),
                    level_max=item.get("level_max"),
                    notes=item.get("notes", "用户自定义"),
                )
            )
        except Exception:
            continue
    return out


def save_user_protocol(spec: ProtocolSpec) -> None:
    raw = []
    if os.path.exists(USER_PROTOCOL_FILE):
        try:
            with open(USER_PROTOCOL_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = []
    raw.append(
        {
            "name": spec.name,
            "vid": f"{spec.vid:04X}",
            "usage_page": f"{spec.usage_page:04X}",
            "report_id": f"{spec.report_id:02X}",
            "payload": list(spec.payload),
            "length": spec.length,
            "level_offset": spec.level_offset,
            "wait_ms": spec.wait_ms,
            "scale": spec.scale,
            "charging_offset": spec.charging_offset,
            "voltage_offset": spec.voltage_offset,
            "level_max": spec.level_max,
            "notes": spec.notes,
        }
    )
    with open(USER_PROTOCOL_FILE, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)


def all_protocols() -> list[ProtocolSpec]:
    return BUILTIN_PROTOCOLS + load_user_protocols()


# --------------------------------------------------------------------------
# 策略 A：标准 HID 电量字段
# --------------------------------------------------------------------------
def strategy_standard_battery_usage(iface: HidInterface) -> Optional[BatteryResult]:
    from standard_hid import read

    sample = read([iface])
    if sample is None:
        return None
    return BatteryResult(
        True,
        sample.level,
        sample.raw_level,
        source="标准 HID 电池字段",
        detail=sample.detail,
        raw=sample.report,
    )


# --------------------------------------------------------------------------
# 策略 A2：DARKRIM（杰理方案）2.4G 耳机 dongle —— 逆向得到的 TLV 协议
# --------------------------------------------------------------------------
def strategy_darkrim(iface: HidInterface) -> Optional[BatteryResult]:
    if (
        iface.vid != drp.VID
        or iface.usage_page != drp.USAGE_PAGE
        or iface.output_len != drp.REPORT_LEN
    ):
        return None
    info = drp.read_info(iface, timeout=0.35)
    if info.battery is None:
        return BatteryResult(
            ok=False,
            source="DARKRIM 私有协议（TLV）",
            detail=info.detail or "没有取到电量字段",
        )
    return BatteryResult(
        ok=True,
        level=info.battery,
        raw_level=info.battery_raw,
        status="unknown",
        source="DARKRIM 私有协议（TLV over HID）",
        detail=f"{info.detail}；电量 TLV(0x01) 原始值 0x{info.battery_raw:02X}",
        extra={
            "name": info.name,
            "dongle_fw": info.dongle_firmware,
            "headset_fw": info.headset_firmware,
            "address": info.address,
            "status_code": info.status_code,
            "tlvs": info.raw_tlvs,
        },
    )


# --------------------------------------------------------------------------
# 策略 B / D：厂商协议（含用户自定义）
# --------------------------------------------------------------------------
def _matches(report: bytes, expect: tuple) -> bool:
    return bool(report) and all(
        len(report) > off and report[off] == val for off, val in expect
    )


def _read_report_after_request(
    dev: HidDevice, spec: ProtocolSpec, kind: Optional[str] = None
) -> bytes:
    """按协议发请求、等回复、再读回报文。

    kind="feature"：发 Feature Report，直接读回 Feature Report。
    kind="output" ：发 Output Report，然后等 Input Report；
                    有 expect 时只接受符合特征的回复，否则拿最后一条。
    """
    kind = kind or spec.kind
    body = ([spec.report_id] + list(spec.payload) + [0] * spec.length)[: spec.length]
    last = b""
    for _ in range(max(1, spec.retries)):
        if kind == "output":
            dev.drain()
        if kind == "feature":
            dev.set_feature_report(bytes(body), allow_zero_access=True)
            time.sleep(spec.wait_ms / 1000.0)
            last = dev.get_feature_report(spec.report_id, spec.length)
            if last != bytes(body) and (not spec.expect or _matches(last, spec.expect)):
                return last
            continue
        dev.write_output(bytes(body))
        time.sleep(spec.wait_ms / 1000.0)
        end = time.monotonic() + 0.6
        while time.monotonic() < end:
            r = dev.read_input(timeout_ms=150)
            if not r:
                continue
            last = r
            if r != bytes(body) and (not spec.expect or _matches(r, spec.expect)):
                return r
    return b""


def _parse_spec_response(
    spec: ProtocolSpec, report: bytes, iface: HidInterface, kind: str
) -> BatteryResult:
    if not report or report[0] != spec.report_id or not _matches(report, spec.expect):
        return BatteryResult(
            False, source=spec.name, detail="回复报告 ID 或命令标记不匹配", raw=report
        )
    if len(report) <= spec.level_offset:
        return BatteryResult(
            ok=False,
            source=spec.name,
            detail=f"回复长度不足（{len(report)} 字节）",
            raw=report,
        )
    raw_level = report[spec.level_offset]
    if spec.level_max is not None and raw_level > spec.level_max:
        return BatteryResult(
            ok=False,
            source=spec.name,
            detail=f"电量字节 0x{raw_level:02X} 超出预期范围",
            raw=report,
        )
    level = raw_level * spec.scale
    if not 0 <= level <= 100:
        return BatteryResult(
            ok=False, source=spec.name, detail="电量值超出百分比范围", raw=report
        )

    status = "unknown"
    if spec.charging_offset is not None and len(report) > spec.charging_offset:
        v = report[spec.charging_offset]
        if spec.charging_nonzero:
            status = "charging" if v else "discharging"
        else:
            status = "discharging" if v else "charging"

    voltage = None
    if spec.voltage_offset is not None and len(report) > spec.voltage_offset + 1:
        voltage = report[spec.voltage_offset] | (report[spec.voltage_offset + 1] << 8)
        if not (2500 <= voltage <= 5000):
            voltage = None

    return BatteryResult(
        ok=True,
        level=level,
        raw_level=raw_level,
        status=status,
        voltage_mv=voltage,
        source=spec.name,
        detail=f"ReportID=0x{spec.report_id:02X} 电量字节@{spec.level_offset}=0x{raw_level:02X}"
        f" ×{spec.scale}",
        raw=report,
    )


def strategy_vendor_protocol(
    iface: HidInterface, *, include_user: bool = True
) -> Optional[BatteryResult]:
    specs = [
        s
        for s in (all_protocols() if include_user else BUILTIN_PROTOCOLS)
        if s.vid == iface.vid
        and s.usage_page == iface.usage_page
        and (iface.feature_len if s.kind == "feature" else iface.output_len) >= s.length
    ]
    if not specs:
        return None
    last = None
    for spec in specs:
        kind = spec.mode or ("feature" if iface.feature_len else "output")
        try:
            with serialized([iface]), HidDevice(iface.path, write=True) as dev:
                report = _read_report_after_request(dev, spec, kind)
                if not report:
                    last = BatteryResult(
                        ok=False,
                        source=spec.name,
                        detail="设备没有回复（多半是外设正在休眠，"
                        "动一下鼠标/开机后重试）",
                    )
                    continue
                res = _parse_spec_response(spec, report, iface, kind)
                if res.ok:
                    return res
                last = res
        except HidError as e:
            last = BatteryResult(
                ok=False,
                source=spec.name,
                detail=f"收发失败：{e}（可能是权限被占用，请关闭厂商驱动软件后重试）",
            )
    return last


# --------------------------------------------------------------------------
# 策略 C：罗技 HID++ 2.0
# --------------------------------------------------------------------------
def strategy_logitech_hidpp(iface: HidInterface) -> Optional[BatteryResult]:
    # Both frontends use the same validated HID++ implementation.
    import logitech_proto

    if not logitech_proto.matches([iface]):
        return None
    try:
        statuses = logitech_proto.read_status([iface])
        if not statuses:
            return None
        item = statuses[0]
        return BatteryResult(
            True,
            item.level,
            status=("charging" if item.charging else "discharging")
            if item.charging is not None
            else "unknown",
            voltage_mv=round(item.voltage * 1000) if item.voltage else None,
            source="Logitech HID++",
            detail=item.note,
            extra={"band": item.band, "slot": item.slot},
        )
    except HidError as exc:
        return BatteryResult(False, source="Logitech HID++", detail=str(exc))


# --------------------------------------------------------------------------
# 策略 E：监听 Input Report 上报的电量
# --------------------------------------------------------------------------
def monitor_input_reports(
    iface: HidInterface,
    seconds: float = 3.0,
    on_report: Optional[Callable[[bytes], None]] = None,
    stop_flag: Optional[Callable[[], bool]] = None,
) -> tuple[Optional[BatteryResult], list[bytes]]:
    """在若干秒内监听 Input Report，找上报的电量字段。"""
    if not iface.input_len:
        return None, []
    try:
        dev = HidDevice(iface.path, write=False)
    except HidError as e:
        return BatteryResult(ok=False, source="输入报告监听", detail=str(e)), []
    found: list[bytes] = []
    result: Optional[BatteryResult] = None
    try:
        import standard_hid

        battery_caps = [
            c for c in dev.value_caps(HidP_Input) if any(standard_hid.battery_fields(c))
        ]
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if stop_flag is not None and stop_flag():
                break
            report = dev.read_input(timeout_ms=250, stop_flag=stop_flag)
            if not report:
                continue
            found.append(report)
            if on_report:
                on_report(report)
            if result is not None:
                continue
            sample = standard_hid.decode(dev, HidP_Input, report, battery_caps)
            if sample:
                result = BatteryResult(
                    True,
                    sample.level,
                    sample.raw_level,
                    source="标准 HID 输入电池字段",
                    detail=sample.detail,
                    raw=report,
                )
            found = found[-64:]
    except HidError as e:
        return BatteryResult(ok=False, source="输入报告监听", detail=str(e)), found
    finally:
        dev.close()
    if result:
        found = found[-16:]
    return result, found


# --------------------------------------------------------------------------
# 策略 F：原始 Feature Report 转储
# --------------------------------------------------------------------------
def dump_feature_reports(iface: HidInterface) -> list[tuple[int, bytes, str]]:
    """扫描并读回所有报告 ID 的 Feature Report。返回 (report_id, data, 备注)。"""
    out: list[tuple[int, bytes, str]] = []
    if not iface.feature_len:
        return out
    ids: list[int] = []
    try:
        with HidDevice(iface.path, write=False) as dev:
            for cap in dev.value_caps(HidP_Feature):
                if cap.ReportID not in ids:
                    ids.append(cap.ReportID)
    except HidError:
        pass
    for rid in range(0x00, 0x21):
        if rid not in ids:
            ids.append(rid)

    try:
        with HidDevice(iface.path, write=False) as dev:
            for rid in ids:
                try:
                    data = dev.get_feature_report(rid)
                    note = "有数据"
                    if any(1 <= b <= 100 for b in data[1:]):
                        note = "含疑似电量字节"
                    out.append((rid, data, note))
                except HidError as e:
                    out.append((rid, b"", f"读取失败：{e}"))
    except HidError as e:
        out = [(0, b"", f"无法打开接口：{e}")]
    return out


# --------------------------------------------------------------------------
# 调度
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# 工具：探测 Report ID / 试发任意命令（配合手动标定）
# --------------------------------------------------------------------------
def probe_report_ids(iface: HidInterface, max_id: int = 0x20) -> list[int]:
    """试出该接口接受哪些 Output Report 的 Report ID（不认识的设备很有用）。

    Windows 对 Report ID 不匹配的写请求会返回 87（参数错误），
    因此用全 0 报文逐个试就能把合法 ID 列出来。
    """
    if not iface.output_len:
        return []
    ok_ids: list[int] = []
    try:
        with HidDevice(iface.path, write=True) as dev:
            length = dev.caps.OutputReportByteLength or iface.output_len
            for rid in range(0, max_id + 1):
                try:
                    dev.write_output(bytes([rid] + [0] * (length - 1)))
                    ok_ids.append(rid)
                except HidError:
                    continue
    except HidError:
        return []
    return ok_ids


def send_command(
    iface: HidInterface,
    rid: int,
    payload: list[int],
    kind: str = "output",
    length: Optional[int] = None,
    wait_ms: int = 350,
    repeat: int = 2,
) -> list[bytes]:
    """按指定方式发一条命令并收集回复（原始探测用）。"""
    replies: list[bytes] = []
    try:
        with HidDevice(iface.path, write=True) as dev:
            size = length or (dev.caps.OutputReportByteLength or iface.output_len)
            body = ([rid] + list(payload) + [0] * size)[:size]
            for _ in range(max(1, repeat)):
                try:
                    if kind == "feature":
                        iface_len = dev.caps.FeatureReportByteLength or size
                        body = ([rid] + list(payload) + [0] * iface_len)[:iface_len]
                        dev.set_feature_report(bytes(body), allow_zero_access=True)
                        time.sleep(wait_ms / 1000.0)
                        replies.append(dev.get_feature_report(rid, iface_len))
                    else:
                        dev.drain()
                        dev.write_output(bytes(body))
                        end = time.monotonic() + wait_ms / 1000.0
                        while time.monotonic() < end:
                            r = dev.read_input(timeout_ms=100)
                            if r:
                                replies.append(r)
                except HidError:
                    replies.append(b"")
                    break
    except HidError:
        return replies
    return replies


def read_battery(
    iface: HidInterface,
    listen_seconds: float = 3.0,
    progress: Optional[Callable[[str], None]] = None,
) -> list[BatteryResult]:
    """按顺序跑所有策略，返回所有尝试记录（第一条 ok=True 的是结论）。"""
    attempts: list[BatteryResult] = []
    steps = [
        ("标准 HID 电量字段", lambda: strategy_standard_battery_usage(iface)),
        ("DARKRIM 私有协议", lambda: strategy_darkrim(iface)),
        ("厂商协议", lambda: strategy_vendor_protocol(iface)),
        ("罗技 HID++", lambda: strategy_logitech_hidpp(iface)),
    ]
    for label, fn in steps:
        if progress:
            progress(f"尝试：{label}")
        try:
            res = fn()
        except Exception as e:  # 单个策略异常不影响其它策略
            res = BatteryResult(ok=False, source=label, detail=f"异常：{e}")
        if res is not None:
            attempts.append(res)
            if res.ok:
                return attempts

    if progress:
        progress(f"尝试：监听输入报告 {listen_seconds:.0f} 秒")
    res, _ = monitor_input_reports(iface, seconds=listen_seconds)
    if res is not None:
        attempts.append(res)
    return attempts


def best_result(attempts: list[BatteryResult]) -> Optional[BatteryResult]:
    for a in attempts:
        if a.ok:
            return a
    return attempts[0] if attempts else None
