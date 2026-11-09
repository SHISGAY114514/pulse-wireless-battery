"""Late headset power-on, bounded recovery work and resident scheduling."""
import queue
import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

from headset_recovery import HeadsetRecovery
from readings import Reading
import device_discovery as discovery
import mchose_headset_proto as proto
import modern_ui
from test_mchose_headset import interface


def headset(key="v9", level=None, **kwargs):
    return Reading(key, "V9", "耳机", "3837:600A", kind="headset",
                   protocol="mchose-headset", level=level, **kwargs)


class RecoveryPolicyTests(unittest.TestCase):
    def test_late_power_on_retries_early_then_stops_at_success_including_zero(self):
        policy = HeadsetRecovery()
        row = headset()
        policy.record([row], 0, full=True)
        self.assertEqual(policy.next_due(600), 5)
        self.assertEqual(policy.due_keys(4.99), set())
        self.assertEqual(policy.due_keys(5), {row.key})
        policy.record([row], 5)
        self.assertEqual(policy.next_due(600), 10)
        policy.record([replace(row, level=0, online=True)], 10)
        self.assertEqual(policy.next_due(600), 600)
        self.assertFalse(policy.pending)

    def test_long_offline_keeps_bounded_retries_without_exponential_wait(self):
        policy = HeadsetRecovery()
        policy.record([headset()], 0, full=True)
        attempts = []
        while policy.next_due(3601) < 3600:
            now = policy.next_due(3601)
            attempts.append(now)
            policy.record([headset()], now)
        self.assertEqual(attempts[:4], [5, 10, 25, 40])
        self.assertLessEqual(len(attempts), 242)
        self.assertEqual(set(b-a for a, b in zip(attempts[2:], attempts[3:])), {15})
        self.assertEqual(policy.pending["v9"].failures, 3)

    def test_multiple_devices_failure_recovery_and_unplug_are_independent(self):
        policy = HeadsetRecovery()
        policy.record([headset("a"), headset("b")], 0, full=True)
        policy.record([headset("a", 80)], 5)
        self.assertEqual(policy.due_keys(5), {"b"})
        policy.record([headset("a", 80)], 6, full=True)
        self.assertFalse(policy.pending)
        policy.record([headset("a", online=False)], 7)
        self.assertEqual(policy.next_due(60), 12)

    def test_unrelated_stale_absent_and_missing_endpoint_results(self):
        policy = HeadsetRecovery()
        other = replace(headset("mouse"), protocol="zowie", kind="mouse")
        policy.record([other, headset("absent", present=False), headset("stale", 80, stale=True)], 0)
        self.assertEqual(set(policy.pending), {"stale"})
        policy.record([], 5, requested={"stale"})
        self.assertFalse(policy.pending)

    def test_unexpected_failure_defers_instead_of_spinning(self):
        policy = HeadsetRecovery()
        policy.record([headset()], 0)
        policy.defer({"v9", "removed"}, 5)
        self.assertFalse(policy.due_keys(5))
        self.assertEqual(policy.next_due(60), 20)


class TargetedReadTests(unittest.TestCase):
    def test_retry_reuses_interfaces_without_scan_or_mouse_reads(self):
        session = discovery.PollSession(workers=1)
        devices = [interface(), interface("second")]
        mouse = interface("mouse", pid=0x6008)
        with patch.object(proto, "read_status", return_value=proto.Status(None, False, None)), \
                patch.object(discovery, "has_battery_field", return_value=False):
            rows = session.read(devices + [mouse])
        key = "mchose-headset:" + discovery.device_key(devices[0].group)
        with patch.object(discovery.hw, "enumerate_hid") as scan, \
                patch.object(proto, "read_status", return_value=proto.Status(73, True, False)) as read, \
                patch.object(discovery.adapters, "read_generic") as generic:
            result = session.retry_headsets({key})
        scan.assert_not_called()
        generic.assert_not_called()
        read.assert_called_once()
        self.assertEqual(read.call_args.args[0][0].group, devices[0].group)
        self.assertIn(key, {r.key for r in rows})
        self.assertEqual([(r.key, r.level) for r in result], [(key, 73)])

    def test_removed_replaced_and_cleared_interfaces_are_not_retried(self):
        session = discovery.PollSession(workers=1)
        old = interface()
        with patch.object(proto, "read_status", return_value=proto.Status(None, False, None)) as read:
            key = session.read([old])[0].key
            replacement = interface()
            replacement.path = "replacement-path"
            session.read([replacement])
            session.retry_headsets({key})
            self.assertIs(read.call_args.args[0][0], replacement)
            session.read([])
            read.reset_mock()
            self.assertEqual(session.retry_headsets({key}), [])
            read.assert_not_called()
            session.read([old])
            session.clear()
            read.reset_mock()
            self.assertEqual(session.retry_headsets({key}), [])
            read.assert_not_called()


