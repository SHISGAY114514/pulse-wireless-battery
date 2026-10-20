"""Cross-family boundaries, model isolation, configuration and transport guards."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from unittest.mock import MagicMock
from contextlib import nullcontext

import device_service as ds
import hid_win as hw
import tray_common as tc
import zowie_proto as zp


def interface(pid=0x800a, usage=0xff04):
    return SimpleNamespace(vid=0x04a5, pid=pid, path=f'x#vid_04a5&pid_{pid:04x}&col{usage}#x',
                           usage_page=usage, input_len=16, output_len=16 if usage == 0xff03 else 0,
                           feature_len=0, describe_usage=lambda: hex(usage))


class ModelTests(unittest.TestCase):
    def setUp(self):
        ds._mouse_display_band = ''
        ds._mouse_context = None

    def test_same_voltage_different_firmware_led_classes(self):
        self.assertEqual(zp.voltage_band(3.88, '2526', 'EC2-DW'), '两格 / 三格（边界）')
        self.assertEqual(zp.voltage_band(3.88, '2526', 'FK2-DW'), '三格')
        self.assertEqual(zp.display_band(3.88, '2526', model='EC2-DW'), '中档')
        for model in ('FK2-DW', 'ZA13-DW', 'S2-DW', 'U2-DW'):
            self.assertEqual(zp.display_band(3.88, '2526', model=model), '高档')
            self.assertEqual(zp.voltage_band(3.73, '2526', model), '一格 / 两格（边界）')
            self.assertEqual(zp.voltage_band(3.80, '2526', model), '两格 / 三格（边界）')
            self.assertEqual(zp.display_band(3.73, '2526', model=model), '低档')
            self.assertEqual(zp.display_band(3.74, '2526', model=model), '中档')
            self.assertEqual(zp.display_band(3.80, '2526', model=model), '中档')
            self.assertEqual(zp.display_band(3.81, '2526', model=model), '高档')

    def test_glossy_only_uses_officially_shared_version(self):
        self.assertEqual(zp.display_band(3.88, '2526', model='FK2-DW Glossy'), '高档')
        self.assertEqual(zp.display_band(3.88, '2524', model='FK2-DW Glossy'), '')

    def test_unknown_model_version_and_pending_models_never_borrow_calibration(self):
        for model in ('auto', 'unknown', 'FK1-DW', 'ZA12-DW', 'EC2-CW'):
            self.assertEqual(zp.display_band(3.88, '2526', '高档', model), '')
            self.assertEqual(zp.voltage_band(3.88, '2526', model), '')
        self.assertEqual(zp.display_band(3.88, '9999', model='EC2-DW'), '')
        self.assertEqual(zp.display_band(3.88, '2511', model='U2'), '高档')

    def test_model_change_and_firmware_change_clear_hysteresis(self):
        statuses = [zp.Status(3.90, '2524', '8511', True, []),
                    zp.Status(3.89, '2524', '8511', True, []),
                    zp.Status(3.89, '2526', '8511', True, []),
                    zp.Status(3.80, '2526', '8511', True, [])]
        with patch.object(zp, 'read_status', side_effect=statuses):
            rows = [ds.read_mouse([interface()], m) for m in ('EC2-DW', 'EC2-DW', 'EC2-DW', 'FK2-DW')]
        self.assertEqual([r.display_band for r in rows], ['高档', '高档', '中档', '中档'])

    def test_unselected_model_still_exposes_voltage_but_no_false_name_or_band(self):
        with patch.object(zp, 'read_status', return_value=zp.Status(3.88, '2524', '8511', True, [])):
            row = ds.read_mouse([interface()], 'auto')
        self.assertEqual(row.voltage, 3.88)
        self.assertEqual(row.display_band, '')
        self.assertNotIn('EC2', row.name)
        self.assertIsNone(row.level)

    def test_mismatched_receiver_and_original_u2_do_not_receive_dw_queries(self):
        with patch.object(zp, 'read_status') as query:
            self.assertFalse(ds.read_mouse([interface(0x8006)], 'EC2-DW').receiver)
            row = ds.read_mouse([interface(0x8008)], 'U2')
        query.assert_not_called()
        self.assertTrue(row.receiver)
        self.assertIsNone(row.voltage)

    def test_u2_4k_uses_its_own_verified_image_version(self):
        with patch.object(zp, 'read_status', return_value=zp.Status(3.88, '2511', '8511', True, [])):
            row = ds.read_mouse([interface()], 'U2')
        self.assertEqual(row.display_band, '高档')
        self.assertEqual(row.model, 'U2')

    def test_multiple_receivers_refused_before_io(self):
        inputs = [interface(), interface(), interface(usage=0xff03), interface(usage=0xff03)]
        with patch.object(hw, 'HidDevice') as opened:
            with self.assertRaises(hw.HidError):
                zp.read_status(inputs)
        opened.assert_not_called()

    def test_receiver_version_guard_and_read_only_request_sequence(self):
        for version in ('8507', '8511', '9999'):
            reader, writer = MagicMock(), MagicMock()
            reader.__enter__.return_value = reader
            writer.__enter__.return_value = writer
            reader.read_input.side_effect = [bytes.fromhex('09'+version+'00c8').ljust(16, b'\0'),
                bytes.fromhex('092511c8').ljust(16, b'\0'),
                bytes.fromhex('09018400c8').ljust(16, b'\0'),
                bytes.fromhex('092511c8').ljust(16, b'\0')]
            with patch.object(hw, 'HidDevice', side_effect=[reader, writer]), patch.object(zp, 'serialized', return_value=nullcontext()):
                if version == '9999':
                    with self.assertRaises(hw.HidError):
                        zp.read_status([interface(), interface(usage=0xff03)])
                else:
                    status = zp.read_status([interface(), interface(usage=0xff03)])
                    self.assertEqual((status.voltage, status.mouse_firmware), (3.88, '2511'))
            self.assertEqual([call.args[0][2] for call in writer.write_output.call_args_list],
                             [0x40] if version == '9999' else [0x40, 0x25, 0x15, 0x25])

    def test_config_persists_selection_and_rejects_invalid_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'settings.json')
            with patch.object(tc, 'CONFIG_PATH', path), patch.object(tc, 'DATA_DIR', folder):
                cfg = dict(tc.DEFAULT_CONFIG, zowie_model='ZA13-DW', autostart=True)
                tc.save_config(cfg)
                self.assertEqual(tc.load_config()['zowie_model'], 'ZA13-DW')
                self.assertTrue(tc.load_config()['autostart'])
                for invalid in ('unknown', [], None, 1):
                    Path(path).write_text(json.dumps({'zowie_model': invalid}), encoding='utf8')
                    self.assertEqual(tc.load_config()['zowie_model'], 'auto')

    def test_cw_voltage_only_and_strict_reply_validation(self):
        frame = bytes.fromhex('280f32ec').ljust(64, b'\0')
        self.assertEqual(zp.parse_cw_voltage(frame), 3.88)
        self.assertEqual(zp.parse_cw_voltage(b'\0' + frame), 3.88)
        self.assertIsNone(zp.parse_cw_voltage(bytes.fromhex('000000ec').ljust(64, b'\0')))
        for bad in (b'', frame[:4], frame[:3] + b'\0' * 61,
                    bytes.fromhex('280fffec').ljust(64, b'\0'), bytes.fromhex('ffff32ec').ljust(64, b'\0')):
            with self.assertRaises(hw.HidError):
                zp.parse_cw_voltage(bad)


if __name__ == '__main__':
    unittest.main()
