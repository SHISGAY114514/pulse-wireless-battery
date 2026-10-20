"""Read-only MCHOSE mouse identity/battery protocol (Feature 11, command 06).

Protocol facts: alexfrih/mchose-linux and JKWTCN/BatteryMonitor. This module
does not expose any setting, pairing or firmware command.
"""
from dataclasses import dataclass
import time
import hid_win as hw
from zowie_proto import serialized

IDENTITIES={(0x5253,p) for p in (0x0011,0x0031,0x00b0,0x1020,0x100a,0x100b)} | {
    (0x3837,p) for p in (0x4018,0x4023,0x4019,0x4021,0x100a,0x100b)}


def matches(interfaces):
    return any((d.vid,d.pid) in IDENTITIES for d in interfaces)


def request(report_id=0x11):
    if report_id not in (0x11,0x12):raise ValueError('只允许迈从状态查询报告')
    return bytes([report_id,0xf9])+b'\xff'*(19 if report_id==0x11 else 63)


@dataclass(frozen=True)
class Status:
    level: int
    charging: bool | None
    logical_vid: int
    logical_pid: int
    firmware: str
    flags: int


def parse_reply(packet):
    if len(packet)<13 or packet[0] not in (0x11,0x12):
        raise hw.HidError('迈从回复长度或报告 ID 无效')
    data=bytes(x^255 for x in packet[1:13])
    if data[0]!=6:raise hw.HidError('迈从回复命令不匹配')
    vid=int.from_bytes(data[1:3],'little')
    pid=int.from_bytes(data[3:5],'little')
    if vid not in (0x5253,0x3837) or pid in (0,0xffff):
        raise hw.HidError('接收器未返回有效鼠标身份，鼠标可能休眠或断开')
    if data[10]>100:raise hw.HidError('迈从电量字段超出 0–100 范围')
    return Status(data[10],bool(data[11]) if data[11] in (0,1) else None,
                  vid,pid,'.'.join(str(x) for x in data[5:9]),data[9])


def read_status(interfaces):
    # FF0B siblings declare reports 2A/2B, NOT report 11. Never send there.
    candidates=[d for d in interfaces if (d.vid,d.pid) in IDENTITIES
                and d.usage_page==0xff01 and d.feature_len>=21]
    errors=[]
    with serialized():
        for iface in candidates:
            try:
                with hw.HidDevice(iface.path) as dev:
                    reports={c.ReportID for c in dev.value_caps(hw.HidP_Feature)}
                    for rid in (0x11,0x12):
                        if rid not in reports:continue
                        for _ in range(4):
                            try:
                                query=request(rid)
                                dev.set_feature_report(query,allow_zero_access=True)
                                time.sleep(.06)
                                return parse_reply(dev.get_feature_report(rid,len(query)))
                            except hw.HidError as exc:
                                errors.append(str(exc))
                                time.sleep(.06)
            except hw.HidError as exc:errors.append(str(exc))
    raise hw.HidError(errors[-1] if errors else '未找到声明 Feature 11 的迈从接口')
