"""Physical grouping and stable private device identities."""

import hashlib
from collections import defaultdict
import hid_win as hw


def physical_groups(interfaces):
    groups = defaultdict(list)
    for iface in interfaces:
        group = getattr(iface, "group", "")
        if not group:
            group = hw.physical_group_key(iface.path)
            if getattr(iface, "serial", ""):
                group += ":" + iface.serial
        groups[group].append(iface)
    return sorted(groups.items())


def device_key(group):
    # Stable local identity without exporting a serial or Windows Container ID.
    return hashlib.sha256(group.encode("utf8")).hexdigest()[:20]


def kind_of(interfaces):
    usages = {(d.usage_page, d.usage) for d in interfaces}
    name = " ".join(getattr(d, "product", "") for d in interfaces).lower()
    if any(token in name for token in ("mouse", "鼠标")):
        return "mouse", "鼠标"
    if any(token in name for token in ("keyboard", "键盘", "royal kludge")):
        return "keyboard", "键盘"
    if (1, 2) in usages and (1, 6) in usages:
        return "device", "键鼠组合接收器"
    if (1, 2) in usages:
        return "mouse", "鼠标"
    if (1, 6) in usages:
        return "keyboard", "键盘"
    if (1, 5) in usages or (1, 4) in usages:
        return "gamepad", "游戏控制器"
    return "device", "无线外设"
