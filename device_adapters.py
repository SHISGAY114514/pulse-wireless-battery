"""Protocol-to-Reading adapters; transport and polling lifecycle live elsewhere."""

import time
from dataclasses import replace
import battery
import readings as ds
import hid_win as hw
import mchose_proto
import keyboard_proto
import logitech_proto
from device_identity import device_key, kind_of
from standard_hid import read as read_standard


def standard_status(interfaces):
    sample = read_standard(interfaces)
    if sample is not None:
        return battery.BatteryResult(
            True,
            sample.level,
            sample.raw_level,
            source="标准 HID 电池字段",
            detail=sample.detail,
        )
    return None


def vendor_candidate(interfaces):
    # Existing upstream adapters remain visible, but successful exchange is
    # still required before declaring a battery reading.
    return any(
        d.vid == s.vid
        and d.usage_page == s.usage_page
        and (d.feature_len if s.kind == "feature" else d.output_len) >= s.length
        for d in interfaces
        for s in battery.BUILTIN_PROTOCOLS
    )


def read_generic(interfaces, group):
    kind, category = kind_of(interfaces)
    row = ds.Reading(
        "hid:" + device_key(group),
        hw.pick_device_name(interfaces),
        category,
        interfaces[0].vid_pid,
        kind=kind,
        connection="USB / 连接方式未确认",
    )
    row.interfaces = [ds.interface_info(d) for d in interfaces]
    result = standard_status(interfaces)
    if result is None and vendor_candidate(interfaces):
        row.protocol = "upstream-vendor"
        row.verification = "原项目厂商协议适配；未在本机实测此型号"
        row.connection = "USB / 连接方式未确认"
        for iface in interfaces:
            try:
                result = battery.strategy_vendor_protocol(iface, include_user=False)
            except Exception as exc:
                row.attempts.append(str(exc))
                continue
            if result and result.ok:
                break
    if result and result.ok:
        row.level = result.level
        row.raw_level = result.raw_level
        row.charging = {"charging": True, "discharging": False, "full": False}.get(
            result.status
        )
        row.voltage = result.voltage_mv / 1000 if result.voltage_mv else None
        row.source = result.source
        row.message = "已读取设备电量" if row.level is not None else "仅读取设备电压"
        row.attempts.append(result.detail)
        row.protocol = row.protocol or "standard-hid"
    else:
        row.source = "电量协议未适配"
        row.message = "已发现设备，暂不支持读取电量"
        row.attempts.append(
            "未找到标准电池字段或匹配的已确认协议；没有扫描未知厂商命令"
        )
        if result:
            row.attempts.append(result.detail)
    return row


def read_mchose(items, group, cache):
    d = items[0]
    receiver = d.pid in (0x1020, 0x100A, 0x100B)
    row = ds.Reading(
        "mchose:" + device_key(group),
        hw.pick_device_name(items),
        "无线游戏鼠标",
        d.vid_pid,
        receiver=receiver,
        connection="2.4G 无线" if receiver else "USB 有线",
        kind="mouse",
        protocol="mchose",
        verification="A7 Ultra 有线及 5253:1020 纯无线已实测；其余收录身份为协议适配",
    )
    row.interfaces = [ds.interface_info(x) for x in items]
    try:
        status = mchose_proto.read_status(items)
        row.level, row.raw_level, row.charging = (
            status.level,
            status.level,
            status.charging,
        )
        row.firmware = status.firmware
        row.online = True
        row.sampled_at = time.time()
        row.source = "迈从设备直接读取"
        row.message = "正在充电" if status.charging else "已读取电量"
        row.attempts = [
            f"Feature 11/12，只读状态命令 06；逻辑设备 {status.logical_vid:04X}:{status.logical_pid:04X}",
            "鼠标休眠时接收器会返回空身份，不能按 0% 处理",
        ]
        cache[group] = replace(row, attempts=list(row.attempts))
    except hw.HidError as exc:
        row.source = "迈从当前电量不可用"
        row.message = "接收器已连接，鼠标可能休眠；移动后刷新"
        row.attempts = [str(exc)]
        last = cache.get(group)
        if receiver and last and 0 <= time.time() - last.sampled_at <= 600:
            row.level, row.sampled_at, row.stale = last.level, last.sampled_at, True
            row.source = "上次成功读取（缓存，非当前电量）"
            row.message = f"鼠标休眠/未回复 · 上次 {last.level}%，非当前值"
    return row


def read_keyboard(items, group, profile):
    row = ds.Reading(
        "keyboard:" + device_key(group),
        profile.name,
        "无线键盘",
        items[0].vid_pid,
        receiver=profile.receiver,
        connection=profile.connection,
        kind="keyboard",
        protocol="keyboard-" + profile.code,
        verification=(
            "本机 RK R98 已识别 25A7:FA70 / 258A:00C1；官方 1024 候选固件仅完成离线灯分支验证，实机 1023 电量尚未读到"
            if profile.code in ("compx-rk", "rk98-wired")
            else "公开协议适配，未在本机逐型号实测"
        ),
    )
    row.interfaces = [ds.interface_info(d) for d in items]
    try:
        status = keyboard_proto.read_status(items, profile)
        row.level, row.charging = status.level, status.charging
        row.online = True
        row.source = "键盘协议直接读取"
        row.message = "正在充电" if status.charging else "已读取键盘电量"
        row.attempts = [status.note] if status.note else []
    except hw.HidError as exc:
        row.message = "键盘已发现，暂无法读取电量"
        row.source = "电量未取得"
        row.attempts = [str(exc)]
    return row


def read_logitech(items, group):
    d = items[0]
    physical = device_key(group)
    receiver = d.pid in logitech_proto.RECEIVERS
    statuses = logitech_proto.read_status(items)
    rows = []
    for status in statuses:
        row = ds.Reading(
            "logitech:" + physical + f":{status.slot}",
            status.name or f"Logitech 外设 · 槽 {status.slot}",
            {"keyboard": "无线键盘", "mouse": "无线鼠标"}.get(status.kind, "无线外设"),
            d.vid_pid,
            receiver=receiver,
            receiver_key=physical,
            connection="2.4G 无线" if receiver else "USB / 连接方式未确认",
            kind=status.kind,
            protocol="logitech",
            level=status.level,
            charging=status.charging,
            voltage=status.voltage,
            display_band=status.band,
            source="Logitech HID++ 只读协议",
            message="已读取设备电量",
            verification="HID++ 公开协议适配，未在本机实测",
            attempts=[status.note],
        )
        row.interfaces = [ds.interface_info(x) for x in items]
        rows.append(row)
    if not rows:
        rows = [
            ds.Reading(
                "logitech:" + physical,
                "Logitech 接收器" if receiver else hw.pick_device_name(items),
                "无线外设",
                d.vid_pid,
                receiver=receiver,
                receiver_key=physical,
                protocol="logitech",
                message="接收器已发现；外设休眠、离线或未提供已适配电池特征",
                source="暂无电量",
                connection="2.4G 无线" if receiver else "USB / 连接方式未确认",
            )
        ]
    return rows
