"""Allowlisted, read-only wireless keyboard protocols.

Wire-format references are documented in docs/KEYBOARD_SUPPORT.md. No keymap,
lighting, reset, pairing or firmware commands are available here.
"""
from dataclasses import dataclass
import time
import hid_win as hw
from hid_transport import serialized


@dataclass(frozen=True)
class Profile:
    code: str
    name: str
    receiver: bool
    connection: str
    percent_offset: int = 6


ROG={
    0x193c:('ROG Falchion',False,11),
    0x193e:('ROG Falchion',True,11),
    0x1b04:('ROG Falchion RX Low Profile',False,11),
    0x196b:('ROG Claymore II',False,6),
    0x1a07:('ROG Strix Scope RX TKL Wireless',False,6),
    0x1aae:('ROG Strix Scope II 96 Wireless',False,6),
    0x1b78:('ROG Strix Scope II 96 RX Wireless',False,6),
    0x1a83:('ROG Azoth',False,6),
    0x1a85:('ROG Azoth',True,6),
    0x1b3f:('ROG Azoth Extreme',False,6),
    0x1cef:('ROG Azoth Extreme SE',False,6),
    0x1c24:('ROG Azoth X',False,6),
    0x1c25:('ROG Azoth X',True,6),
    # Omni multiplexes a mouse and keyboard. Channel 2 is keyboard-only.
    0x1ace:('ROG Omni 无线键盘',True,6),
}


def detect(items):
    for d in items:
        if d.vid==0x0b05 and d.pid in ROG:
            name,receiver,offset=ROG[d.pid]
            return Profile('rog',name,receiver,'2.4G 无线' if receiver else 'USB / 2.4G（模式待确认）',offset)
        if (d.vid,d.pid)==(0x3837,0x3033):
            return Profile('k99v3','MCHOSE K99 V3',True,'2.4G 无线')
        if ((d.vid,d.pid)==(0x05ac,0x024f) and
                any(x.usage_page==0xff02 and x.output_len==20 for x in items)):
            return Profile('k99','Compx / MCHOSE K99 协议键盘',True,'2.4G 无线')
        if d.vid==0x260d and d.pid in (0x0101,0x0042):
            return Profile('dareu','DAREU TK51G / EK75' if d.pid==0x0101 else 'DAREU 无线键盘接收器',
                           d.pid==0x0042,'2.4G 无线' if d.pid==0x0042 else 'USB 有线')
        if (d.vid,d.pid)==(0x25a7,0xfa70):
            # Shared Compx identity, not an automatic RK model identifier.
            return Profile('compx-rk','Compx 2.4G 无线键盘',True,'2.4G 无线')
        if (d.vid,d.pid)==(0x258a,0x00c1):
            return Profile('rk98-wired','RK98 / R98（00C1）',False,'USB 有线')
    return None


@dataclass(frozen=True)
class Status:
    level: int
    charging: bool | None = None
    note: str = ''


def output_query(iface,packet,parse,timeout=.6):
    if not iface.output_len or len(packet)!=iface.output_len:
        raise hw.HidError('键盘输出报告长度与描述符不符')
    with hw.HidDevice(iface.path,write=True) as dev:
        # Create the dedicated reader before sending to avoid dropping a quick reply.
        dev.drain()
        dev.write_output(packet)
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            raw=dev.read_input(timeout_ms=max(1,min(100,int((deadline-time.monotonic())*1000))))
            if not raw:continue
            try:return parse(raw)
            except hw.HidError:continue
    raise hw.HidError('键盘未返回匹配的电量回复，可能休眠或协议版本不同')


def parse_rog(raw,rid,offset):
    if len(raw)<max(12,offset+1) or raw[0]!=rid or raw[1:3]!=b'\x12\x01':
        raise hw.HidError('ROG 电源回复不匹配')
    if raw[offset]>100:raise hw.HidError('ROG 电量字段越界')
    return Status(raw[offset],bool(raw[9]) if raw[9] in (0,1) else None)


def parse_k99(raw):
    if len(raw)!=20 or raw[:2]!=b'\x13\x4a' or raw[5]>100:
        raise hw.HidError('K99 电量回复无效')
    if sum(raw[:-1])&255!=raw[-1]:raise hw.HidError('K99 回复校验和不匹配')
    return Status(raw[5],bool(raw[6]&0x10) if raw[6] in (0,1,0x10,0x11) else None)


def parse_k99v3(raw):
    if len(raw)!=65 or raw[:3]!=b'\0\xaa\x03' or raw[9]>100:
        raise hw.HidError('K99 V3 电量回复无效')
    return Status(raw[9],None,'此版本的充电字段尚未验证')


