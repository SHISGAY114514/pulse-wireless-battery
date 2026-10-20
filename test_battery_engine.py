"""Regression cases for shared reads, invalid replies and polling isolation."""

import ctypes
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import battery
import standard_hid as standard
import hid_win as hw
import device_discovery as discovery
import device_service as service
import device_adapters as adapters
import darkrim_proto as darkrim
import receiver_protocols as receivers
import aula_proto as aula
import logitech_proto as logitech
import zowie_proto
from test_discovery import iface


def cap(rid=3, low=0, high=100):
    return NS(
        UsagePage=6,
        IsRange=False,
        u=NS(NotRange=NS(Usage=0x20)),
        ReportID=rid,
        LogicalMin=low,
        LogicalMax=high,
        BitSize=8,
    )


def standard_device():
    item = iface(0xBEEF, 1, "standard", feature=8)
    item.num_feature_value_caps = 1
    dev = MagicMock()
    dev.__enter__.return_value = dev
    dev.value_caps.return_value = [cap()]
    dev.get_feature_report.return_value = b"\x03\x32"
    dev.usage_value.return_value = 50
    return item, dev


class StandardTests(unittest.TestCase):
    def test_all_frontends_share_validation_and_scaling(self):
        item, dev = standard_device()
        dev.value_caps.return_value = [cap(low=10, high=110)]
        dev.usage_value.return_value = 60
        with patch.object(hw, "HidDevice", return_value=dev):
            self.assertEqual(standard.read([item]).level, 50)
            self.assertEqual(battery.strategy_standard_battery_usage(item).level, 50)
            self.assertEqual(discovery.standard_status([item]).level, 50)
            row = service.Reading("x", "X", "device", "")
            self.assertTrue(service.standard_read(row, [item]))
            self.assertEqual(row.level, 50)

    def test_first_failed_report_does_not_hide_second(self):
        item, dev = standard_device()
        dev.value_caps.return_value = [cap(2), cap(3)]
        dev.get_feature_report.side_effect = [hw.HidError("unsupported"), b"\x03\x32"]
        with patch.object(hw, "HidDevice", return_value=dev):
            self.assertEqual(standard.read([item]).level, 50)

    def test_same_report_fetched_once_and_range_null_rejected(self):
        item, dev = standard_device()
        dev.value_caps.return_value = [cap(), cap()]
        dev.usage_value.side_effect = [255, 0]
        with patch.object(hw, "HidDevice", return_value=dev):
            self.assertEqual(standard.read([item]).level, 0)
        dev.get_feature_report.assert_called_once_with(3)

    def test_wrong_id_never_decoded(self):
        item, dev = standard_device()
        dev.get_feature_report.return_value = b"\x09\x32"
        with patch.object(hw, "HidDevice", return_value=dev):
            self.assertIsNone(standard.read([item]))
        dev.usage_value.assert_not_called()

    def test_signed_and_invalid_ranges(self):
        self.assertEqual(standard.percentage(246, cap(low=-20, high=0)), 50)
        self.assertIsNone(standard.percentage(101, cap()))
        self.assertIsNone(standard.percentage(0, cap(low=100, high=100)))
        self.assertEqual(standard.percentage(0, cap()), 0)
        self.assertEqual(standard.percentage(100, cap()), 100)

    def test_failure_on_one_interface_does_not_hide_another(self):
        item, dev = standard_device()
        other = iface(0xBEEF, 2, "other", feature=8)
        other.num_feature_value_caps = 1
        with patch.object(hw, "HidDevice", side_effect=[hw.HidError("denied"), dev]):
            self.assertEqual(standard.read([other, item]).level, 50)

    def test_monitor_rejects_bad_id_and_out_of_range(self):
        item, dev = standard_device()
        dev.read_input.side_effect = [b"\x09\x64", b"\x03\xff", b"\x03\x00"]
        dev.usage_value.side_effect = [255, 0]
        calls = 0

        def stop():
            nonlocal calls
            calls += 1
            return calls > 3

        with patch.object(battery, "HidDevice", return_value=dev):
            result, _ = battery.monitor_input_reports(item, stop_flag=stop)
        self.assertEqual(result.level, 0)


