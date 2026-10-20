"""Hardware-free regressions for new mouse/keyboard battery adapters."""
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock,patch
import hid_win as hw
import device_discovery as dd
import device_service as ds
import keyboard_proto as kp
import mchose_proto as mp
import logitech_proto as lp
import razer_proto as rp
from test_discovery import iface


def mchose_reply(level=4,charging=0,rid=0x11):
    # A7 Ultra's measured identity, firmware and radio flag. No personal IDs.
    data=bytes.fromhex('06 53 52 11 00 00 05 05 08 09')+bytes([level,charging])
    return bytes([rid])+bytes(x^255 for x in data)+b'\xff'*8


class MchoseTests(unittest.TestCase):
    def test_actual_wireless_and_wired_battery(self):
        self.assertEqual((mp.parse_reply(mchose_reply()).level,mp.parse_reply(mchose_reply()).charging),(4,False))
        self.assertTrue(mp.parse_reply(mchose_reply(charging=1)).charging)
        self.assertIsNone(mp.parse_reply(mchose_reply(charging=3)).charging)

    def test_sleep_echo_wrong_vendor_bad_id_and_range_rejected(self):
        cases=[mp.request(),b'',b'\x13'+mchose_reply()[1:],mchose_reply(101)]
        bad=bytearray(mchose_reply());bad[2]=0;cases.append(bytes(bad))
        for raw in cases:
            with self.assertRaises(hw.HidError):mp.parse_reply(raw)
        for rid in (0,0x13,0xff):
            with self.assertRaises(ValueError):mp.request(rid)

    def test_short_long_and_secondary_ff0b_not_queried(self):
        f=iface(0x5253,0x1020,'M','MCHOSE A7 Ultra',65);f.usage_page=0xff01
        sibling=iface(0x5253,0x1020,'M','MCHOSE A7 Ultra',61);sibling.usage_page=0xff0b
        device=MagicMock();device.__enter__.return_value=device
        device.value_caps.return_value=[NS(ReportID=0x11),NS(ReportID=0x12)]
        device.get_feature_report.side_effect=[mp.request()]*4+[mchose_reply(rid=0x12)]
        with patch.object(hw,'HidDevice',return_value=device) as opened,patch.object(mp.time,'sleep'):
            self.assertEqual(mp.read_status([sibling,f]).level,4)
        opened.assert_called_once_with(f.path)
        self.assertEqual([c.args[0][0] for c in device.set_feature_report.call_args_list],[0x11]*4+[0x12])
        self.assertTrue(all(c.args[0][1]==0xf9 for c in device.set_feature_report.call_args_list))

    def test_sleep_cache_labeled_expires_and_unplug_clears(self):
        dd._mchose_last.clear()
        f=iface(0x5253,0x1020,'M','MCHOSE A7 Ultra',65);f.usage_page=0xff01
        with patch.object(mp,'read_status',return_value=mp.parse_reply(mchose_reply())):
            rows=dd.read_devices([f])
        self.assertEqual(len(rows),1);self.assertFalse(rows[0].stale)
        with patch.object(mp,'read_status',side_effect=hw.HidError('sleep')):
            row=dd.read_devices([f])[0]
            self.assertTrue(row.stale);self.assertEqual(row.level,4);self.assertIsNone(row.charging)
            self.assertIn('非当前',row.source)
            dd._mchose_last['M'].sampled_at=time.time()-601
            self.assertIsNone(dd.read_devices([f])[0].level)
            dd.read_devices([])
            self.assertFalse(dd._mchose_last)
            self.assertIsNone(dd.read_devices([f])[0].level)


