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
import mchose_proto
import keyboard_proto
import logitech_proto
from dataclasses import replace

_zowie_states = {}
_mchose_last = {}


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
    name=' '.join(getattr(d,'product','') for d in interfaces).lower()
    if any(token in name for token in ('mouse','鼠标')):return 'mouse','鼠标'
    if any(token in name for token in ('keyboard','键盘','royal kludge')):return 'keyboard','键盘'
    if (1,2) in usages and (1,6) in usages:return 'device','键鼠组合接收器'
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
                            try:report=getter(cap.ReportID)
                            except hw.HidError:
                                if kind!=hw.HidP_Input:continue
                                # Some keyboard receivers push battery input
                                # but do not implement HID GET_INPUT_REPORT.
                                report=None
                                end=time.monotonic()+.15
                                while time.monotonic()<end:
                                    candidate=dev.read_input(timeout_ms=30)
                                    if candidate and candidate[0]==cap.ReportID:
                                        report=candidate
                                        break
                                if report is None:continue
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
    if mchose_proto.matches(interfaces) or keyboard_proto.detect(interfaces) or logitech_proto.matches(interfaces):return True
    name=' '.join(getattr(x,'product','') for x in interfaces).lower()
    if 'virtual' in name:return False
    if any(word in name for word in ('wireless','receiver','dongle','2.4g','无线','接收器')):return True
    if any(word in name for word in ('mchose','迈从','dareu','达尔优','royal kludge')):return True
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


def read_mchose(items,group):
    d=items[0]
    receiver=d.pid in (0x1020,0x100a,0x100b)
    row=ds.Reading('mchose:'+device_key(group),hw.pick_device_name(items),'无线游戏鼠标',d.vid_pid,
        receiver=receiver,connection='2.4G 无线' if receiver else 'USB 有线',kind='mouse',protocol='mchose',
        verification='A7 Ultra 有线及 5253:1020 纯无线已实测；其余收录身份为协议适配')
    row.interfaces=[ds.interface_info(x) for x in items]
    try:
        status=mchose_proto.read_status(items)
        row.level,row.raw_level,row.charging=status.level,status.level,status.charging
        row.firmware=status.firmware
        row.online=True
        row.sampled_at=time.time()
        row.source='迈从设备直接读取'
        row.message='正在充电' if status.charging else '已读取电量'
        row.attempts=[f'Feature 11/12，只读状态命令 06；逻辑设备 {status.logical_vid:04X}:{status.logical_pid:04X}',
                      '鼠标休眠时接收器会返回空身份，不能按 0% 处理']
        _mchose_last[group]=replace(row,attempts=list(row.attempts))
    except hw.HidError as exc:
        row.source='迈从当前电量不可用'
        row.message='接收器已连接，鼠标可能休眠；移动后刷新'
        row.attempts=[str(exc)]
        last=_mchose_last.get(group)
        if receiver and last and time.time()-last.sampled_at<=600:
            row.level,row.sampled_at,row.stale=last.level,last.sampled_at,True
            row.source='上次成功读取（缓存，非当前电量）'
            row.message=f'鼠标休眠/未回复 · 上次 {last.level}%，非当前值'
    return row


def read_keyboard(items,group,profile):
    row=ds.Reading('keyboard:'+device_key(group),profile.name,'无线键盘',items[0].vid_pid,
        receiver=profile.receiver,connection=profile.connection,kind='keyboard',protocol='keyboard-'+profile.code,
        verification=('本机 RK R98 已识别 25A7:FA70 / 258A:00C1；官方 1024 候选固件仅完成离线灯分支验证，实机 1023 电量尚未读到'
                      if profile.code in ('compx-rk','rk98-wired') else '公开协议适配，未在本机逐型号实测'))
    row.interfaces=[ds.interface_info(d) for d in items]
    try:
        status=keyboard_proto.read_status(items,profile)
        row.level,row.charging=status.level,status.charging
        row.online=True
        row.source='键盘协议直接读取'
        row.message='正在充电' if status.charging else '已读取键盘电量'
        row.attempts=[status.note] if status.note else []
    except hw.HidError as exc:
        row.message='键盘已发现，暂无法读取电量'
        row.source='电量未取得'
        row.attempts=[str(exc)]
    return row


def read_logitech(items,group):
    d=items[0]
    physical=device_key(group)
    receiver=d.pid in logitech_proto.RECEIVERS
    statuses=logitech_proto.read_status(items)
    rows=[]
    for status in statuses:
        row=ds.Reading('logitech:'+physical+f':{status.slot}',status.name or f'Logitech 外设 · 槽 {status.slot}',
            {'keyboard':'无线键盘','mouse':'无线鼠标'}.get(status.kind,'无线外设'),d.vid_pid,
            receiver=receiver,receiver_key=physical,connection='2.4G 无线' if receiver else 'USB / 连接方式未确认',
            kind=status.kind,protocol='logitech',level=status.level,charging=status.charging,
            voltage=status.voltage,display_band=status.band,source='Logitech HID++ 只读协议',
            message='已读取设备电量',verification='HID++ 公开协议适配，未在本机实测',attempts=[status.note])
        row.interfaces=[ds.interface_info(x) for x in items]
        rows.append(row)
    if not rows:
        rows=[ds.Reading('logitech:'+physical,'Logitech 接收器' if receiver else hw.pick_device_name(items),'无线外设',d.vid_pid,
            receiver=receiver,receiver_key=physical,protocol='logitech',message='接收器已发现；外设休眠、离线或未提供已适配电池特征',
            source='暂无电量',connection='2.4G 无线' if receiver else 'USB / 连接方式未确认')]
    return rows


def read_devices(interfaces):
    rows=[]
    active_zowie=set()
    active_mchose=set()
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
                row=ds.Reading('razer:'+ident,profile.name,'无线键盘' if profile.kind=='keyboard' else '无线游戏鼠标',d.vid_pid,
                    receiver=profile.receiver,connection='2.4G 无线' if profile.receiver else 'USB 有线',
                    kind=profile.kind,protocol='razer',verification='公开协议适配；仅 DeathAdder V3 Pro 00B7 已在本机实测')
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
                    if profile.kind=='keyboard':row.message='键盘可能休眠或未回复，请唤醒后刷新'
                    row.attempts.append(str(exc))
            elif mchose_proto.matches(items):
                active_mchose.add(group)
                row=read_mchose(items,group)
            elif keyboard_proto.detect(items):
                row=read_keyboard(items,group,keyboard_proto.detect(items))
            elif logitech_proto.matches(items):
                expanded=read_logitech(items,group)
                for item in expanded:item.checked_at=time.time()
                rows.extend(expanded)
                continue
            else:row=read_generic(items,group)
        except Exception as exc:
            row=ds.Reading('hid:'+ident,hw.pick_device_name(items),'外设',d.vid_pid,message='读取失败',connection='USB / 连接方式未确认',attempts=[str(exc)])
        row.checked_at=time.time()
        rows.append(row)
    for key in set(_zowie_states)-active_zowie:del _zowie_states[key]
    for key in set(_mchose_last)-active_mchose:del _mchose_last[key]
    return sorted(rows,key=lambda r:({'zowie':0,'edifier':1,'razer':2}.get(r.protocol,3),r.name,r.key))
