"""Discover battery peripherals by physical device, then select a read adapter.

No unknown vendor commands, raw-report sweeps, or generic voltage-to-% curves.
One composite receiver may expose many HID collections: count it only once.
"""
import hashlib
import time
from collections import defaultdict
import hid_win as hw
import battery
import device_service as ds
import tray_common as tc
import razer_proto

_zowie_states = {}


def physical_groups(interfaces):
    groups=defaultdict(list)
    for iface in interfaces:
        group=getattr(iface,'group','')
        if not group:
            group=hw.physical_group_key(iface.path)
            if getattr(iface,'serial',''):group+=':'+iface.serial
        groups[group].append(iface)
    return sorted(groups.items())


def device_key(group):
    # Stable local identity without exporting a serial or Windows Container ID.
    return hashlib.sha256(group.encode('utf8')).hexdigest()[:20]


def kind_of(interfaces):
    usages={(d.usage_page,d.usage) for d in interfaces}
    if (1,2) in usages:return 'mouse','鼠标'
    if (1,6) in usages:return 'keyboard','键盘'
    if (1,5) in usages or (1,4) in usages:return 'gamepad','游戏控制器'
    return 'device','无线外设'


def battery_fields(cap):
    for page,usage,_ in hw.BATTERY_USAGES:
        if page!=cap.UsagePage:continue
        if getattr(cap,'IsRange',False):
            if cap.u.Range.UsageMin<=usage<=cap.u.Range.UsageMax:yield usage
        elif usage==cap.u.NotRange.Usage:yield usage


def report_types(iface):
    if iface.feature_len and getattr(iface,'num_feature_value_caps',0):yield hw.HidP_Feature
    if iface.input_len and getattr(iface,'num_input_value_caps',0):yield hw.HidP_Input


def has_battery_field(interfaces):
    for iface in interfaces:
        if not list(report_types(iface)):continue
        try:
            with hw.HidDevice(iface.path) as dev:
                for kind in report_types(iface):
                    if any(list(battery_fields(c)) for c in dev.value_caps(kind)):return True
        except hw.HidError:pass
    return False


def standard_status(interfaces):
    """Read only declared percentage/strength fields; never treat RF as battery."""
    for iface in interfaces:
        if not list(report_types(iface)):continue
        try:
            with hw.HidDevice(iface.path) as dev:
                for kind in report_types(iface):
                    for cap in dev.value_caps(kind):
                        for usage in battery_fields(cap):
                            getter=dev.get_feature_report if kind==hw.HidP_Feature else dev.get_input_report
                            report=getter(cap.ReportID)
                            value=dev.usage_value(kind,report,cap,usage=usage)
                            low,high=cap.LogicalMin,cap.LogicalMax
                            if value is None or high<=low or not low<=value<=high:continue
                            return battery.BatteryResult(True,round((value-low)*100/(high-low)),value,
                                source='标准 HID 电池字段',detail='按 HID 描述符声明的逻辑范围读取')
        except hw.HidError:pass
    return None


def vendor_candidate(interfaces):
    # Existing upstream adapters remain visible, but successful exchange is
    # still required before declaring a battery reading.
    return any(d.vid==s.vid and d.usage_page==s.usage_page
               for d in interfaces for s in battery.BUILTIN_PROTOCOLS)


def is_candidate(interfaces):
    d=interfaces[0]
    if d.vid==0x04a5 and any(x.pid in (0x8006,0x8008,0x800a) for x in interfaces):return True
    if d.vid==0x35bb and any(x.pid in (0xa00b,0xa211) for x in interfaces):return True
    if d.vid==0x1532 and d.pid in razer_proto.MODELS:return True
    name=' '.join(getattr(x,'product','') for x in interfaces).lower()
    if 'virtual' in name:return False
    if any(word in name for word in ('wireless','receiver','dongle','2.4g','无线','接收器')):return True
    return vendor_candidate(interfaces) or has_battery_field(interfaces)