class KeyboardTests(unittest.TestCase):
    def test_rk_wired_identity_does_not_enable_unverified_battery_commands(self):
        f=iface(0x258a,0x00c1,'RK-W','Bluetooth Keyboard',65,usage=1)
        f.version=0x1023
        f.usage_page=0xff00
        with patch.object(hw,'HidDevice') as opened:
            row=dd.read_devices([f])[0]
        opened.assert_not_called()
        self.assertFalse(row.receiver)
        self.assertIsNone(row.level)
        self.assertEqual(row.protocol,'keyboard-rk98-wired')
        self.assertIn('1023',' '.join(row.attempts))
        self.assertIn('1024',' '.join(row.attempts))

    def test_rk_receiver_is_keyboard_once_and_sends_no_commands(self):
        items=[iface(0x25a7,0xfa70,'RK','2.4G Wireless Receiver',usage=u) for u in (2,6,6,6)]
        with patch.object(hw,'HidDevice') as opened:
            rows=dd.read_devices(items)
        opened.assert_not_called()
        self.assertEqual(len(rows),1)
        self.assertEqual((rows[0].kind,rows[0].receiver,rows[0].level),('keyboard',True,None))
        self.assertIn('Feature 06',' '.join(rows[0].attempts))

    def test_keyboard_name_overrides_emulated_mouse_collection(self):
        keyboard=[iface(0x1234,1,'K','Wireless Keyboard',usage=u) for u in (2,6)]
        mouse=[iface(0x1234,1,'M','Wireless Mouse',usage=u) for u in (2,6)]
        self.assertEqual(dd.kind_of(keyboard)[0],'keyboard')
        self.assertEqual(dd.kind_of(mouse)[0],'mouse')

    def test_unpowered_rog_he_is_not_battery_candidate(self):
        items=[iface(0xb05,0x1c7d,'HE','ROG FALCHION ACE 75 HE',usage=6)]
        self.assertIsNone(kp.detect(items))
        self.assertEqual(dd.read_devices(items),[])

    def test_rog_variant_fields_command_and_charging(self):
        raw=bytearray(65);raw[1:3]=b'\x12\x01';raw[6]=7;raw[11]=92;raw[9]=1
        self.assertEqual(kp.parse_rog(bytes(raw),0,11).level,92)
        self.assertEqual(kp.parse_rog(bytes(raw),0,6).level,7)
        self.assertTrue(kp.parse_rog(bytes(raw),0,11).charging)
        raw[1]=0xff
        with self.assertRaises(hw.HidError):kp.parse_rog(bytes(raw),0,11)

    def test_k99_checksum_header_range_and_v3_unknown_charge(self):
        raw=bytearray(20);raw[:2]=b'\x13\x4a';raw[5]=87;raw[6]=0x10;raw[-1]=sum(raw[:-1])&255
        self.assertEqual(kp.parse_k99(bytes(raw)).level,87)
        self.assertTrue(kp.parse_k99(bytes(raw)).charging)
        raw[-1]^=1
        with self.assertRaises(hw.HidError):kp.parse_k99(bytes(raw))
        raw=bytearray(65);raw[:3]=b'\0\xaa\x03';raw[9]=66
        self.assertEqual(kp.parse_k99v3(bytes(raw)).level,66)
        self.assertIsNone(kp.parse_k99v3(bytes(raw)).charging)

    def test_dareu_range_scaling_and_request_whitelist(self):
        raw=bytearray(64);raw[:4]=b'\x02\x04\x07\x80';raw[6:10]=bytes([1,3,4,0])
        self.assertEqual(kp.parse_dareu_battery(bytes(raw)).level,75)
        self.assertIsNone(kp.parse_dareu_battery(bytes(raw)).charging)
        raw[8]=0
        with self.assertRaises(hw.HidError):kp.parse_dareu_battery(bytes(raw))
        with self.assertRaises(ValueError):kp.dareu_query(MagicMock(),'reset')

    def test_razer_keyboard_transaction_and_adapter_kind(self):
        f=iface(0x1532,0x0296,'RK','Razer DSV2 Pro TKL',91,usage=6)
        self.assertEqual((rp.MODELS[f.pid].kind,rp.MODELS[f.pid].transaction),('keyboard',0x9f))
        with patch.object(rp,'read_status',return_value=rp.Status(70,False,179,[])):
            row=dd.read_devices([f])[0]
        self.assertEqual(row.kind,'keyboard');self.assertEqual(row.level,70)


class StandardKeyboardTests(unittest.TestCase):
    def test_interrupt_fallback_reads_only_declared_battery_report(self):
        f=iface(0x1234,1,'K','Wireless Keyboard');f.num_input_value_caps=1
        cap=NS(UsagePage=6,IsRange=False,u=NS(NotRange=NS(Usage=0x20)),ReportID=9,LogicalMin=0,LogicalMax=100)
        dev=MagicMock();dev.__enter__.return_value=dev;dev.value_caps.return_value=[cap]
        dev.get_input_report.side_effect=hw.HidError('GET_INPUT_REPORT unsupported')
        dev.read_input.side_effect=[b'\x01\xff',b'\x09\x43'];dev.usage_value.return_value=67
        with patch.object(hw,'HidDevice',return_value=dev):result=dd.standard_status([f])
        self.assertEqual(result.level,67)
        self.assertEqual(dev.usage_value.call_args.args[1],b'\x09\x43')
        dev.write_output.assert_not_called();dev.set_feature_report.assert_not_called()


