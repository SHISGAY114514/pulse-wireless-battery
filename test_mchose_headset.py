"""V9 Turbo+ captured report, protocol isolation and front-end state tests."""
from contextlib import nullcontext
from itertools import count
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, patch

import hid_win as hw
import mchose_headset_proto as proto
import device_discovery as discovery
import device_adapters as adapters
import resident_app
from test_discovery import iface


def interface(group="V9", pid=0x600A, usage_page=0xFF90):
    d = iface(0x3837, pid, group, "MCHOSE V9 Turbo+", usage=1)
    d.usage_page = usage_page
    d.input_len = d.output_len = 64
    return d


def reply(level=100, state=2):
    return bytes([0x55, 0x65, level, state]).ljust(64, b"\0")


class V9Tests(unittest.TestCase):
    def test_actual_capture_and_charge_state_semantics(self):
        self.assertEqual(proto.parse_reply(reply()), proto.Status(100, True, False))
        self.assertEqual(proto.parse_reply(reply(0)), proto.Status(0, True, False))
        self.assertEqual(proto.parse_reply(reply(42, 3)), proto.Status(42, True, True))
        self.assertEqual(proto.parse_reply(reply(100, 4)), proto.Status(100, True, False, True))
        self.assertEqual(proto.parse_reply(reply(255, 0x1A)), proto.Status(None, False, None))

    def test_malformed_echo_and_unrelated_report_are_not_battery(self):
        for packet in (b"", reply()[:4], reply() + b"\0", proto.BATTERY_QUERY,
                       b"\x41" + reply()[1:], b"\x55\x11" + reply()[2:],
                       reply(101), reply(100, 1), reply(100, 0xFF)):
            with self.subTest(packet=packet), self.assertRaises(hw.HidError):
                proto.parse_reply(packet)

    def test_only_correct_physical_endpoint_can_be_queried(self):
        for devices in ([], [interface(pid=0x6008)], [interface(pid=0x6009)],
                        [interface(usage_page=0xFF82)], [interface(usage_page=0xFF22)],
                        [interface(), interface("other")], [interface(), interface()]):
            with patch.object(hw, "HidDevice") as opened:
                with self.assertRaises(hw.HidError):
                    proto.read_status(devices)
                opened.assert_not_called()
        bad_length = interface()
        bad_length.output_len = 65
        with patch.object(hw, "HidDevice") as opened, self.assertRaises(hw.HidError):
            proto.read_status([bad_length])
        opened.assert_not_called()

    def test_report_id_guard_runs_before_write(self):
        dev = MagicMock()
        dev.__enter__.return_value = dev
        dev.value_caps.return_value = [NS(ReportID=0x41)]
        dev.button_caps.return_value = []
        with patch.object(proto, "serialized", return_value=nullcontext()), patch.object(hw, "HidDevice", return_value=dev):
            with self.assertRaises(hw.HidError):
                proto.read_status([interface()])
        dev.write_output.assert_not_called()

    def test_exact_battery_request_and_notification_filtering(self):
        dev = MagicMock()
        dev.__enter__.return_value = dev
        dev.value_caps.return_value = [NS(ReportID=0x55)]
        dev.button_caps.return_value = []
        dev.read_input.side_effect = [proto.BATTERY_QUERY, b"\x55\x11" + bytes(62), reply(73, 3)]
        with patch.object(proto, "serialized", return_value=nullcontext()), patch.object(hw, "HidDevice", return_value=dev):
            self.assertEqual(proto.read_status([interface()]), proto.Status(73, True, True))
        dev.write_output.assert_called_once_with(bytes.fromhex("55 65 01").ljust(64, b"\0"), use_hidd=False, timeout_ms=800)
        dev.set_feature_report.assert_not_called()
        self.assertEqual(dev.read_input.call_count, 3)

    def test_timeout_does_not_retry_writes_or_return_old_values(self):
        dev = MagicMock()
        dev.__enter__.return_value = dev
        dev.value_caps.return_value = [NS(ReportID=0x55)]
        dev.button_caps.return_value = []
        dev.read_input.return_value = b""
        with patch.object(proto, "serialized", return_value=nullcontext()), patch.object(hw, "HidDevice", return_value=dev), patch.object(proto.time, "monotonic", side_effect=count(0, .1)):
            with self.assertRaises(hw.HidError):
                proto.read_status([interface()])
        self.assertEqual(dev.write_output.call_count, 1)

    def test_discovery_deduplicates_collections_and_excludes_dock(self):
        devices = [interface(usage_page=p) for p in (0xFF90, 0xFF82, 0xFF22, 0xFF21)]
        dock = interface("dock", 0x6008, 0xFF00)
        dock.product = dock.display_name = "V9 Turbo"
        with patch.object(proto, "read_status", return_value=proto.Status(100, True, False)) as read, patch.object(discovery, "has_battery_field", return_value=False):
            rows = discovery.PollSession(workers=1).read(devices + [dock])
        read.assert_called_once()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row.kind, row.level, row.protocol, row.receiver), ("headset", 100, "mchose-headset", True))
        self.assertEqual(row.value_text, "100%")
        self.assertEqual(len(row.interfaces), 4)

    def test_sleep_and_failure_clear_previous_value_and_do_not_fallback(self):
        session = discovery.PollSession(workers=1)
        with patch.object(proto, "read_status", side_effect=[proto.Status(60, True, True), proto.Status(None, False, None), hw.HidError("timeout")]), patch.object(discovery, "standard_status") as fallback:
            rows = [session.read([interface()])[0] for _ in range(3)]
        self.assertEqual(rows[0].level, 60)
        self.assertTrue(rows[0].charging)
        for row in rows[1:]:
            self.assertFalse(row.available)
            self.assertIsNone(row.level)
            self.assertIsNone(row.charging)
            self.assertFalse(row.stale)
        self.assertFalse(rows[1].online)
        self.assertIsNone(rows[2].online)
        fallback.assert_not_called()

    def test_resident_status_includes_mchose_headset_and_excludes_stale(self):
        with patch.object(proto, "read_status", return_value=proto.Status(100, True, False)):
            row = adapters.read_mchose_headset([interface()], "V9")
        dashboard = NS(root=None, rows=[row], visible=False, busy=False, auto=True)
        tray = resident_app.ResidentTray(dashboard)
        for stale, present, expected in ((False, True, True), (True, True, False), (False, False, False)):
            row.stale, row.present = stale, present
            bits = tray.wnd_proc(None, tray.messages["status"], 0, 0)
            self.assertEqual(bool(bits & 16), expected)