class ProtocolValidationTests(unittest.TestCase):
    def test_vendor_busy_marker_never_becomes_zero_percent(self):
        spec = next(s for s in battery.BUILTIN_PROTOCOLS if s.vid == 0x3367)
        for rid, marker in ((0xA1, 3), (0xA1, 7), (0xA1, 8), (0, 1)):
            raw = bytes([rid, marker]).ljust(64, b"\0")
            self.assertFalse(
                battery._parse_spec_response(spec, raw, None, "feature").ok
            )
        raw = bytearray(64)
        raw[:2] = b"\xa1\x01"
        raw[16] = 100
        self.assertEqual(
            battery._parse_spec_response(spec, bytes(raw), None, "feature").level, 100
        )

    def test_vendor_retry_exhaustion_discards_unmatched_reply(self):
        spec = next(s for s in battery.BUILTIN_PROTOCOLS if s.vid == 0x3367)
        dev = MagicMock()
        dev.get_feature_report.return_value = b"\xa1\x08" + bytes(62)
        with patch.object(battery.time, "sleep"):
            self.assertEqual(battery._read_report_after_request(dev, spec), b"")
        dev.drain.assert_not_called()

    def test_darkrim_corrupt_crc_wrong_channel_and_truncated_tlv_rejected(self):
        valid = darkrim.build_frame(0, b"\x01\x01\x46")
        corrupt = bytearray(valid)
        corrupt[12] ^= 1
        wrong = darkrim.build_frame(0, b"\x01\x01\x46", ch=0x11)
        broken = darkrim.build_frame(0, b"\x01\x02\x46")
        self.assertEqual(darkrim.parse_frame(broken).tlvs(), [])
        dev = MagicMock()
        dev.read_input.side_effect = [bytes(corrupt), wrong, valid]
        # Deterministic deadline: each fake input advances the clock.
        clock = iter([0, 0, 0.1, 0.2, 1])
        with patch.object(darkrim.time, "monotonic", side_effect=lambda: next(clock)):
            frames = darkrim.query(dev, 0, 1, timeout=0.5)
        self.assertEqual(len(frames), 1)
        self.assertTrue(frames[0].crc_ok)

    def test_debug_logitech_keeps_unknown_charging_percentage_unknown(self):
        item = iface(0x46D, 0xC548, "L")
        item.usage_page = 0xFF00
        item.output_len = 7
        with patch.object(
            logitech,
            "read_status",
            return_value=[logitech.Status(1, charging=True, voltage=4)],
        ):
            result = battery.strategy_logitech_hidpp(item)
        self.assertIsNone(result.level)
        self.assertEqual(result.voltage_mv, 4000)

    def test_asus_percent_and_coarse_are_distinct(self):
        raw = bytearray(65)
        raw[1:3] = b"\x12\x07"
        raw[5] = 3
        raw[10] = 1
        modern = receivers.PROFILES[0xB05, 0x1A68]
        old = receivers.PROFILES[0xB05, 0x1960]
        self.assertEqual(receivers.parse(modern, bytes(raw)).level, 3)
        coarse = receivers.parse(old, bytes(raw))
        self.assertIsNone(coarse.level)
        self.assertEqual(coarse.band, "高档")
        raw[5] = 5
        with self.assertRaises(hw.HidError):
            receivers.parse(old, bytes(raw))
        raw[1] = 0
        with self.assertRaises(hw.HidError):
            receivers.parse(modern, bytes(raw))

    def test_mouseenc_online_request_checksum_and_no_voltage_curve(self):
        profile = receivers.PROFILES[0x3554, 0xFB3E]
        for command in (3, 4):
            self.assertEqual(sum(receivers.request(profile, command)) & 255, 0x55)
        with self.assertRaises(ValueError):
            receivers.request(profile, 5)
        raw = bytearray(17)
        raw[:2] = bytes([8, 3])
        self.assertFalse(receivers.parse(profile, bytes(raw), 3))
        raw[1] = 4
        raw[6] = 12
        raw[8:10] = (4200).to_bytes(2, "big")
        status = receivers.parse(profile, bytes(raw), 4)
        self.assertEqual((status.level, status.voltage), (12, 4.2))
        raw[6] = 255
        with self.assertRaises(hw.HidError):
            receivers.parse(profile, bytes(raw), 4)

    def test_weisheng_fields_and_unknown_charge(self):
        profile = receivers.PROFILES[0x320F, 0x5088]
        raw = bytearray(16)
        raw[0] = 4
        raw[3] = 26
        raw[8] = 69
        raw[9] = 3
        status = receivers.parse(profile, bytes(raw))
        self.assertEqual(status.level, 69)
        self.assertIsNone(status.charging)
        raw[3] = 25
        with self.assertRaises(hw.HidError):
            receivers.parse(profile, bytes(raw))

    def test_unknown_pid_or_undeclared_report_sends_nothing(self):
        unknown = iface(0xB05, 0xFFFF, "unknown", feature=65)
        self.assertIsNone(receivers.detect([unknown]))
        item = iface(0xB05, 0x1A68, "A")
        item.path += "&mi_00"
        item.input_len = item.output_len = 65
        dev = MagicMock()
        dev.__enter__.return_value = dev
        dev.value_caps.return_value = []
        dev.button_caps.return_value = []
        with patch.object(hw, "HidDevice", return_value=dev):
            with self.assertRaises(hw.HidError):
                receivers.read_status([item], receivers.detect([item]))
        dev.write_output.assert_not_called()

    def test_aula_empty_block_wrong_address_and_valid_zero(self):
        raw = bytearray(33)
        raw[:4] = bytes([0, 0x55, 0x10, 24])
        with self.assertRaises(hw.HidError):
            aula.parse(bytes(raw), 0x10, 24)
        raw[13:17] = bytes.fromhex("450cfefe")
        raw[25] = 1
        self.assertEqual(aula.parse(bytes(raw), 0x10, 24).level, 0)
        raw[4] = 24
        with self.assertRaises(hw.HidError):
            aula.parse(bytes(raw), 0x10, 24)
        with self.assertRaises(ValueError):
            aula.request(65, 0x20, 56)


