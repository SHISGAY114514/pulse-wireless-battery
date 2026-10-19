"""EC2-DW receiver cached-voltage reader, independently implemented.

Protocol cross-reference: github.com/0Mercy/U2-dw-battery-monitor.
Our 04A5:800A hardware confirms receiver 8511, EC2-DW mouse 2524.
No capacity percentage or charging flag is supplied by these receiver replies.
"""
from __future__ import annotations
import ctypes
import math
from ctypes import wintypes
from contextlib import contextmanager
from dataclasses import dataclass
import hid_win as hw

K=hw.kernel32
K.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
K.CreateMutexW.restype=wintypes.HANDLE
K.ReleaseMutex.argtypes=[wintypes.HANDLE]
K.ReleaseMutex.restype=wintypes.BOOL

@contextmanager
def serialized():
    # Share the public project's lock name to coordinate cooperating readers.
    mutex=K.CreateMutexW(None,False,r'Local\ZowieU2DW.HidQuery.v1')
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    owned=False
    try:
        status=K.WaitForSingleObject(mutex,1500)
        if status not in (0,0x80):
            raise hw.HidError('卓威私有接口正被其他读取器使用')
        owned=True
        yield
    finally:
        if owned: K.ReleaseMutex(mutex)
        K.CloseHandle(mutex)

def parse_reply(command,packet):
    if command not in (0x40,0x25,0x15):
        raise ValueError('仅允许已确认的三个只读命令')
    marker=3 if command==0x25 else 4
    if (not packet or len(packet)!=16 or packet[0]!=9 or packet[marker]!=0xc8
            or any(packet[marker+1:]) or (marker==4 and packet[3]!=0)):
        raise hw.HidError('卓威回复格式或成功标记不正确')
    if command==0x15:
        value=int.from_bytes(packet[1:3],'big')
        if not 300<value<500:
            raise hw.HidError('卓威电压缓存为空、饱和或超出有效范围')
        return value
    if any(nibble>9 for b in packet[1:3] for nibble in (b>>4,b&15)):
        raise hw.HidError('卓威固件版本格式不正确')
    return packet[1:3].hex()

@dataclass
class Status:
    voltage: float | None
    mouse_firmware: str
    receiver_firmware: str
    linked: bool
    reports: list[str]

def voltage_band(voltage,mouse_firmware):
    """Infer an LED band, not a capacity percentage, from verified EC images.

    2524 and 2526 EC-DW use ADC boundaries 3398/3472/3590. Quantization
    and firmware correction can map different ADCs to the same cached voltage.
    U2-DW uses different boundaries; its calibration must not be reused here.
    """
    if mouse_firmware not in ('2524','2526'):
        return ''
    raw=round(voltage*100)
    bands=set()
    for adc in range(4096):
        encoded=max(300,min(500,(adc*0x3ca50//0x38000)//10))
        if raw not in (encoded,370 if encoded in (368,369) else encoded):
            continue
        bands.add(0 if adc<=3398 else 1 if adc<=3472 else 2 if adc<=3590 else 3)
    labels={0:'低电量提示档',1:'一格',2:'两格',3:'三格'}
    return ' / '.join(labels[b] for b in sorted(bands)) + ('（边界）' if len(bands)>1 else '') if bands else ''

def display_band(voltage,mouse_firmware,previous=''):
    """Conservative high/mid/low indication; never a capacity percentage.

    EC firmware changes one/two LEDs around 3.76 V and two/three around
    3.888 V. Cached voltage has 0.01 V resolution. The user confirms mid
    at 3.88 V; reserve >=3.90 V for high rather than promote the boundary.
    A one-step dead band holds the previous indication during small changes.
    State is cleared on unavailable data, not carried across disconnects.
    """
    if (mouse_firmware not in ('2524','2526') or voltage is None
            or not math.isfinite(voltage) or not 3.00<voltage<5.00):
        return ''
    raw=round(voltage*100)
    if previous=='高档' and raw>=389:
        return '高档'
    if previous=='低档' and raw<=376:
        return '低档'
    if previous=='中档' and 375<=raw<=389:
        return '中档'
    return '高档' if raw>=390 else '中档' if raw>=376 else '低档'

def read_status(interfaces):
    matches=[d for d in interfaces if (d.vid,d.pid)==(0x04a5,0x800a)]
    outputs=[d for d in matches if d.usage_page==0xff03 and d.output_len==16]
    inputs=[d for d in matches if d.usage_page==0xff04 and d.input_len==16]
    if len(outputs)!=1 or len(inputs)!=1:
        raise hw.HidError('需要唯一一组卓威 FF03/FF04 接口')
    reports=[]
    with serialized(),hw.HidDevice(inputs[0].path) as reader,hw.HidDevice(outputs[0].path,write=True) as writer:
        def query(command):
            reader.drain()
            # WriteFile reaches the interrupt-OUT handler; SetOutputReport did not.
            writer.write_output(bytes([8,0xb4,command])+bytes(13),use_hidd=False)
            packet=reader.read_input(timeout_ms=900)
            value=parse_reply(command,packet)
            reports.append(f'{command:02X}: {packet.hex(" ")}')
            return value
        receiver=query(0x40)
        if receiver!='8511':
            raise hw.HidError(f'接收器固件 {receiver} 尚未验证')
        before=query(0x25)
        if before=='0000':
            return Status(None,before,receiver,False,reports)
        raw=query(0x15)
        after=query(0x25)
        if after=='0000' or before!=after:
            return Status(None,after,receiver,False,reports)
        return Status(raw/100,after,receiver,True,reports)
