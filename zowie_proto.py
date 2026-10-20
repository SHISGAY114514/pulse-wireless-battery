"""DW receiver cached-voltage reader, independently implemented.

Protocol cross-reference: github.com/0Mercy/U2-dw-battery-monitor.
Our 04A5:800A hardware confirms receiver 8511, EC2-DW mouse 2524.
No capacity percentage or charging flag is supplied by these receiver replies.
"""
from __future__ import annotations
import ctypes
import zowie_profiles as profiles
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

def voltage_band(voltage, mouse_firmware, model='EC2-DW'):
    return profiles.voltage_band(voltage, mouse_firmware, model)


def display_band(voltage, mouse_firmware, previous='', model='EC2-DW'):
    return profiles.display_band(voltage, mouse_firmware, previous, model)


def parse_cw_voltage(packet):
    """Experimental public EC-CW format; no capacity or LED calibration inferred."""
    if packet and len(packet) == 65 and packet[0] == 0:
        packet = packet[1:]
    if not packet or len(packet) != 64 or packet[3] != 0xEC or packet[2] > 100:
        raise hw.HidError('EC-CW 回复格式未匹配已公开的电压协议')
    mv = int.from_bytes(packet[:2], 'little')
    if mv == 0:
        return None
    if not 3000 < mv < 5000:
        raise hw.HidError('EC-CW 电压无效')
    return mv / 1000


def read_cw_voltage(interfaces):
    # Only selected CW profiles may reach this experimental read request.
    candidates = [d for d in interfaces if (d.vid, d.pid) == (0x04A5, 0x8006)
                  and d.usage_page >= 0xFF00 and d.input_len in (64, 65) and d.output_len in (64, 65)]
    if len(candidates) != 1:
        raise hw.HidError('需要唯一的 EC-CW 厂商接口（64 / 65 字节）；不猜测其他接口')
    iface = candidates[0]
    payload = bytes([0xA5, 0x03]).ljust(64, b'\0')
    if iface.output_len == 65:
        payload = b'\0' + payload  # Windows prefix for unnumbered HID reports.
    with serialized(), hw.HidDevice(iface.path) as reader, hw.HidDevice(iface.path, write=True) as writer:
        reader.drain()
        writer.write_output(payload, use_hidd=False)
        voltage = parse_cw_voltage(reader.read_input(timeout_ms=900))
    return voltage, ['仅发送公开只读请求 A5 03；EC-CW Windows 传输尚待实机验证']


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
        if receiver not in ('8511', '8507'):
            raise hw.HidError(f'接收器固件 {receiver} 尚未验证')
        before=query(0x25)
        if before=='0000':
            return Status(None,before,receiver,False,reports)
        raw=query(0x15)
        after=query(0x25)
        if after=='0000' or before!=after:
            return Status(None,after,receiver,False,reports)
        return Status(raw/100,after,receiver,True,reports)
