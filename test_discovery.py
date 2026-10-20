"""Protocol validation, physical-device discovery, hotplug and tray lifecycle."""
import queue
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, patch

import hid_win as hw
import razer_proto as rp
import device_discovery as dd
import device_service as ds
import resident_app as resident


def iface(vid,pid,group,name='Wireless Mouse',feature=0,usage=2):
    return NS(vid=vid,pid=pid,vid_pid=f'{vid:04X}:{pid:04X}',group=group,serial='',
              path=f'x#vid_{vid:04x}&pid_{pid:04x}#{group}',product=name,manufacturer='',
              input_len=16,output_len=0,feature_len=feature,usage_page=1,usage=usage,
              num_feature_value_caps=0,num_input_value_caps=0,is_vendor_page=False,
              describe_usage=lambda:'HID',display_name=name)


def response(command=0x80,raw=72,status=2):
    data=bytearray(rp.request(command,0x1f))
    data[1]=status
    data[10]=raw
    data[89]=rp.checksum(data[1:])
    return bytes(data)


def known(protocol,key,level=None,voltage=None):
    return ds.Reading(key,protocol,'外设','0000:0000',receiver=True,protocol=protocol,
                      kind='headset' if protocol=='edifier' else 'mouse',level=level,voltage=voltage,model='EC2-DW')


