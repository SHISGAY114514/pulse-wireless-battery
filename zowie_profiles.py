"""Model-specific firmware evidence. No generic battery discharge curve.

USB 04A5:800A and a version number cannot identify the paired mouse model.
Keep unverified models visible, but never borrow another family's calibration.
See docs/ZOWIE_SUPPORT.md and docs/firmware-evidence.json.
"""
from dataclasses import dataclass, replace
from functools import lru_cache
import math


@dataclass(frozen=True)
class Profile:
    model: str
    family: str
    receiver_pid: int
    firmwares: tuple[str, ...] = ()
    thresholds: tuple[int, int, int] | None = None
    middle: int = 0
    high: int = 0
    evidence: str = ""
    transport: str = "dw"


PROFILES: dict[str, Profile] = {}
for model in ("EC1-DW", "EC2-DW", "EC3-DW", "FK2-DW", "ZA13-DW", "S2-DW", "U2-DW"):
    family = "ECDW" if model.startswith("EC") else {
        "FK2-DW": "FK", "ZA13-DW": "ZA13", "S2-DW": "S2", "U2-DW": "U2DW"}[model]
    ec = family == "ECDW"
    PROFILES[model] = Profile(
        model, family, 0x800A, ("2524", "2526"),
        (3398, 3472, 3590) if ec else (3398, 3446, 3510),
        376 if ec else 374, 390 if ec else 381,
        "固件离线验证" + ("；EC2-DW / 2524 已实机读取" if model == "EC2-DW" else "；此型号未实机验证"))

PROFILES["U2"] = Profile("U2", "U2", 0x800A, ("2511",), (3398, 3446, 3510),
                         374, 381, "固件离线验证；需 ER2-80 4K 接收器；未实机验证")
for model in ("EC1-CW", "EC2-CW", "EC3-CW"):
    PROFILES[model] = Profile(model, "ECCW", 0x8006, evidence="缺少固件；实验性电压查询，不显示档位", transport="cw")
for model in ("FK1-DW", "ZA12-DW"):
    PROFILES[model] = Profile(model, "unverified", 0x800A, evidence="未取得该型号固件；仅读取电压")
for base in ("EC1-DW", "EC2-DW", "EC3-DW", "FK2-DW", "ZA13-DW", "S2-DW", "U2-DW"):
    model = base + " Glossy"
    # Official *-WH download pages all point to the same 2526 updater, whose
    # model/PID table has one binary per family. Older Glossy firmware unknown.
    PROFILES[model] = replace(PROFILES[base], model=model, firmwares=("2526",),
                              evidence="官网 WH 版共用 2526 固件；离线验证，未实机验证")

MODEL_CHOICES = ("auto", *PROFILES)


def get_profile(model: str) -> Profile | None:
    return PROFILES.get(model)


def valid_voltage(voltage):
    return isinstance(voltage, (int, float)) and math.isfinite(voltage) and 3 < voltage < 5


@lru_cache(maxsize=16)
def inverse_map(thresholds):
    """All possible LED classes for each quantized voltage, including correction."""
    lookup: dict[int, set[int]] = {}
    for adc in range(4096):
        encoded = max(300, min(500, (adc * 0x3CA50 // 0x38000) // 10))
        band = sum(adc > boundary for boundary in thresholds)
        for raw in (encoded, 370 if encoded in (368, 369) else encoded):
            lookup.setdefault(raw, set()).add(band)
    return lookup


def voltage_band(voltage, firmware, model):
    profile = get_profile(model)
    if (not profile or not profile.thresholds or firmware not in profile.firmwares
            or not valid_voltage(voltage)):
        return ""
    bands = inverse_map(profile.thresholds).get(round(voltage * 100), set())
    labels = {0: "低电量提示档", 1: "一格", 2: "两格", 3: "三格"}
    return " / ".join(labels[b] for b in sorted(bands)) + ("（边界）" if len(bands) > 1 else "") if bands else ""


def display_band(voltage, firmware, previous, model):
    profile = get_profile(model)
    if (not profile or not profile.thresholds or firmware not in profile.firmwares
            or not valid_voltage(voltage)):
        return ""
    raw = round(voltage * 100)
    mid, high = profile.middle, profile.high
    # Preserve EC2-DW's observed boundary policy. Other families promote only
    # after the cached value is unambiguously on the upper side of the ADC cut.
    if previous == "高档" and raw >= high - 1:
        return "高档"
    if previous == "低档" and raw <= mid:
        return "低档"
    if previous == "中档" and mid - 1 <= raw <= high - 1:
        return "中档"
    return "高档" if raw >= high else "中档" if raw >= mid else "低档"


def policy_text(model):
    p = get_profile(model)
    if not p or not p.thresholds:
        return "缺少该型号的固件电量灯映射；不推断高中低档"
    m, h = p.middle, p.high
    return (f"首次分档：≤{(m-1)/100:.2f} V 低 / {m/100:.2f}–{(h-1)/100:.2f} V 中 / ≥{h/100:.2f} V 高；"
            f"缓冲：低→中 ≥{(m+1)/100:.2f}，中→低 ≤{(m-2)/100:.2f}，"
            f"中→高 ≥{h/100:.2f}，高→中 ≤{(h-2)/100:.2f} V")
