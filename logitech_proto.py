"""Independent read-only HID++ battery/name reader; protocol facts from Solaar.

Feature discovery is limited to battery and device name/type. Legacy access is
limited to battery registers 0D/07. Never enable notifications or alter pairing.
"""
from contextlib import ExitStack
from dataclasses import dataclass
import time
import hid_win as hw
from hid_transport import serialized

RECEIVERS={p:6 for p in (0xc52b,0xc532,0xc548)}
RECEIVERS.update({p:1 for p in (0xc518,0xc51a,0xc51b,0xc521,0xc525,0xc526,
    0xc52e,0xc52f,0xc531,0xc535,0xc537,0xc539,0xc53a,0xc53d,0xc53f,0xc541,0xc545,0xc547,0xc54d)})
RECEIVERS[0xc534]=2
SOFTWARE=0x0d


def matches(items):
    return any(d.vid==0x046d and d.usage_page==0xff00 and
               (d.output_len>=7 or d.input_len>=7) for d in items)


@dataclass
class Status:
    slot: int
    level: int | None = None
    charging: bool | None = None
    voltage: float | None = None
    band: str = ''
    name: str = ''
    kind: str = 'device'
    note: str = ''


def parse_battery(feature,data,slot):
    if len(data)<3:raise hw.HidError('HID++ 电池回复过短')
    if feature==0x1004:
        if len(data)<4:raise hw.HidError('HID++ 统一电池回复过短')
        level,band,state=data[:3]
        if level>100:raise hw.HidError('HID++ 电量越界')
        label={8:'高档',4:'中档',2:'低档',1:'低档',0:''}.get(band)
        if label is None:raise hw.HidError('HID++ 电池档位无效')
        # A zero percentage with a nonempty level means a coarse-only device.
        return Status(slot,level if level or not band else None,
                      state in (1,2,4) if state in range(5) else None,
                      band=label if not level and band else '',note='HID++ 1004 设备原生电量')
    if feature==0x1000:
        level,_,state=data[:3]
        if level>100:raise hw.HidError('HID++ 电量越界')
        return Status(slot,level if level else None,state in (1,2,4) if state in range(5) else None,
                      note='HID++ 1000；零值可能表示未知，不推算充电百分比')
    if feature==0x1001:
        mv=int.from_bytes(data[:2],'big')
        if not 1000<=mv<=6000:raise hw.HidError('HID++ 电压越界')
        flags=data[2]
        # A full/error charging status must not be presented as active charging.
        charging=(flags&3)==0 if flags&0x80 else bool(flags&0x10)
        if flags&0x80 and flags&3==3:charging=None
        return Status(slot,charging=charging,voltage=mv/1000,
                      note='HID++ 1001 原生电压；不套用通用百分比曲线')
    raise ValueError('不支持的电池特征')