class RazerTests(unittest.TestCase):
    def test_real_value_scale_and_query_whitelist(self):
        self.assertEqual(rp.parse_reply(response(),0x80,0x1f),72)
        for command in (0,1,0x81,0xff):
            with self.assertRaises(ValueError):rp.request(command,0x1f)
        device=MagicMock()
        device.__enter__.return_value=device
        device.get_feature_report.side_effect=[response(),response(0x84,0)]
        with patch.object(hw,'HidDevice',return_value=device),patch.object(rp.time,'sleep'):
            s=rp.read_status([iface(0x1532,0xb7,'R',feature=91)])
        self.assertEqual((s.level,s.raw,s.charging),(28,72,False))
        self.assertEqual([c.args[0][8] for c in device.set_feature_report.call_args_list],[0x80,0x84])

    def test_rejects_bad_length_crc_transaction_command_and_status(self):
        valid=response()
        bad=[b'',valid[:20],b'\x01'+valid[1:],response(status=4),response(status=1)]
        for pos in (2,5,6,7,8,89,90):
            packet=bytearray(valid);packet[pos]^=1;bad.append(bytes(packet))
        for packet in bad:
            with self.assertRaises(hw.HidError):rp.parse_reply(packet,0x80,0x1f)
        with self.assertRaises(hw.HidError):rp.parse_reply(response(0x84,3),0x84,0x1f)

    def test_charge_query_failure_does_not_discard_valid_battery(self):
        device=MagicMock();device.__enter__.return_value=device
        device.get_feature_report.side_effect=[response(raw=255),response(0x84,0,4),response(0x84,0,4)]
        with patch.object(hw,'HidDevice',return_value=device),patch.object(rp.time,'sleep'):
            s=rp.read_status([iface(0x1532,0xb7,'R',feature=91)])
        self.assertEqual(s.level,100)
        self.assertIsNone(s.charging)
        self.assertTrue(s.notes)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.z=[iface(0x4a5,0x800a,'Z') for _ in range(3)]
        self.e=[iface(0x35bb,0xa00b,'E',usage=0) for _ in range(5)]
        self.r=[iface(0x1532,0xb7,'R',feature=91)]+[iface(0x1532,0xb7,'R') for _ in range(9)]

    def read(self,items,razer=None):
        with patch.object(ds,'read_mouse',return_value=known('zowie','mouse',voltage=3.88)), \
             patch.object(ds,'read_headset',return_value=known('edifier','headset',100)), \
             patch.object(rp,'read_status',return_value=razer or rp.Status(28,False,72,[])):
            return dd.read_devices(items)

    def test_three_physical_receivers_not_eighteen_hid_collections(self):
        rows=self.read(self.z+self.e+self.r)
        self.assertEqual(len(rows),3)
        self.assertEqual(sum(r.receiver for r in rows),3)
        self.assertEqual([r.protocol for r in rows],['zowie','edifier','razer'])
        self.assertEqual(rows[2].level,28)
        self.assertEqual(len(set(r.key for r in rows)),3)

    def test_same_model_two_receivers_stay_separate(self):
        rows=self.read(self.r+[iface(0x1532,0xb7,'OTHER',feature=91)])
        self.assertEqual(len(rows),2)
        self.assertNotEqual(rows[0].key,rows[1].key)

    def test_unplug_removes_reading_and_replug_has_same_identity(self):
        first=self.read(self.z+self.e+self.r)
        removed=self.read(self.z+self.e)
        replugged=self.read(self.z+self.e+list(reversed(self.r)))
        self.assertEqual([len(first),len(removed),len(replugged)],[3,2,3])
        self.assertFalse(any(r.protocol=='razer' for r in removed))
        self.assertEqual(first[2].key,replugged[2].key)

    def test_unknown_wireless_visible_without_any_vendor_writes(self):
        with patch.object(hw,'HidDevice') as opened,patch.object(ds.battery,'strategy_vendor_protocol') as vendor:
            rows=dd.read_devices([iface(0xbeef,0x1234,'UNKNOWN')])
        self.assertEqual(len(rows),1)
        self.assertIsNone(rows[0].level)
        opened.assert_not_called();vendor.assert_not_called()

    def test_wired_keyboard_and_virtual_input_not_wireless_receivers(self):
        keyboard=iface(0xb05,0x1c7d,'KEYBOARD','Wired Keyboard',usage=6)
        virtual=iface(0xff,0xbacc,'VIRTUAL','Virtual Wireless Mouse')
        self.assertEqual(dd.read_devices([keyboard,virtual]),[])

    def test_failed_razer_read_does_not_hide_other_devices_or_reuse_old_value(self):
        with patch.object(ds,'read_mouse',return_value=known('zowie','mouse',voltage=3.88)), \
             patch.object(rp,'read_status',side_effect=hw.HidError('timeout')):
            rows=dd.read_devices(self.z+self.r)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0].voltage,3.88)
        self.assertIsNone(rows[1].level)

    def test_empty_scan_is_empty_not_two_fixed_devices(self):
        self.assertEqual(dd.read_devices([]),[])
        self.assertEqual(ds.initial_readings(),[])

    def test_multiple_zowie_receivers_require_independent_models(self):
        import zowie_proto as zp
        other=iface(0x4a5,0x800a,'SECOND-Z')
        cfg=dict(ds.tc.DEFAULT_CONFIG,zowie_model='EC2-DW',zowie_models={})
        status=zp.Status(3.88,'2526','8511',True,[])
        with patch.object(ds.tc,'load_config',return_value=cfg),patch.object(zp,'read_status',return_value=status):
            rows=dd.read_devices(self.z+[other])
            self.assertTrue(all(r.model=='auto' and not r.display_band for r in rows))
            cfg['zowie_models']={dd.device_key('Z'):'EC2-DW',dd.device_key('SECOND-Z'):'FK2-DW'}
            rows=dd.read_devices(self.z+[other])
        self.assertEqual({r.model:r.display_band for r in rows},{'EC2-DW':'中档','FK2-DW':'高档'})


