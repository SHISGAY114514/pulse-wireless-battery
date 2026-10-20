"""Descriptor-driven battery reads shared by the dashboard and debug tools.

Only declared battery usages are read. Reports are fetched once per ID, failed
fields do not hide other fields, and invalid/null values are never clamped.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
import hid_win as hw


@dataclass(frozen=True)
class Sample:
    level: int
    raw_level: int
    report: bytes
    detail: str


def battery_fields(cap):
    for page, usage, _ in hw.BATTERY_USAGES:
        if page != cap.UsagePage:
            continue
        if cap.IsRange:
            if cap.u.Range.UsageMin <= usage <= cap.u.Range.UsageMax:
                yield usage
        elif cap.u.NotRange.Usage == usage:
            yield usage


def report_types(iface):
    for kind, prefix in ((hw.HidP_Feature, "feature"), (hw.HidP_Input, "input")):
        if getattr(iface, prefix + "_len", 0) and getattr(
            iface, "num_" + prefix + "_value_caps", 0
        ):
            yield kind


def percentage(value, cap):
    if value is None:
        return None
    low, high = cap.LogicalMin, cap.LogicalMax
    # HidP_GetUsageValue returns an unsigned ULONG even for signed fields.
    bits = getattr(cap, "BitSize", 0)
    if low < 0 and 0 < bits <= 32 and value & (1 << (bits - 1)):
        value -= 1 << bits
    if high <= low or not low <= value <= high:
        return None
    return round((value - low) * 100 / (high - low))


def decode(dev, kind, report, caps):
    if not isinstance(report, bytes) or not report:
        return None
    for cap in caps:
        if report[0] != cap.ReportID:
            continue
        for usage in battery_fields(cap):
            try:
                value = dev.usage_value(kind, report, cap, usage=usage)
            except hw.HidError:
                continue
            level = percentage(value, cap)
            if level is not None:
                return Sample(
                    level,
                    value,
                    report,
                    f"HID {cap.UsagePage:04X}:{usage:04X} / Report {cap.ReportID:02X}; "
                    f"逻辑范围 {cap.LogicalMin}–{cap.LogicalMax}",
                )
    return None


def has_battery_field(interfaces):
    for iface in interfaces:
        kinds = tuple(report_types(iface))
        if not kinds:
            continue
        try:
            with hw.HidDevice(iface.path) as dev:
                if any(
                    any(battery_fields(c))
                    for kind in kinds
                    for c in dev.value_caps(kind)
                ):
                    return True
        except hw.HidError:
            continue
    return False


def read(interfaces, input_timeout=0.15):
    for iface in interfaces:
        kinds = tuple(report_types(iface))
        if not kinds:
            continue
        try:
            with hw.HidDevice(iface.path) as dev:
                for kind in kinds:
                    caps = [c for c in dev.value_caps(kind) if any(battery_fields(c))]
                    getter = (
                        dev.get_feature_report
                        if kind == hw.HidP_Feature
                        else dev.get_input_report
                    )
                    for rid in dict.fromkeys(c.ReportID for c in caps):
                        try:
                            sample = decode(dev, kind, getter(rid), caps)
                            if sample:
                                return sample
                        except hw.HidError:
                            continue
                    if kind != hw.HidP_Input or not caps:
                        continue
                    # One bounded listening window for all declared input IDs.
                    end = time.monotonic() + input_timeout
                    while time.monotonic() < end:
                        report = dev.read_input(
                            timeout_ms=max(
                                1, min(30, int((end - time.monotonic()) * 1000))
                            )
                        )
                        sample = decode(dev, kind, report, caps)
                        if sample:
                            return sample
        except hw.HidError:
            continue
    return None
