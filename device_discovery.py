"""Adapter registry and isolated polling sessions for physical peripherals."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

import hid_win as hw
import device_service as ds
import device_adapters as adapters
import tray_common as tc
import razer_proto
import mchose_proto
import keyboard_proto
import logitech_proto
import darkrim_proto
import receiver_protocols
import aula_proto
from device_identity import physical_groups, device_key, kind_of as kind_of
from standard_hid import (
    battery_fields as battery_fields,
    report_types as report_types,
    has_battery_field,
)
from device_adapters import standard_status, vendor_candidate


@dataclass(frozen=True)
class Adapter:
    name: str
    matches: Callable
    read: Callable


def _zowie(items):
    return any(d.vid == 0x04A5 and d.pid in (0x8006, 0x8008, 0x800A) for d in items)


def _edifier(items):
    return any(d.vid == 0x35BB and d.pid in (0xA00B, 0xA211) for d in items)


def _darkrim(items):
    return any(
        d.vid == darkrim_proto.VID
        and d.usage_page == darkrim_proto.USAGE_PAGE
        and d.usage == darkrim_proto.USAGE
        and d.output_len == 65
        and d.input_len == 65
        for d in items
    )


def read_zowie(items, group, session, cfg):
    ident = device_key(group)
    model = cfg["zowie_models"].get(
        ident, cfg["zowie_model"] if session.zowie_count == 1 else "auto"
    )
    state = session.zowie_states.setdefault(group, ds.ZowieState())
    # Queue local Zowie workers before taking the public cross-process mutex.
    with session.zowie_lock:
        row = ds.read_mouse(items, model, state=state)
    row.key, row.receiver = "zowie:" + ident, True
    return [row]


def read_edifier(items, group, session, cfg):
    row = ds.read_headset(items)
    row.key = "edifier:" + device_key(group)
    return [row]


def read_razer(items, group, session, cfg):
    d = next(d for d in items if d.vid == 0x1532 and d.pid in razer_proto.MODELS)
    profile = razer_proto.MODELS[d.pid]
    row = ds.Reading(
        "razer:" + device_key(group),
        profile.name,
        "无线键盘" if profile.kind == "keyboard" else "无线游戏鼠标",
        d.vid_pid,
        receiver=profile.receiver,
        connection="2.4G 无线" if profile.receiver else "USB 有线",
        kind=profile.kind,
        protocol="razer",
        verification="公开协议适配；00B7 已在本机实测",
    )
    try:
        status = razer_proto.read_status(items)
        row.level, row.charging, row.raw_level = (
            status.level,
            status.charging,
            status.raw,
        )
        row.online, row.source = True, "雷蛇设备直接读取"
        row.message = "正在充电" if status.charging else "已读取电量"
        row.attempts = status.notes + [
            "07:80 电量 / 07:84 充电；0–255 按比例转换为百分比"
        ]
    except hw.HidError as exc:
        row.message, row.source = "设备可能休眠或未回复", "雷蛇电量暂不可用"
        row.attempts.append(str(exc))
    return [row]


def read_receiver_protocol(items, group, session, cfg):
    profile = receiver_protocols.detect(items)
    row = ds.Reading(
        profile.family + ":" + device_key(group),
        hw.pick_device_name(items),
        "无线键盘" if profile.kind == "keyboard" else "无线鼠标",
        items[0].vid_pid,
        receiver=profile.receiver,
        kind=profile.kind,
        protocol=profile.family,
        connection="2.4G 无线" if profile.receiver else "USB / 连接方式未确认",
        verification="公开协议适配；本机未实测此型号",
        model=profile.name,
    )
    try:
        status = receiver_protocols.read_status(items, profile)
        row.level, row.charging, row.voltage = (
            status.level,
            status.charging,
            status.voltage,
        )
        row.raw_level, row.display_band = status.raw, status.band
        row.source, row.message = profile.family + " 只读电量协议", "已读取设备电量"
        if status.band:
            row.attempts.append("设备只上报 0–4 粗略档位，不换算成精确百分比")
    except hw.HidError as exc:
        row.message, row.source = "设备已发现，暂无法读取电量", "电量未取得"
        row.attempts.append(str(exc))
    return [row]


def read_darkrim(items, group, session, cfg):
    from hid_transport import serialized

    row = ds.Reading(
        "darkrim:" + device_key(group),
        hw.pick_device_name(items),
        "无线耳机",
        items[0].vid_pid,
        receiver=True,
        kind="headset",
        protocol="darkrim",
        verification="原项目协议适配；本机未实测此型号",
    )
    with serialized(items):
        for iface in items:
            if not _darkrim([iface]):
                continue
            level, error = darkrim_proto.read_battery_only(iface)
            if level is not None:
                row.level, row.source, row.message = (
                    level,
                    "DARKRIM TLV 电池字段",
                    "已读取电量",
                )
                return [row]
            row.attempts.append(error)
    row.message = "接收器已发现；耳机休眠、未回复或报文无效"
    return [row]


def read_aula(items, group, session, cfg):
    row = ds.Reading(
        "aula:" + device_key(group),
        hw.pick_device_name(items),
        "无线键盘",
        items[0].vid_pid,
        receiver=True,
        kind="keyboard",
        protocol="aula",
        verification="公开 GET_DEVICE_INFO 协议适配；本机未实测",
    )
    try:
        status = aula_proto.read_status(items)
        row.level, row.charging = status.level, status.charging
        row.source, row.message = "AULA / AJAZZ 设备信息", "已读取键盘电量"
    except hw.HidError as exc:
        row.message = "键盘已发现，暂无法读取电量"
        row.attempts.append(str(exc))
    return [row]


ADAPTERS = (
    Adapter("zowie", _zowie, read_zowie),
    Adapter("edifier", _edifier, read_edifier),
    Adapter(
        "razer",
        lambda items: any(
            d.vid == 0x1532 and d.pid in razer_proto.MODELS for d in items
        ),
        read_razer,
    ),
    Adapter(
        "mchose",
        mchose_proto.matches,
        lambda items, group, session, cfg: [
            adapters.read_mchose(items, group, session.mchose_last)
        ],
    ),
    Adapter(
        "keyboard",
        keyboard_proto.detect,
        lambda items, group, session, cfg: [
            adapters.read_keyboard(items, group, keyboard_proto.detect(items))
        ],
    ),
    Adapter(
        "logitech",
        logitech_proto.matches,
        lambda items, group, session, cfg: adapters.read_logitech(items, group),
    ),
    Adapter("receiver-protocol", receiver_protocols.detect, read_receiver_protocol),
    Adapter("darkrim", _darkrim, read_darkrim),
    Adapter("aula", aula_proto.matches, read_aula),
)


def select_adapter(items):
    return next((adapter for adapter in ADAPTERS if adapter.matches(items)), None)


def is_candidate(items):
    if not items:
        return False
    name = " ".join(getattr(d, "product", "") for d in items).lower()
    if "virtual" in name:
        return False
    return bool(
        select_adapter(items)
        or vendor_candidate(items)
        or any(
            word in name
            for word in (
                "wireless",
                "receiver",
                "dongle",
                "2.4g",
                "无线",
                "接收器",
                "mchose",
                "迈从",
                "dareu",
                "达尔优",
                "royal kludge",
            )
        )
        or has_battery_field(items)
    )


class PollSession:
    """Owns all mutable state. Independent receivers can poll concurrently.

    A session serializes overlapping scans. Each worker handles one physical
    receiver; device-specific cross-process mutexes protect query/reply pairs.
    """

    def __init__(self, workers=4):
        self.workers = max(1, min(8, workers))
        self.lock = threading.RLock()
        self.zowie_lock = threading.Lock()
        self.zowie_states = {}
        self.mchose_last = {}
        self.zowie_count = 0

    def clear(self):
        with self.lock:
            self.zowie_states.clear()
            self.mchose_last.clear()

    def _read_group(self, group, items, cfg):
        adapter = None
        try:
            if not is_candidate(items):
                return []
            adapter = select_adapter(items)
            rows = (
                adapter.read(items, group, self, cfg)
                if adapter
                else [adapters.read_generic(items, group)]
            )
            # A failed vendor read may still have a real standard battery field.
            # Zowie never borrows a generic percentage or another LED policy.
            if (
                adapter
                and adapter.name not in ("zowie", "logitech")
                and all(r.stale or not r.available for r in rows)
            ):
                sample = standard_status(items)
                if sample:
                    rows[0].level, rows[0].raw_level = sample.level, sample.raw_level
                    rows[0].source, rows[0].message = (
                        sample.source,
                        "已读取标准 HID 电量",
                    )
                    rows[0].attempts.append(sample.detail)
                    rows[0].stale, rows[0].sampled_at = False, 0
                    rows[0].online, rows[0].charging = None, None
        except Exception as exc:
            rows = [
                ds.Reading(
                    (adapter.name if adapter else "hid") + ":" + device_key(group),
                    hw.pick_device_name(items),
                    "外设",
                    items[0].vid_pid,
                    protocol=adapter.name if adapter else "",
                    message="读取失败",
                    connection="USB / 连接方式未确认",
                    attempts=[str(exc)],
                )
            ]
        now = time.time()
        for row in rows:
            row.checked_at = now
            row.receiver_key = device_key(group) if row.receiver else ""
            row.interfaces = row.interfaces or [ds.interface_info(d) for d in items]
        return rows

    def read(self, interfaces):
        with self.lock:
            groups = physical_groups(interfaces)
            active = {group for group, _ in groups}
            for cache in (self.zowie_states, self.mchose_last):
                for group in set(cache) - active:
                    del cache[group]
            self.zowie_count = sum(_zowie(items) for _, items in groups)
            cfg = tc.load_config()
            if len(groups) < 2 or self.workers == 1:
                batches = [
                    self._read_group(group, items, cfg) for group, items in groups
                ]
            else:
                with ThreadPoolExecutor(
                    max_workers=self.workers, thread_name_prefix="PULSE HID"
                ) as pool:
                    batches = list(
                        pool.map(lambda pair: self._read_group(*pair, cfg), groups)
                    )
            rows = [row for batch in batches for row in batch]
            return sorted(
                rows,
                key=lambda r: (
                    {"zowie": 0, "edifier": 1, "razer": 2}.get(r.protocol, 3),
                    r.name,
                    r.key,
                ),
            )


default_session = PollSession()
# Compatibility aliases for existing integrations; new callers can own a session.
_zowie_states = default_session.zowie_states
_mchose_last = default_session.mchose_last
read_devices = default_session.read
read_generic = adapters.read_generic
read_logitech = adapters.read_logitech
read_keyboard = adapters.read_keyboard