def dareu_query(dev,command,target=0):
    # Only GET battery and GET wireless slots are constructible.
    if command=='battery':head=bytes([target,3,7,0x80,0,0])
    elif command=='slots' and target==0:head=b'\0\x07\0\xa0\0\0'
    else:raise ValueError('只允许达尔优电量和连接槽只读查询')
    dev.set_feature_report(b'\0'+head+bytes(64-len(head)),allow_zero_access=True)
    for _ in range(12):
        time.sleep(.02)
        raw=dev.get_feature_report(0,65)
        if len(raw)!=65 or raw[0]!=0:continue
        data=raw[1:]
        if data[0]&15==2 and data[2:4]==head[2:4] and data[0]&0xf0 in (0,target):
            return data
    raise hw.HidError('达尔优未返回匹配的就绪状态')


def parse_dareu_battery(data):
    if len(data)!=64 or data[0]&15!=2 or data[2:4]!=b'\x07\x80' or data[1]<4:
        raise hw.HidError('达尔优电池回复无效')
    level,maximum=data[7:9]
    if not maximum or level>maximum:raise hw.HidError('达尔优电池逻辑范围无效')
    return Status(round(level*100/maximum),None,'按设备 Level / MaxLevel 换算，充电状态未验证')


def read_status(items,profile):
    if profile.code=='compx-rk':
        raise hw.HidError('已识别共享型号的键盘接收器；Feature 06 的 8 字节与电量灯无已验证映射。RK98 候选固件已找到内部灯格变量，但尚未找到 USB 读取入口及电压阈值；暂不能自动读取电量，请用 Fn+Enter 查看实体灯。')
    if profile.code=='rk98-wired':
        versions='/'.join(sorted({f'{d.version:04X}' for d in items if getattr(d,'version',0)})) or '未知'
        raise hw.HidError(f'当前 USB 版本为 {versions}，已分析的官方 00C1 候选版本为 1024；候选固件由无线芯片上报电量步数，尚未获得电压阈值或已验证的 USB 电量查询。未发送 Report 05 或猜测的厂商命令。')
    errors=[]
    with serialized(items):
        for d in items:
            try:
                if profile.code=='rog':
                    omni=d.pid==0x1ace
                    if omni:
                        if 'mi_02&col02' not in d.path.lower():continue
                        rid=2
                    else:
                        if d.usage_page!=0xff00 or d.usage!=1:continue
                        rid=0
                    if d.output_len!=65 or d.input_len!=65:continue
                    packet=bytes([rid,0x12,1])+bytes(62)
                    # Omni's keyboard model is ambiguous: require primary and
                    # duplicate percentage to agree before using the shared rule.
                    def parse(raw):
                        if omni and (len(raw)<12 or raw[6]!=raw[11]):
                            raise hw.HidError('Omni 键盘电量字段不一致，需确认型号')
                        return parse_rog(raw,rid,profile.percent_offset)
                    return output_query(d,packet,parse)
                if profile.code=='k99':
                    if d.usage_page!=0xff02 or d.output_len!=20 or d.input_len!=20:continue
                    head=b'\x13\x4a\x01'+bytes(16)
                    return output_query(d,head+bytes([sum(head)&255]),parse_k99)
                if profile.code=='k99v3':
                    if (d.usage_page,d.usage)!=(1,0) or d.output_len!=65 or d.input_len!=65:continue
                    packet=b'\0\x55\x03\0\x38\x38'+bytes(59)
                    return output_query(d,packet,parse_k99v3)
                if profile.code=='dareu':
                    if d.usage_page!=0xff00 or d.feature_len!=65:continue
                    with hw.HidDevice(d.path) as dev:
                        target=0
                        if d.pid==0x0042:
                            slots=dareu_query(dev,'slots')
                            count=slots[6]
                            if count>4:raise hw.HidError('达尔优连接槽数量无效')
                            targets=[(i+1)<<4 for i in range(count)
                                     if slots[7+3*i]==1 and slots[8+3*i:10+3*i]==b'\x01\x01']
                            if len(targets)!=1:raise hw.HidError('未找到唯一的已适配 EK75 键盘连接槽')
                            target=targets[0]
                        result=parse_dareu_battery(dareu_query(dev,'battery',target))
                        if target:return Status(result.level,None,result.note+'；无线转发为实验性适配，未实测')
                        return result
            except hw.HidError as exc:errors.append(str(exc))
    raise hw.HidError(errors[-1] if errors else '未找到匹配此键盘协议的厂商接口')