class Channel:
    def __init__(self,writer,readers,deadline,output_len=7):
        self.writer,self.readers,self.deadline=writer,readers,deadline
        if not 7<=output_len<=65:raise hw.HidError('HID++ 输出报告长度不支持')
        self.output_len=output_len
        self.features={}

    def exchange(self,slot,address,function,params=b'',legacy=False):
        if time.monotonic()>=self.deadline:return None
        if legacy:
            if address!=0x81 or function not in (0x0d,7):raise ValueError('仅允许 HID++ 1.0 电池寄存器读取')
        elif not ((address==0 and function==SOFTWARE) or (address in self.features.get(slot,{}) and
                   function in self.features[slot][address])):
            raise ValueError('仅允许已发现的电池或名称只读函数')
        if len(params)>3:raise ValueError('HID++ 请求参数过长')
        request=bytes([0x10,slot,address,function])+params+bytes(3-len(params))
        for reader in self.readers:
            for _ in range(16):
                if time.monotonic()>=self.deadline:return None
                if not reader.read_input(timeout_ms=1):break
        if time.monotonic()>=self.deadline:return None
        # Windows expects the collection's maximum OutputReportByteLength even
        # when report 10 itself carries only seven bytes.
        self.writer.write_output(request.ljust(self.output_len,b'\0'),use_hidd=False)
        end=min(self.deadline,time.monotonic()+.3)
        while time.monotonic()<end:
            for reader in self.readers:
                if time.monotonic()>=end:break
                raw=reader.read_input(timeout_ms=max(1,min(30,int((end-time.monotonic())*1000))))
                if not raw or len(raw)<7 or raw[1]!=slot:continue
                if raw[0] not in (0x10,0x11):continue
                if raw[2]==0x8f and raw[3:5]==bytes([address,function]):return None
                if raw[2]==0xff and raw[3:5]==bytes([address,function]):return None
                if raw[2:4]==bytes([address,function]):return raw[4:]
        return None

    def feature(self,slot,feature):
        if feature not in (0x1004,0x1000,0x1001,5):raise ValueError('未知特征查询')
        reply=self.exchange(slot,0,SOFTWARE,feature.to_bytes(2,'big'))
        if not reply or not reply[0]:return None
        index=reply[0]
        allowed={SOFTWARE,0x10|SOFTWARE,0x20|SOFTWARE} if feature==5 else {0x10|SOFTWARE} if feature==0x1004 else {SOFTWARE}
        self.features.setdefault(slot,{})[index]=allowed
        return index

    def battery(self,slot):
        for fid in (0x1004,0x1000,0x1001):
            index=self.feature(slot,fid)
            if not index:continue
            data=self.exchange(slot,index,0x10|SOFTWARE if fid==0x1004 else SOFTWARE)
            if data:
                try:
                    status=parse_battery(fid,data,slot)
                    if status.level is not None or status.voltage is not None or status.band:return status
                except hw.HidError:continue
        for register in (0x0d,7):
            data=self.exchange(slot,0x81,register,legacy=True)
            if not data or len(data)<3:continue
            if register==0x0d and data[0]<=100 and data[2]&0xf0 in (0x30,0x50,0x90):
                return Status(slot,data[0],data[2]&0xf0==0x50,note='HID++ 1.0 原生电池百分比')
            if register==7:
                band={7:'高档',5:'中档',3:'低档',1:'低档'}.get(data[0])
                if band:
                    flags=data[1]
                    charging=False if flags==0 or flags&0x22==0x22 else True if flags&0x21==0x21 else None
                    return Status(slot,charging=charging,band=band,note='HID++ 1.0 原生粗略档位，非精确百分比')
        return None

    def name(self,slot):
        index=self.feature(slot,5)
        if not index:return '', 'device'
        name=''
        count=self.exchange(slot,index,SOFTWARE)
        if count and 0<count[0]<=64:
            buf=bytearray()
            while len(buf)<count[0]:
                fragment=self.exchange(slot,index,0x10|SOFTWARE,bytes([len(buf)]))
                if not fragment:break
                buf.extend(fragment[:count[0]-len(buf)])
            if len(buf)==count[0]:name=buf.decode('utf-8',errors='replace').strip('\0 ')
        kind=self.exchange(slot,index,0x20|SOFTWARE)
        return name,({0:'keyboard',1:'keyboard',3:'mouse',4:'mouse',5:'mouse'}.get(kind[0],'device') if kind else 'device')


def read_status(items):
    endpoints=[d for d in items if d.vid==0x046d and d.usage_page==0xff00]
    writers=sorted([d for d in endpoints if 7<=d.output_len<=65],key=lambda d:(d.usage!=1,d.output_len))
    inputs=sorted([d for d in endpoints if d.input_len>=7],key=lambda d:d.usage!=2)
    if not writers or not inputs:raise hw.HidError('未找到 HID++ 厂商收发集合')
    pid=endpoints[0].pid
    slots=range(1,RECEIVERS[pid]+1) if pid in RECEIVERS else (0xff,)
    with serialized(items),ExitStack() as stack:
        writer=None
        for endpoint in writers:
            try:
                candidate=stack.enter_context(hw.HidDevice(endpoint.path,write=True))
                caps = candidate.value_caps(hw.HidP_Output) + candidate.button_caps(hw.HidP_Output)
                if any(c.ReportID==0x10 for c in caps):
                    writer=candidate
                    output_len=endpoint.output_len
                    break
            except hw.HidError:continue
        if writer is None:raise hw.HidError('未找到声明 Report 10 的 HID++ 输出集合')
        readers=[]
        for endpoint in inputs:
            try:readers.append(stack.enter_context(hw.HidDevice(endpoint.path)))
            except hw.HidError:continue
        if not readers:raise hw.HidError('HID++ 输入集合不可读')
        channel=Channel(writer,readers,time.monotonic()+2,output_len)
        results=[]
        for slot in slots:
            # Every slot gets a budget, even when earlier paired devices sleep.
            channel.deadline=time.monotonic()+2
            status=channel.battery(slot)
            if status:
                results.append(status)
        for status in results:
            channel.deadline=time.monotonic()+.8
            status.name,status.kind=channel.name(status.slot)
        return results
