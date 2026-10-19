"""Protocol and state tests using real captured packets plus negative cases."""
import ctypes
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import edifier_proto as ep
import device_service as ds
import hid_win as hw
import tray_common as tc
import zowie_proto as zp

class ProtocolTests(unittest.TestCase):
    def test_wireless_status(self):
        packet=bytes.fromhex("D0 35 BB 0A 00 01 23 00 01 05 07 02 03 08").ljust(64,b"\0")
        s=ep.parse_status(packet)
        self.assertEqual((s.level,s.online,s.charging,s.firmware),(35,True,False,"1.5.7"))

    def test_wired_charging(self):
        s=ep.parse_status(bytes.fromhex("E0 35 BB 07 00 01 20 01 01 05 07"))
        self.assertEqual((s.level,s.charging),(32,True))

    def test_offline_is_not_zero_percent(self):
        s=ep.parse_status(bytes.fromhex("D0 35 BB 0A 00 00 00 00 00 00 00 02 03 08"))
        self.assertFalse(s.online)
        self.assertIsNone(s.level)

    def test_zero_and_full_are_real_values(self):
        for value in (0,100):
            s=ep.parse_status(bytes([0xD0,0x35,0xBB,7,0,1,value,0,1,5,7]))
            self.assertEqual(s.level,value)

    def test_rejects_malformed_unrelated_and_out_of_range(self):
        for packet in (b"",bytes.fromhex("D0 35 BB 0A 00 01 23"),
                       bytes.fromhex("ED 35 BB 07 00 01 23 00 01 05 07"),
                       bytes.fromhex("D0 35 BB 07 02 01 23 00 01 05 07"),
                       bytes.fromhex("D0 35 BB 07 00 01 FF 00 01 05 07"),
                       bytes.fromhex("D0 35 BB 07 00 02 23 00 01 05 07")):
            self.assertIsNone(ep.parse_status(packet))

    def test_native_button_caps_abi(self):
        self.assertEqual(ctypes.sizeof(hw.HIDP_BUTTON_CAPS),72)

class StateTests(unittest.TestCase):
    def test_frozen_commands_use_the_executable(self):
        with patch.object(tc,"FROZEN",True), patch.object(tc.sys,"executable",r"C:\Portable\PULSE.exe"):
            self.assertEqual(tc.tray_command(),[r"C:\Portable\PULSE.exe","--background"])
            self.assertEqual(tc.panel_command(),[r"C:\Portable\PULSE.exe"])

    def test_missing_devices(self):
        self.assertFalse(ds.read_mouse([]).receiver)
        self.assertIsNone(ds.read_headset([]).level)

    def test_reader_failure_clears_level(self):
        iface=SimpleNamespace(vid=0x35BB,pid=0xA00B,path="x#vid_35bb&pid_a00b&mi_03&col03#x",
            usage_page=0xFF02,input_len=64,output_len=64,feature_len=0,
            describe_usage=lambda:"0xFF02:0x0002")
        with patch.object(ds,"standard_read",return_value=False), patch.object(ep,"read_status",side_effect=OSError("timeout")):
            row=ds.read_headset([iface])
        self.assertTrue(row.receiver)
        self.assertIsNone(row.level)
        self.assertIn("timeout",row.attempts[-1])