def read_generic(interfaces,group):
    kind,category=kind_of(interfaces)
    row=ds.Reading('hid:'+device_key(group),hw.pick_device_name(interfaces),category,interfaces[0].vid_pid,
                   kind=kind,connection='USB / 连接方式未确认')
    row.interfaces=[ds.interface_info(d) for d in interfaces]
    result=standard_status(interfaces)
    if result is None and vendor_candidate(interfaces):
        row.protocol='upstream-vendor'
        row.verification='原项目厂商协议适配；未在本机实测此型号'
        row.connection='USB / 连接方式未确认'
        for iface in interfaces:
            try:result=battery.strategy_vendor_protocol(iface,include_user=False)
            except Exception as exc:
                row.attempts.append(str(exc));continue
            if result and result.ok:break
    if result and result.ok:
        row.level=result.level
        row.raw_level=result.raw_level
        row.charging={'charging':True,'discharging':False,'full':False}.get(result.status)
        row.voltage=result.voltage_mv/1000 if result.voltage_mv else None
        row.source=result.source
        row.message='已读取设备电量' if row.level is not None else '仅读取设备电压'
        row.attempts.append(result.detail)
        row.protocol=row.protocol or 'standard-hid'
    else:
        row.source='电量协议未适配'
        row.message='已发现设备，暂不支持读取电量'
        row.attempts.append('未找到标准电池字段或匹配的已确认协议；没有扫描未知厂商命令')
        if result:row.attempts.append(result.detail)
    return row


def read_devices(interfaces):
    rows=[]
    active_zowie=set()
    cfg=tc.load_config()
    groups=physical_groups(interfaces)
    zowie_count=sum(any(x.vid==0x04a5 and x.pid in (0x8006,0x8008,0x800a) for x in items) for _,items in groups)
    for group,items in groups:
        if not is_candidate(items):continue
        ident=device_key(group)
        d=items[0]
        try:
            if d.vid==0x04a5 and any(x.pid in (0x8006,0x8008,0x800a) for x in items):
                active_zowie.add(group)
                model=cfg['zowie_models'].get(ident,cfg['zowie_model'] if zowie_count==1 else 'auto')
                ds._mouse_display_band,ds._mouse_context=_zowie_states.get(group,('',None))
                row=ds.read_mouse(items,model)
                row.receiver=True
                _zowie_states[group]=(ds._mouse_display_band,ds._mouse_context)
                row.key='zowie:'+ident
            elif d.vid==0x35bb and any(x.pid in (0xa00b,0xa211) for x in items):
                row=ds.read_headset(items)
                row.key='edifier:'+ident
            elif d.vid==0x1532 and d.pid in razer_proto.MODELS:
                profile=razer_proto.MODELS[d.pid]
                row=ds.Reading('razer:'+ident,profile.name,'无线游戏鼠标',d.vid_pid,
                    receiver=profile.receiver,connection='2.4G 无线' if profile.receiver else 'USB 有线',
                    kind='mouse',protocol='razer',verification='公开协议适配；仅 DeathAdder V3 Pro 00B7 已在本机实测')
                row.interfaces=[ds.interface_info(x) for x in items]
                try:
                    status=razer_proto.read_status(items)
                    row.level,row.charging,row.raw_level=status.level,status.charging,status.raw
                    row.online=True
                    row.source='雷蛇设备直接读取'
                    row.message='正在充电' if status.charging else '已读取电量'
                    row.attempts=status.notes+['只读命令 07:80 电量 / 07:84 充电；原始 0–255 按比例转换为百分比']
                except Exception as exc:
                    row.source='雷蛇电量暂不可用';row.message='接收器已连接，鼠标可能休眠或未回复'
                    row.attempts.append(str(exc))
            else:row=read_generic(items,group)
        except Exception as exc:
            row=ds.Reading('hid:'+ident,hw.pick_device_name(items),'外设',d.vid_pid,message='读取失败',connection='USB / 连接方式未确认',attempts=[str(exc)])
        row.checked_at=time.time()
        rows.append(row)
    for key in set(_zowie_states)-active_zowie:del _zowie_states[key]
    return sorted(rows,key=lambda r:({'zowie':0,'edifier':1,'razer':2}.get(r.protocol,3),r.name,r.key))