class StandardBatteryTests(unittest.TestCase):
    def test_declared_feature_and_input_use_logical_range(self):
        for report_type in ('feature','input'):
            f=iface(0x1234,1,'GENERIC','Battery Peripheral',feature=8)
            setattr(f,'num_'+report_type+'_value_caps',1)
            cap=NS(UsagePage=6,IsRange=False,u=NS(NotRange=NS(Usage=0x20)),ReportID=3,LogicalMin=10,LogicalMax=110)
            device=MagicMock();device.__enter__.return_value=device
            device.value_caps.return_value=[cap];device.usage_value.return_value=60
            with patch.object(hw,'HidDevice',return_value=device):result=dd.standard_status([f])
            self.assertEqual(result.level,50)
            self.assertEqual(result.raw_level,60)
            getattr(device,'get_'+report_type+'_report').assert_called_once_with(3)

    def test_signal_strength_capacity_and_invalid_values_are_not_battery_percent(self):
        self.assertNotIn((6,0x29),{(p,u) for p,u,_ in hw.BATTERY_USAGES})
        self.assertNotIn((0x85,0x66),{(p,u) for p,u,_ in hw.BATTERY_USAGES})
        f=iface(0x1234,1,'G','Battery Peripheral',feature=8);f.num_feature_value_caps=1
        cap=NS(UsagePage=6,IsRange=False,u=NS(NotRange=NS(Usage=0x20)),ReportID=3,LogicalMin=0,LogicalMax=100)
        device=MagicMock();device.__enter__.return_value=device;device.value_caps.return_value=[cap]
        device.usage_value.return_value=255
        with patch.object(hw,'HidDevice',return_value=device):self.assertIsNone(dd.standard_status([f]))

    def test_battery_inside_usage_range(self):
        cap=NS(UsagePage=6,IsRange=True,u=NS(Range=NS(UsageMin=0x10,UsageMax=0x25)))
        self.assertEqual(list(dd.battery_fields(cap)),[0x20])


class DynamicTrayTests(unittest.TestCase):
    def test_add_remove_replug_and_no_device_fallback(self):
        dashboard=NS(root=None,messages=queue.Queue())
        tray=resident.ResidentTray(dashboard)
        rows=[known('zowie','Z',voltage=3.88),known('edifier','E',100),known('razer','R',28)]
        with patch.object(tray,'_apply_icon',return_value=True),patch.object(resident.native.SHELL32,'Shell_NotifyIconW',return_value=True) as notify:
            tray.pending_rows=tuple(rows);tray.update_icons()
            self.assertEqual(len(tray.icon_ids),3)
            uid=tray.device_uids['R']
            tray.pending_rows=tuple(rows[:2]);tray.update_icons()
            self.assertEqual(len(tray.icon_ids),2)
            self.assertEqual(notify.call_args.args[0],resident.native.NIM_DELETE)
            tray.pending_rows=tuple(rows);tray.update_icons()
            self.assertEqual(tray.device_uids['R'],uid)
            tray.pending_rows=();tray.update_icons()
            self.assertEqual(tray.icon_ids,{1})
        self.assertTrue(tray.icons_added)

    def test_hardware_change_queues_action_without_calling_tk(self):
        dashboard=NS(root=None,messages=queue.Queue())
        tray=resident.ResidentTray(dashboard)
        self.assertEqual(tray.wnd_proc(None,0x219,0x8000,0),1)
        self.assertEqual(dashboard.messages.get_nowait(),('action','devices_changed'))


class DynamicLayoutTests(unittest.TestCase):
    def test_three_cards_pagination_and_empty_state_on_real_tk_canvas(self):
        import tkinter as tk
        import modern_ui
        root=tk.Tk()
        root.withdraw()
        try:
            ui=modern_ui.Dashboard(root)
            rows=[known('zowie','Z',voltage=3.88),known('edifier','E',100),known('razer','R',28)]
            rows[0].name='ZOWIE EC2-DW'
            rows[1].name='漫步者 G5 MAX'
            rows[2].name='Razer DeathAdder V3 Pro'
            ui.rows=rows
            ui.draw()
            def texts():return [ui.c.itemcget(i,'text') for i in ui.c.find_all() if ui.c.type(i)=='text']
            for row in rows:self.assertIn(row.name,texts())
            for item in ui.c.find_all():
                if ui.c.type(item)=='text' and ui.c.itemcget(item,'text') in [r.name for r in rows]:
                    box=ui.c.bbox(item)
                    self.assertGreaterEqual(box[0],216)
                    self.assertLessEqual(box[2],max(1000,ui.c.winfo_width())-16)
                    self.assertLess(box[3],275)
            ui.rows=rows+[known('standard-hid','FOURTH',66)]
            ui.rows[-1].name='Fourth peripheral'
            ui.draw();ui.change_device_page(1)
            self.assertIn('Fourth peripheral',texts())
            self.assertNotIn('ZOWIE EC2-DW',texts())
            ui.rows=[];ui.draw()
            self.assertIn('尚未发现电量设备',texts())
        finally:
            root.destroy()


if __name__=='__main__':unittest.main()