class LogitechTests(unittest.TestCase):
    def test_windows_output_padding_and_reject_unmatched_device_or_function(self):
        reader=MagicMock()
        reader.read_input.side_effect=[None,
            bytes.fromhex('10 02 09 1d 30 04 00'),
            bytes.fromhex('10 01 09 0d 30 04 00'),
            bytes.fromhex('11 01 09 1d 30 04 00 00')]
        writer=MagicMock()
        channel=lp.Channel(writer,[reader],time.monotonic()+1,20)
        channel.features={1:{9:{0x1d}}}
        self.assertEqual(channel.exchange(1,9,0x1d),bytes.fromhex('30 04 00 00'))
        writer.write_output.assert_called_once_with(bytes.fromhex('10 01 09 1d')+bytes(16),use_hidd=False)

    def test_deadline_prevents_writes_and_unknown_level_does_not_stop_fallback(self):
        writer=MagicMock()
        channel=lp.Channel(writer,[],time.monotonic()-1)
        self.assertIsNone(channel.exchange(1,0,lp.SOFTWARE,b'\x10\x04'))
        writer.write_output.assert_not_called()
        channel=lp.Channel(writer,[],time.monotonic()+1)
        with patch.object(channel,'feature',side_effect=[1,2,3]),patch.object(channel,'exchange',side_effect=[
                bytes([0,0xff,0,0]),bytes([0,20,0]),bytes.fromhex('0f a0 81')]):
            status=channel.battery(1)
        self.assertEqual(status.voltage,4)
        self.assertFalse(status.charging)

    def test_unified_battery_correct_status_offset_and_coarse_level(self):
        result=lp.parse_battery(0x1004,bytes([71,4,1,0]),1)
        self.assertEqual((result.level,result.charging),(71,True))
        result=lp.parse_battery(0x1004,bytes([0,8,0,0]),1)
        self.assertIsNone(result.level);self.assertEqual(result.band,'高档')

    def test_voltage_is_big_endian_and_no_fake_percentage(self):
        result=lp.parse_battery(0x1001,bytes.fromhex('0f a0 80'),1)
        self.assertEqual(result.voltage,4)
        self.assertIsNone(result.level);self.assertTrue(result.charging)
        result=lp.parse_battery(0x1000,bytes([0,20,1]),1)
        self.assertIsNone(result.level)

    def test_invalid_battery_frames_and_write_commands_rejected(self):
        for fid,data in ((0x1004,b'\xff\x04\0\0'),(0x1001,b'\0\0\0'),(0x1000,b'\x80\0\0')):
            with self.assertRaises(hw.HidError):lp.parse_battery(fid,data,1)
        channel=lp.Channel(MagicMock(),[],time.monotonic()+10)
        with self.assertRaises(ValueError):channel.exchange(1,0x80,7,legacy=True)
        with self.assertRaises(ValueError):channel.feature(1,0x1f00)
        with self.assertRaises(ValueError):channel.exchange(1,5,0x40)

    def test_root_feature_index_and_1004_get_status_function(self):
        channel=lp.Channel(MagicMock(),[],time.monotonic()+10)
        with patch.object(channel,'exchange',return_value=bytes([9,0,0])):
            self.assertEqual(channel.feature(1,0x1004),9)
        self.assertEqual(channel.features[1][9],{0x1d})
        with patch.object(channel,'feature',return_value=9),patch.object(channel,'exchange',return_value=bytes([51,4,0,0])) as exchange:
            self.assertEqual(channel.battery(1).level,51)
        self.assertEqual(exchange.call_args.args,(1,9,0x1d))

    def test_shared_receiver_has_two_cards_but_one_receiver_count(self):
        f=iface(0x46d,0xc548,'L','Logitech Bolt',feature=0);f.usage_page=0xff00;f.output_len=7
        statuses=[lp.Status(1,60,name='MX Keys',kind='keyboard'),lp.Status(2,80,name='MX Master',kind='mouse')]
        with patch.object(lp,'read_status',return_value=statuses):rows=dd.read_devices([f])
        self.assertEqual(len(rows),2);self.assertEqual(ds.receiver_count(rows),1)
        self.assertNotEqual(rows[0].key,rows[1].key)


class PeripheralLayoutTests(unittest.TestCase):
    def test_native_band_without_voltage_and_gray_stale_value_on_real_canvas(self):
        import tkinter as tk
        import modern_ui as ui
        root=tk.Tk();root.withdraw()
        try:
            dashboard=ui.Dashboard(root)
            native=ds.Reading('L','Native keyboard','键盘','046D:C548',kind='keyboard',
                protocol='logitech',display_band='高档')
            stale=ds.Reading('M','MCHOSE asleep','鼠标','5253:1020',kind='mouse',
                protocol='mchose',level=8,stale=True,sampled_at=time.time())
            for rows in ([native,stale],[native,stale,ds.Reading('RK','RK R98','键盘','25A7:FA70')]):
                dashboard.rows=rows;dashboard.draw()
                texts={dashboard.c.itemcget(i,'text'):dashboard.c.itemcget(i,'fill')
                       for i in dashboard.c.find_all() if dashboard.c.type(i)=='text'}
                self.assertIn('高档',texts)
                self.assertEqual(texts['8'],ui.MUTED)
                self.assertTrue(any('非当前值' in t for t in texts))
        finally:root.destroy()


if __name__=='__main__':unittest.main()
