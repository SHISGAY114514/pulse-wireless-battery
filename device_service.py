"""Shared, truthful device readings for the panel, CLI and tray."""
from __future__ import annotations
import time
from dataclasses import dataclass, field, asdict
from typing import Optional
import hid_win as hw
import battery
import edifier_proto
import zowie_proto

_mouse_display_band = ''

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

    def to_dict(self):
        return asdict(self)

def interface_info(iface):
    return {"collection": iface.path.split("#")[1], "usage": iface.describe_usage(),
            "input": iface.input_len, "output": iface.output_len,
            "feature": iface.feature_len}

def initial_readings():
    return [Reading("mouse", "ZOWIE EC2-DW", "无线游戏鼠标", "04A5:800A"),
            Reading("headset", "漫步者 G5 MAX", "无线游戏耳机", "35BB:A00B")]

def standard_read(row, interfaces):
    for iface in interfaces:
        result = battery.strategy_standard_battery_usage(iface)
        if result and result.ok and result.level is not None:
            row.level, row.online = result.level, True
            row.source, row.message = result.source, "已读取电量"
            return True
        # Only matching built-in protocols are attempted; unknown vendor writes are excluded.
        result = battery.strategy_vendor_protocol(iface)
        if result and result.ok and result.level is not None:
            row.level, row.online = result.level, True
            row.source, row.message = result.source, "已读取电量"
            return True
    return False

def read_mouse(interfaces):
    global _mouse_display_band
    row = initial_readings()[0]
    matches = [d for d in interfaces if (d.vid, d.pid) == (0x04A5, 0x800A)]
    row.receiver = bool(matches)
    row.interfaces = [interface_info(d) for d in matches]
    if not matches:
        _mouse_display_band=''
        row.message = "未检测到鼠标接收器"
        return row
    if standard_read(row, matches):
        _mouse_display_band=''
        return row
    row.attempts = ["已检测到卓威接收器 04A5:800A",
                    "原项目标准 HID 策略：未声明电量字段",
                    "原项目厂商协议表：没有匹配的卓威协议",
                    "厂商私有通道：FF03 发送 / FF04 接收",
                    "已确认只读查询：40 接收器版本 / 25 鼠标版本 / 15 电压缓存"]
    try:
        status=zowie_proto.read_status(matches)
        row.firmware=f"鼠标 {status.mouse_firmware} / 接收器 {status.receiver_firmware}"
        row.attempts += status.reports
        if not status.linked:
            _mouse_display_band=''
            row.online=False
            row.source="鼠标连接状态未知"
            row.message="鼠标未连接或正在休眠，请移动后刷新"
            return row
        row.voltage=status.voltage
        row.battery_band=zowie_proto.voltage_band(status.voltage,status.mouse_firmware)
        row.display_band=zowie_proto.display_band(status.voltage,status.mouse_firmware,_mouse_display_band)
        _mouse_display_band=row.display_band
        # Nonzero cached firmware does not prove that the mouse is online now.
        row.online=None
        row.source="接收器电压缓存"
        row.message=f"电量{row.display_band} · 最近接收电压 {status.voltage:.2f} V" if row.display_band else f"最近接收电压 {status.voltage:.2f} V；档位未适配"
        row.attempts += ["电压查询不会强制鼠标重新采样；缓存采样时间未知",
                         "接收器回复未提供可靠的充电状态或剩余容量百分比"]
        if row.battery_band:
            row.attempts.append("按 EC-DW 同版本固件推断电量灯档位："+row.battery_band+"；不是当前灯态或精确容量测量")
        if row.display_band:
            row.attempts += ["显示分档（首次）：≤3.75 V 低档 / 3.76–3.89 V 中档 / ≥3.90 V 高档",
                             "边界缓冲：升档需≥3.77 / ≥3.90 V；降档需≤3.74 / ≤3.88 V；断开后重判"]
    except Exception as exc:
        _mouse_display_band=''
        row.source="电压暂不可用"
        row.message="接收器已连接，暂无法读取电压"
        row.attempts.append(str(exc))
    return row

def read_headset(interfaces):
    row = initial_readings()[1]
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
        _mouse_display_band=''
        rows = initial_readings()
        for row in rows:
            row.message = "设备扫描失败"
            row.attempts = [str(exc)]
            row.checked_at = time.time()
        return rows
    rows = []
    for function in (read_mouse, read_headset):
        try:
            row = function(interfaces)
        except Exception as exc:
            row = initial_readings()[len(rows)]
            row.message = "设备读取出现异常"
            row.attempts = [str(exc)]
        row.checked_at = time.time()
        rows.append(row)
    return rows