class EngineTests(unittest.TestCase):
    def test_band_only_reading_is_available_and_has_cli_text(self):
        row = service.Reading("x", "coarse", "mouse", "", display_band="中档")
        self.assertTrue(row.available)
        self.assertEqual(row.value_text, "中档")
        row.display_band = ""
        row.level = 0
        self.assertEqual(row.value_text, "0%")

    def test_mouseenc_transport_checks_online_before_battery(self):
        item = iface(0x3554, 0xFB3E, "VGN")
        item.usage_page, item.input_len, item.output_len = 0xFF02, 17, 17
        profile = receivers.detect([item])
        online = bytearray(17)
        online[:2], online[6] = b"\x08\x03", 1
        level = bytearray(17)
        level[:2], level[6] = b"\x08\x04", 44
        dev = MagicMock()
        dev.__enter__.return_value = dev
        dev.value_caps.return_value = [NS(ReportID=8)]
        dev.button_caps.return_value = []
        dev.read_input.side_effect = [bytes(online), bytes(level)]
        with patch.object(hw, "HidDevice", return_value=dev):
            self.assertEqual(receivers.read_status([item], profile).level, 44)
        self.assertEqual(
            [call.args[0][1] for call in dev.write_output.call_args_list], [3, 4]
        )
        online[6] = 0
        dev.reset_mock()
        dev.read_input.side_effect = [bytes(online)]
        with patch.object(hw, "HidDevice", return_value=dev):
            with self.assertRaises(hw.HidError):
                receivers.read_status([item], profile)
        self.assertEqual(dev.write_output.call_count, 1)

    def test_aula_wired_mode_is_not_wireless_zero_percent(self):
        raw = bytearray(33)
        raw[:4] = bytes([0, 0x55, 0x10, 24])
        raw[13:17] = bytes.fromhex("450cfefe")
        with self.assertRaises(hw.HidError):
            aula.parse(bytes(raw), 0x10, 24)

    def test_sessions_do_not_share_zowie_hysteresis(self):
        a, b = discovery.PollSession(1), discovery.PollSession(1)
        item = iface(0x4A5, 0x800A, "Z")
        cfg = dict(service.tc.DEFAULT_CONFIG, zowie_model="EC2-DW", zowie_models={})
        with (
            patch.object(service.tc, "load_config", return_value=cfg),
            patch.object(
                zowie_proto,
                "read_status",
                side_effect=[
                    zowie_proto.Status(3.90, "2524", "8511", True, []),
                    zowie_proto.Status(3.89, "2524", "8511", True, []),
                    zowie_proto.Status(3.89, "2524", "8511", True, []),
                ],
            ),
        ):
            self.assertEqual(a.read([item])[0].display_band, "高档")
            self.assertEqual(a.read([item])[0].display_band, "高档")
            self.assertEqual(b.read([item])[0].display_band, "中档")
        a.read([])
        self.assertFalse(a.zowie_states)

    def test_parallel_receivers_and_failure_isolation(self):
        session = discovery.PollSession(2)
        barrier = threading.Barrier(2, timeout=2)

        def read(items, group):
            barrier.wait()
            if group == "bad":
                raise hw.HidError("bad receiver")
            return service.Reading(group, "working", "device", "0001:0002", level=44)

        with patch.object(adapters, "read_generic", side_effect=read):
            rows = session.read([iface(0xBEEF, 1, "bad"), iface(0xBEEF, 1, "good")])
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(r.level for r in rows if r.level is not None), [44])
        self.assertTrue(any("bad receiver" in r.attempts for r in rows))

    def test_failed_vendor_can_use_standard_field(self):
        item, dev = standard_device()
        item.vid = 0x1532
        item.pid = 0xB7
        with (
            patch.object(hw, "HidDevice", return_value=dev),
            patch.object(
                discovery.razer_proto,
                "read_status",
                side_effect=hw.HidError("no vendor"),
            ),
        ):
            row = discovery.PollSession(1).read([item])[0]
        self.assertEqual(row.level, 50)
        self.assertEqual(row.source, "标准 HID 电池字段")
        self.assertIn("no vendor", row.attempts)

    def test_same_model_fallback_identity_not_merged(self):
        a = iface(1, 2, "A")
        b = iface(1, 2, "B")
        a.group = b.group = ""
        self.assertEqual(len(discovery.physical_groups([a, b])), 2)

    def test_darkrim_is_visible_in_main_panel(self):
        item = iface(0x3654, 1, "D", "DARKRIM")
        item.usage_page = 0xFF02
        item.usage = 2
        item.input_len = item.output_len = 65
        with patch.object(darkrim, "read_battery_only", return_value=(70, "")):
            row = discovery.PollSession(1).read([item])[0]
        self.assertEqual(
            (row.protocol, row.level, row.kind), ("darkrim", 70, "headset")
        )

    def test_scan_failure_clears_cached_states(self):
        discovery.default_session.zowie_states["x"] = service.ZowieState("高档")
        with patch.object(hw, "enumerate_hid", side_effect=OSError("scan")):
            rows = service.read_all()
        self.assertFalse(discovery.default_session.zowie_states)
        self.assertFalse(rows[0].present)

    def test_native_caps_layout_and_cancellation_completion(self):
        self.assertEqual(ctypes.sizeof(hw.HIDP_VALUE_CAPS), 72)
        self.assertEqual(ctypes.sizeof(hw._RANGE), 16)
        device = object.__new__(hw.HidDevice)
        device._read_handle = 123
        with (
            patch.object(hw.kernel32, "CancelIoEx"),
            patch.object(hw.kernel32, "GetOverlappedResult") as done,
        ):
            device._cancel_read(hw.OVERLAPPED())
        self.assertTrue(done.call_args.args[-1])


if __name__ == "__main__":
    unittest.main()
