"""Shared, truthful device readings for the panel, CLI and tray."""
from __future__ import annotations
import time
from dataclasses import dataclass, field, asdict
from typing import Optional
import hid_win as hw
import battery
import edifier_proto
import zowie_proto
import zowie_profiles as profiles
import tray_common as tc

_mouse_display_band = ''
_mouse_context = None

@dataclass
class Reading:
    key: str
    name: str
    category: str
    usb_id: str
    receiver: bool = False
    online: Optional[bool] = None
    level: Optional[int] = None
    voltage: Optional[float] = None
    model: str = ""
    verification: str = ""
    battery_band: str = ""
    display_band: str = ""
    charging: Optional[bool] = None
    source: str = ""
    message: str = "正在检测设备"
    firmware: str = ""
    connection: str = "2.4G 无线"
    checked_at: float = 0.0
    attempts: list[str] = field(default_factory=list)
    interfaces: list[dict] = field(default_factory=list)
    kind: str = "device"
    protocol: str = ""
    raw_level: Optional[int] = None
    present: bool = True
    receiver_key: str = ''
    stale: bool = False
    sampled_at: float = 0.0

    def to_dict(self):
        return asdict(self)


def receiver_count(rows):
    return len({r.receiver_key or r.key for r in rows if r.present and r.receiver})

def interface_info(iface):
    return {"collection": iface.path.split("#")[1], "usage": iface.describe_usage(),
            "input": iface.input_len, "output": iface.output_len,
            "feature": iface.feature_len}

def _known_templates():
    return [Reading("mouse", "ZOWIE 无线鼠标", "无线游戏鼠标", "04A5:800A"),
            Reading("headset", "漫步者 G5 MAX", "无线游戏耳机", "35BB:A00B")]


def initial_readings():
    return []

def standard_read(row, interfaces):
    for iface in interfaces:
        result = battery.strategy_standard_battery_usage(iface)
        if result and result.ok and result.level is not None:
            row.level, row.online = result.level, True
            row.source, row.message = result.source, "已读取电量"
            return True
        # Only matching built-in protocols are attempted; unknown vendor writes are excluded.
        result = battery.strategy_vendor_protocol(iface, include_user=False)
        if result and result.ok and result.level is not None:
            row.level, row.online = result.level, True
            row.source, row.message = result.source, "已读取电量"
            return True
    return False

