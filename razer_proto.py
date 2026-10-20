"""Independent read-only Razer battery implementation. Protocol facts: OpenRazer.

Only class 07 commands 80 (battery) and 84 (charge) can be constructed here.
90-byte vendor message + Windows report-ID prefix, no settings or RGB writes.
"""
from dataclasses import dataclass
from functools import reduce
import operator
import time
import hid_win as hw
from hid_transport import serialized


@dataclass(frozen=True)
class Model:
    name: str
    transaction: int = 0x1F
    receiver: bool = True
    rechargeable: bool = True
    kind: str = 'mouse'


MODELS = {
    0x00B7: Model('Razer DeathAdder V3 Pro'),
    0x00B6: Model('Razer DeathAdder V3 Pro',receiver=False),
    0x00C3: Model('Razer DeathAdder V3 Pro'),
    0x00C2: Model('Razer DeathAdder V3 Pro',receiver=False),
    0x00C5: Model('Razer DeathAdder V3 HyperSpeed'),
    0x00C4: Model('Razer DeathAdder V3 HyperSpeed',receiver=False),
    0x00C1: Model('Razer Viper V3 Pro'),
    0x00C0: Model('Razer Viper V3 Pro',receiver=False),
    0x00A6: Model('Razer Viper V2 Pro'),
    0x00A5: Model('Razer Viper V2 Pro',receiver=False),
    0x00AB: Model('Razer Basilisk V3 Pro'),
    0x00AA: Model('Razer Basilisk V3 Pro',receiver=False),
    0x00CD: Model('Razer Basilisk V3 Pro 35K'),
    0x00CC: Model('Razer Basilisk V3 Pro 35K',receiver=False),
    0x00B0: Model('Razer Cobra Pro'),
    0x00AF: Model('Razer Cobra Pro',receiver=False),
    0x00A8: Model('Razer Naga V2 Pro'),
    0x00A7: Model('Razer Naga V2 Pro',receiver=False),
    0x0090: Model('Razer Naga Pro'),
    0x008F: Model('Razer Naga Pro',receiver=False),
    0x0088: Model('Razer Basilisk Ultimate'),
    0x0086: Model('Razer Basilisk Ultimate',receiver=False),
    0x007B: Model('Razer Viper Ultimate',0xFF),
    0x007A: Model('Razer Viper Ultimate',0xFF,False),
    0x007D: Model('Razer DeathAdder V2 Pro',0x3F),
    0x007C: Model('Razer DeathAdder V2 Pro',0x3F,False),
    0x0083: Model('Razer Basilisk X HyperSpeed',0xFF,rechargeable=False),
    0x009C: Model('Razer DeathAdder V2 X HyperSpeed',rechargeable=False),
    0x00B8: Model('Razer Viper V3 HyperSpeed',rechargeable=False),
    0x00B9: Model('Razer Basilisk V3 X HyperSpeed',rechargeable=False),
    0x00B4: Model('Razer Naga V2 HyperSpeed',rechargeable=False),
}

# Same battery commands, keyboard-specific transaction IDs (OpenRazer).
for wired,wireless,name,wired_transaction in (
    (0x025a,0x025c,'BlackWidow V3 Pro',0x3f),
    (0x0258,0x0271,'BlackWidow V3 Mini HyperSpeed',0x1f),
    (0x0292,0x0290,'DeathStalker V2 Pro',0x1f),
    (0x0298,0x0296,'DeathStalker V2 Pro TKL',0x1f),
    (0x02b9,0x02ba,'BlackWidow V4 Mini HyperSpeed',0x1f),
    (0x02d7,0x02d5,'BlackWidow V4 Tenkeyless HyperSpeed',0x1f),
):
    MODELS[wired]=Model('Razer '+name,wired_transaction,False,kind='keyboard')
    MODELS[wireless]=Model('Razer '+name,0x9f,kind='keyboard')


def checksum(message):
    return reduce(operator.xor,message[2:88],0)


def request(command,transaction):
    if command not in (0x80,0x84):
        raise ValueError('仅允许雷蛇电量和充电状态的只读查询')
    data=bytearray(90)
    data[1],data[5],data[6],data[7]=transaction,2,7,command
    data[88]=checksum(data)
    return b'\0'+data


def parse_reply(packet,command,transaction):
    if (not packet or len(packet)!=91 or packet[0]!=0):
        raise hw.HidError('雷蛇 Feature 回复长度或报告 ID 不正确')
    data=packet[1:]
    if (data[1]!=transaction or any(data[2:5]) or data[5]!=2 or data[6:8]!=bytes([7,command])
            or data[88]!=checksum(data) or data[89]!=0):
        raise hw.HidError('雷蛇回复的命令、事务或校验和不匹配')
    if data[0]!=2:
        raise hw.HidError({1:'雷蛇接口忙，请稍后刷新',3:'雷蛇查询失败',
                           4:'雷蛇鼠标未回复（可能休眠或未连接）',5:'雷蛇设备不支持此查询'}.get(data[0],'雷蛇回复状态无效'))
    value=data[9]
    if command==0x84 and value not in (0,1):
        raise hw.HidError('雷蛇充电标记无效')
    return value


@dataclass
class Status:
    level: int
    charging: bool | None
    raw: int
    notes: list[str]


def read_status(interfaces):
    candidates=[d for d in interfaces if d.vid==0x1532 and d.pid in MODELS and d.feature_len==91]
    if not candidates:
        raise hw.HidError('未找到雷蛇 91 字节 Feature 接口')
    model=MODELS[candidates[0].pid]
    if model.kind=='keyboard':
        preferred=3 if candidates[0].pid in (0x0258,0x0271,0x02b9,0x02ba,0x0292,0x0298,0x02d7) else 2
        candidates.sort(key=lambda d:f'mi_{preferred:02x}' not in d.path.lower())
    elif len(candidates)!=1:
        raise hw.HidError('需要唯一的雷蛇鼠标 91 字节 Feature 接口')
    errors=[]
    for iface in candidates:
        try:return _read_interface(iface)
        except hw.HidError as exc:errors.append(str(exc))
    raise hw.HidError(errors[-1])


def _read_interface(iface):
    model=MODELS[iface.pid]
    notes=[]
    with serialized([iface]), hw.HidDevice(iface.path) as dev:
        def query(command):
            last=None
            for _ in range(2):
                dev.set_feature_report(request(command,model.transaction),allow_zero_access=True)
                time.sleep(.065)
                try:
                    return parse_reply(dev.get_feature_report(0,91),command,model.transaction)
                except hw.HidError as exc:
                    last=exc
            raise last
        raw=query(0x80)
        charging=None
        if model.rechargeable:
            try:charging=bool(query(0x84))
            except hw.HidError as exc:notes.append(str(exc))
    return Status(round(raw*100/255),charging,raw,notes)