class TimedOutputTests(unittest.TestCase):
    def device(self):
        dev = hw.HidDevice.__new__(hw.HidDevice)
        dev.path, dev.write_access = "test-hid", True
        return dev

    def kernel(self):
        kernel = MagicMock()
        kernel.CreateFileW.return_value = 100
        kernel.CreateEventW.return_value = 200
        kernel.WriteFile.return_value = False
        kernel.WaitForSingleObject.return_value = hw.WAIT_OBJECT_0
        def complete(handle, ov, written, wait):
            written._obj.value = 64
            return True
        kernel.GetOverlappedResult.side_effect = complete
        return kernel

    def test_pending_write_completes_and_closes_independent_handles(self):
        kernel = self.kernel()
        with patch.object(hw, "kernel32", kernel), patch.object(hw.ctypes, "get_last_error", return_value=hw.ERROR_IO_PENDING):
            self.device().write_output(proto.BATTERY_QUERY, use_hidd=False, timeout_ms=800)
        self.assertEqual(kernel.CreateFileW.call_args.args[5], hw.FILE_FLAG_OVERLAPPED)
        kernel.WaitForSingleObject.assert_called_once_with(200, 800)
        kernel.CancelIoEx.assert_not_called()
        self.assertFalse(kernel.GetOverlappedResult.call_args.args[3])
        self.assertEqual([c.args[0] for c in kernel.CloseHandle.call_args_list], [200, 100])

    def test_timeout_cancels_and_waits_before_freeing_buffer(self):
        kernel = self.kernel()
        kernel.WaitForSingleObject.return_value = hw.WAIT_TIMEOUT
        with patch.object(hw, "kernel32", kernel), patch.object(hw.ctypes, "get_last_error", return_value=hw.ERROR_IO_PENDING):
            with self.assertRaisesRegex(hw.HidError, "超时"):
                self.device().write_output(proto.BATTERY_QUERY, use_hidd=False, timeout_ms=800)
        names = [c[0] for c in kernel.mock_calls]
        self.assertLess(names.index("CancelIoEx"), names.index("GetOverlappedResult"))
        self.assertLess(names.index("GetOverlappedResult"), names.index("CloseHandle"))
        self.assertTrue(kernel.GetOverlappedResult.call_args.args[3])

    def test_short_synchronous_write_is_rejected(self):
        kernel = self.kernel()
        def write(handle, data, size, written, ov):
            written._obj.value = size - 1
            return True
        kernel.WriteFile.side_effect = write
        with patch.object(hw, "kernel32", kernel), self.assertRaisesRegex(hw.HidError, "完整"):
            self.device().write_output(proto.BATTERY_QUERY, use_hidd=False, timeout_ms=800)
        kernel.WaitForSingleObject.assert_not_called()
        kernel.CancelIoEx.assert_not_called()

    def test_failed_event_creation_closes_write_handle(self):
        kernel = self.kernel()
        kernel.CreateEventW.return_value = None
        with patch.object(hw, "kernel32", kernel), self.assertRaises(hw.HidError):
            self.device().write_output(proto.BATTERY_QUERY, use_hidd=False, timeout_ms=800)
        kernel.CloseHandle.assert_called_once_with(100)
        kernel.WriteFile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