def read_mouse(interfaces, model=None):
    global _mouse_display_band, _mouse_context
    model = tc.load_config()["zowie_model"] if model is None else model
    profile = profiles.get_profile(model)
    row = _known_templates()[0]
    row.kind, row.protocol = 'mouse', 'zowie'
    row.model = model
    row.name = "ZOWIE " + profile.model if profile else "ZOWIE 无线鼠标"
    row.verification = profile.evidence if profile else "待选择型号；共享接收器无法识别具体型号"
    pids = ((0x8008, 0x800a) if model == 'U2' else (profile.receiver_pid,)) if profile else (0x8006, 0x8008, 0x800a)
    matches = [d for d in interfaces if d.vid == 0x04a5 and d.pid in pids]
    row.receiver = bool(matches)
    row.usb_id = " / ".join(sorted({d.vid_pid if hasattr(d, 'vid_pid') else f"{d.vid:04X}:{d.pid:04X}" for d in matches}))
    row.interfaces = [interface_info(d) for d in matches]
    context = (model, tuple(sorted(d.path for d in matches)))
    if _mouse_context is None or context != _mouse_context[:2]:
        _mouse_display_band = ''
        _mouse_context = context
    if not matches:
        _mouse_display_band = ''
        row.message = "未检测到所选型号的接收器" if profile else "未检测到卓威接收器"
        return row
    row.attempts = [row.verification]
    if profile is None and not all(d.pid == 0x800a for d in matches):
        _mouse_display_band = ''
        row.message = "请在偏好设置中选择卓威型号"
        return row
    if model == 'U2' and not any(d.pid == 0x800a for d in matches):
        _mouse_display_band = ''
        row.message = "已识别 U2 接收器；读取协议尚未验证"
        row.attempts.append("已离线验证 U2 固件阈值；不向未验证接收器发送 DW 命令")
        return row
    if model == 'U2':
        matches = [d for d in matches if d.pid == 0x800a]
    try:
        if profile and profile.transport == 'cw':
            voltage, reports = zowie_proto.read_cw_voltage(matches)
            _mouse_display_band = ''
            row.voltage = voltage
            row.source = "EC-CW 实验性电压查询"
            row.message = "未连接或未返回电压" if voltage is None else f"最近回复 {voltage:.3f} V；缺少固件映射"
            row.attempts += reports + ["未使用通用锂电池曲线；不从未经验证的百分比推断电量灯档位"]
            return row
        row.attempts += ["只读查询：40 接收器版本 / 25 鼠标版本 / 15 电压缓存"]
        status = zowie_proto.read_status(matches)
        row.firmware = f"鼠标 {status.mouse_firmware} / 接收器 {status.receiver_firmware}"
        row.attempts += status.reports
        if status.receiver_firmware == '8507':
            row.attempts.append('接收器 8507 的只读命令已离线执行验证；尚未实机验证')
        if not status.linked:
            _mouse_display_band = ''
            row.online = False
            row.source = "鼠标连接状态未知"
            row.message = "鼠标未连接或正在休眠，请移动后刷新"
            return row
        # Reset hysteresis when the cached firmware changes (including another mouse).
        firmware_context = context + (status.mouse_firmware, status.receiver_firmware)
        if _mouse_context != firmware_context:
            _mouse_display_band = ''
        _mouse_context = firmware_context
        row.voltage = status.voltage
        row.battery_band = zowie_proto.voltage_band(status.voltage, status.mouse_firmware, model)
        row.display_band = zowie_proto.display_band(status.voltage, status.mouse_firmware, _mouse_display_band, model)
        _mouse_display_band = row.display_band
        row.online = None  # A cached nonzero firmware version does not prove a live link.
        row.source = "接收器电压缓存"
        row.message = (f"电量{row.display_band} · 最近接收 {status.voltage:.2f} V" if row.display_band else
                       "已读电压；请先选择鼠标型号" if profile is None else "已读电压；此型号或固件的档位未验证")
        row.attempts += ["电压查询不会强制鼠标重新采样；缓存采样时间未知",
                         "接收器回复未提供可靠的充电状态或剩余容量百分比"]
        if profile and status.mouse_firmware not in profile.firmwares:
            row.attempts.append("此型号和固件组合未验证，隐藏档位；相同版本号不能证明不同型号阈值相同")
        if row.battery_band:
            row.attempts += [f"{profile.family} 固件启动电量灯推断：{row.battery_band}；不是当前灯态或精确容量",
                             profiles.policy_text(model)]
    except Exception as exc:
        _mouse_display_band = ''
        row.source = "电压暂不可用"
        row.message = "接收器已连接，暂无法读取电压"
        row.attempts.append(str(exc))
    return row


def read_headset(interfaces):
    row = _known_templates()[1]
    row.kind, row.protocol = 'headset', 'edifier'
    matches = [d for d in interfaces if d.vid == 0x35BB and d.pid in (0xA00B, 0xA211)]
    row.receiver = any(d.pid == 0xA00B for d in matches)
    row.interfaces = [interface_info(d) for d in matches]
    if not matches:
        row.message = "未检测到耳机接收器"
        return row
    if standard_read(row, matches):
        return row
    row.attempts = ["原项目标准 HID / 厂商策略：无匹配电量协议"]
    # Query receiver first, then an explicitly connected USB headset as a labelled fallback.
    candidates = []
    for pid, col in [(0xA00B, "mi_03&col03"), (0xA211, "mi_03&col02")]:
        candidates += [d for d in matches if d.pid == pid and col in d.path.lower()]
    for iface in candidates:
        wireless = iface.pid == 0xA00B
        mode = "2.4G 接收器" if wireless else "USB 有线"
        try:
            status = edifier_proto.read_status(iface)
            row.attempts.append(f"{mode}状态查询：" +
                                (f"{status.level}%" if status.online else "耳机未连接"))
            if not status.online:
                row.online = False
                row.message = "接收器已连接，请开启耳机并切到 2.4G"
                continue
            row.online, row.level = True, status.level
            row.charging, row.firmware = status.charging, status.firmware
            row.connection = "2.4G 无线" if wireless else "USB 有线"
            row.usb_id = iface.vid_pid
            row.source = "接收器直接读取" if wireless else "USB 直接读取"
            row.message = "正在充电" if status.charging else "电量已同步"
            return row
        except Exception as exc:
            row.attempts.append(f"{mode}：{exc}")
            row.message = "暂时无法读取，请重试或检查耳机模式"
    row.source = "设备未返回在线电量"
    return row

def read_all():
    global _mouse_display_band
    try:
        interfaces = hw.enumerate_hid()
    except Exception as exc:
        _mouse_display_band = ''
        row = Reading('scan-error', '设备扫描失败', '系统状态', '', present=False,
                      message=str(exc), checked_at=time.time())
        return [row]
    from device_discovery import read_devices
    return read_devices(interfaces)
