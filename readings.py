"""Shared data model; no hardware or UI dependencies."""

from dataclasses import dataclass, field, asdict
from typing import Optional


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
    receiver_key: str = ""
    stale: bool = False
    sampled_at: float = 0.0

    @property
    def available(self):
        return (
            self.level is not None
            or self.voltage is not None
            or bool(self.display_band)
        )

    @property
    def value_text(self):
        if self.display_band:
            return self.display_band
        if self.level is not None:
            return f"{self.level}%"
        if self.voltage is not None:
            return f"{self.voltage:.2f} V"
        return "--"

    def to_dict(self):
        return asdict(self)


def receiver_count(rows):
    return len({r.receiver_key or r.key for r in rows if r.present and r.receiver})


def interface_info(iface):
    return {
        "collection": (iface.path.split("#")[1] if "#" in iface.path else "HID"),
        "usage": iface.describe_usage(),
        "input": iface.input_len,
        "output": iface.output_len,
        "feature": iface.feature_len,
    }