class ZowieTests(unittest.TestCase):
    def test_hardware_captures_are_voltage_and_versions(self):
        self.assertEqual(zp.parse_reply(0x40,bytes.fromhex('09851100c8').ljust(16,b'\0')),'8511')
        self.assertEqual(zp.parse_reply(0x25,bytes.fromhex('092524c8').ljust(16,b'\0')),'2524')
        self.assertEqual(zp.parse_reply(0x15,bytes.fromhex('09017700c8').ljust(16,b'\0')),375)

    def test_rejects_unrelated_malformed_and_invalid_voltage(self):
        cases=[b'',bytes.fromhex('09017700c8'),bytes.fromhex('08017700c8').ljust(16,b'\0'),
               bytes.fromhex('09017700e6').ljust(16,b'\0'),bytes.fromhex('09017701c8').ljust(16,b'\0'),
               bytes.fromhex('09000000c8').ljust(16,b'\0'),bytes.fromhex('0901f400c8').ljust(16,b'\0')]
        for packet in cases:
            with self.assertRaises(hw.HidError): zp.parse_reply(0x15,packet)
        with self.assertRaises(ValueError): zp.parse_reply(0x58,bytes(16))

    def test_cached_voltage_does_not_fabricate_percentage_or_online(self):
        iface=SimpleNamespace(vid=0x04a5,pid=0x800a,path='x#vid_04a5&pid_800a#x',
            input_len=16,output_len=0,feature_len=0,describe_usage=lambda:'FF04')
        s=zp.Status(3.75,'2524','8511',True,[])
        with patch.object(ds,'standard_read',return_value=False),patch.object(zp,'read_status',return_value=s):
            row=ds.read_mouse([iface])
        self.assertEqual(row.voltage,3.75)
        self.assertIsNone(row.level)
        self.assertIsNone(row.online)
        self.assertTrue(any('采样时间未知' in a for a in row.attempts))

    def test_ec_specific_band_and_ambiguous_boundaries(self):
        self.assertEqual(zp.voltage_band(3.75,'2524'),'一格')
        self.assertEqual(zp.voltage_band(3.89,'2524'),'三格')
        self.assertEqual(zp.voltage_band(3.76,'2524'),'两格')
        self.assertEqual(zp.voltage_band(3.88,'2526'),'两格 / 三格（边界）')
        self.assertEqual(zp.voltage_band(3.89,'9999'),'')

    def test_display_bands_match_current_observation_and_thresholds(self):
        for voltage,expected in [(3.60,'低档'),(3.75,'低档'),(3.76,'中档'),
                                 (3.88,'中档'),(3.89,'中档'),(3.90,'高档'),(4.15,'高档')]:
            self.assertEqual(zp.display_band(voltage,'2524'),expected)
        for voltage,firmware in [(None,'2524'),(float('nan'),'2524'),(float('inf'),'2524'),
                                 (3.88,'0000'),(3.88,'9999'),(0,'2524')]:
            self.assertEqual(zp.display_band(voltage,firmware),'')

    def test_band_buffers_prevent_threshold_flapping(self):
        previous='中档'
        for voltage,expected in [(3.89,'中档'),(3.90,'高档'),(3.89,'高档'),(3.88,'中档'),
                                 (3.75,'中档'),(3.74,'低档'),(3.76,'低档'),(3.77,'中档')]:
            previous=zp.display_band(voltage,'2524',previous)
            self.assertEqual(previous,expected)

    def test_disconnect_clears_band_buffer(self):
        iface=SimpleNamespace(vid=0x04a5,pid=0x800a,path='x#vid_04a5&pid_800a#x',
            input_len=16,output_len=0,feature_len=0,describe_usage=lambda:'FF04')
        sequence=[zp.Status(3.90,'2524','8511',True,[]),
                  zp.Status(None,'0000','8511',False,[]),
                  zp.Status(3.89,'2524','8511',True,[])]
        with patch.object(ds,'standard_read',return_value=False),patch.object(zp,'read_status',side_effect=sequence),patch.object(ds,'_mouse_display_band',''):
            rows=[ds.read_mouse([iface]) for _ in sequence]
        self.assertEqual([r.display_band for r in rows],['高档','','中档'])
        self.assertTrue(all(r.level is None for r in rows))

    def test_zero_firmware_or_read_failure_clears_voltage(self):
        iface=SimpleNamespace(vid=0x04a5,pid=0x800a,path='x#vid_04a5&pid_800a#x',
            input_len=16,output_len=0,feature_len=0,describe_usage=lambda:'FF04')
        for result in (zp.Status(None,'0000','8511',False,[]),OSError('timeout')):
            options={'side_effect':result} if isinstance(result,Exception) else {'return_value':result}
            with patch.object(ds,'standard_read',return_value=False),patch.object(zp,'read_status',**options):
                row=ds.read_mouse([iface])
            self.assertIsNone(row.voltage)
            self.assertIsNone(row.level)

if __name__=="__main__":
    unittest.main()