class ResidentRecoveryTests(unittest.TestCase):
    def dashboard(self):
        ui = modern_ui.Dashboard.__new__(modern_ui.Dashboard)
        ui.root, ui.tray, ui.draw = MagicMock(), MagicMock(), MagicMock()
        ui.closed = ui.busy = ui.devices_dirty = False
        ui.visible = ui.auto = True
        ui.timer = ui.discovery_timer = None
        ui.cfg = {"interval": 60, "zowie_models": {}, "zowie_model": "auto"}
        ui.rows = [headset(), replace(headset("mouse"), protocol="zowie", voltage=4.0)]
        ui.messages = queue.Queue()
        ui.recovery = HeadsetRecovery()
        ui.recovery.record(ui.rows, 0, full=True)
        ui.next_refresh = 60
        return ui

    def test_timer_selects_targeted_retry_and_prioritizes_full_scan(self):
        ui = self.dashboard()
        ui.refresh = MagicMock()
        with patch.object(modern_ui.time, "monotonic", return_value=5):
            ui.scheduled_refresh()
        ui.refresh.assert_called_once_with(retry_keys={"v9"})
        ui.refresh.reset_mock()
        with patch.object(modern_ui.time, "monotonic", return_value=60):
            ui.scheduled_refresh()
        ui.refresh.assert_called_once_with()

    def test_auto_pause_close_and_busy_never_start_retry_worker(self):
        for attr, value in (("auto", False), ("closed", True), ("busy", True)):
            ui = self.dashboard()
            setattr(ui, attr, value)
            ui.refresh = MagicMock()
            with patch.object(modern_ui.time, "monotonic", return_value=5):
                ui.scheduled_refresh()
                ui.schedule()
            ui.refresh.assert_not_called()
            ui.root.after.assert_not_called()

    def test_success_merges_headset_without_postponing_full_scan(self):
        ui = self.dashboard()
        mouse = ui.rows[1]
        ui.busy = True
        ui.messages.put(("headset_readings", ({"v9"}, [headset(level=73, online=True)])))
        with patch.object(modern_ui.time, "monotonic", return_value=5):
            ui.pump()
        self.assertFalse(ui.busy)
        self.assertEqual(ui.rows[0].level, 73)
        self.assertIs(ui.rows[1], mouse)
        self.assertEqual(ui.next_refresh, 60)
        self.assertFalse(ui.recovery.pending)
        self.assertEqual(ui.root.after.call_args_list[0].args, (55000, ui.scheduled_refresh))
        ui.tray.update_readings.assert_called_once_with(ui.rows)

    def test_unchanged_offline_result_skips_redraw_and_icon_rebuild(self):
        ui = self.dashboard()
        ui.messages.put(("headset_readings", ({"v9"}, [headset(checked_at=200)])))
        with patch.object(modern_ui.time, "monotonic", return_value=5):
            ui.pump()
        ui.draw.assert_not_called()
        ui.tray.update_readings.assert_not_called()
        self.assertEqual(ui.next_refresh, 60)
        self.assertEqual(ui.recovery.next_due(60), 10)

    def test_due_full_scan_survives_retry_in_flight_and_error(self):
        for message in (("headset_readings", ({"v9"}, [headset()])),
                        ("error", ({"v9"}, "worker failed"))):
            ui = self.dashboard()
            ui.busy = True
            ui.messages.put(message)
            with patch.object(modern_ui.time, "monotonic", return_value=61):
                ui.pump()
            self.assertEqual(ui.next_refresh, 60)
            self.assertEqual(ui.root.after.call_args_list[0].args, (1, ui.scheduled_refresh))

    def test_hardware_event_during_retry_still_triggers_full_discovery(self):
        ui = self.dashboard()
        ui.devices_dirty = True
        ui.refresh = MagicMock()
        ui.messages.put(("headset_readings", ({"v9"}, [headset()])))
        with patch.object(modern_ui.time, "monotonic", return_value=5):
            ui.pump()
        self.assertFalse(ui.devices_dirty)
        ui.refresh.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
