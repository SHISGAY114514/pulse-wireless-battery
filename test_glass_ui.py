"""Offline interaction regressions for the real Tk glass dashboard.

The root stays withdrawn: no resident process, HID query or user setting is used.
Run from upstream with ``python3.12 -m unittest test_glass_ui -v``.
"""
from contextlib import ExitStack
from copy import deepcopy
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import patch

from modern_ui import Dashboard
from readings import Reading
import tray_common as tc


class GlassDashboardTests(unittest.TestCase):
    def setUp(self):
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch("modern_ui.window_theme"))
        self.patches.enter_context(patch("modern_ui.service.initial_readings", return_value=[]))
        self.patches.enter_context(patch("modern_ui.service.read_all", side_effect=AssertionError("hardware access")))
        self.patches.enter_context(patch("modern_ui.tc.load_config", return_value=deepcopy(tc.DEFAULT_CONFIG)))
        self.patches.enter_context(patch("modern_ui.tc.is_autostart_enabled", return_value=False))
        self.patches.enter_context(patch("modern_ui.tc.save_config", side_effect=AssertionError("configuration write")))
        self.refresh = self.patches.enter_context(patch.object(Dashboard, "refresh"))
        self.patches.enter_context(patch.object(Dashboard, "pump"))
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.destroy_root)
        self.view = Dashboard(self.root)

    def destroy_root(self):
        # Cancel startup, resize and animation callbacks before destroying Tcl.
        for callback in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.after_cancel(callback)
        self.root.destroy()

    @staticmethod
    def reading(key, kind="mouse", **values):
        return Reading(key, "Device " + key, "无线设备", "1234:5678", kind=kind, **values)

    def texts(self, tag="all"):
        return [self.view.c.itemcget(item, "text")
                for item in self.view.c.find_withtag(tag)
                if self.view.c.type(item) == "text"]

    def region(self, key):
        return next(region for region in self.view._regions if region["key"] == key)

    def center(self, key):
        left, top, right, bottom = self.region(key)["box"]
        return SimpleNamespace(x=(left + right) / 2, y=(top + bottom) / 2)

    def displayed_devices(self):
        return [region["key"][5:] for region in self.view._regions
                if region["key"].startswith("card_")]

    def frame_at(self, now):
        # Simulate a timer firing without entering a Tk loop or mapping the root.
        self.view._stop_motion()
        with patch("glass_view.time.monotonic", return_value=now):
            self.view._tick()

    def assert_image_references_match_canvas(self):
        images = {item for item in self.view.c.find_all()
                  if self.view.c.type(item) == "image"}
        self.assertEqual(set(self.view._item_photos), images)
        self.assertLessEqual(set(self.view._shape_specs), images)
        for item, photo in self.view._item_photos.items():
            self.assertEqual(self.view.c.itemcget(item, "image"), str(photo))
            self.assertGreater(photo.width(), 0)
            self.assertGreater(photo.height(), 0)

    def test_category_change_resets_page_and_pagination_uses_filtered_devices(self):
        self.view.rows = [self.reading(str(i), "mouse" if i < 4 else "headset")
                          for i in range(7)]
        self.view.draw()
        self.assertEqual(self.displayed_devices(), ["0", "1", "2"])
        self.view.change_device_page(1)
        self.assertEqual(self.displayed_devices(), ["3", "4", "5"])
        self.view.set_filter("mouse")
        self.assertEqual(self.view.device_page, 0)
        self.assertEqual(self.displayed_devices(), ["0", "1", "2"])
        self.view.change_device_page(1)
        self.assertEqual(self.displayed_devices(), ["3"])
        self.view.change_device_page(1)
        self.assertEqual(self.displayed_devices(), ["0", "1", "2"])
        self.view.set_filter("keyboard")
        self.assertEqual(self.displayed_devices(), [])
        self.assertIn("此分类暂无设备", self.texts())
        self.assertFalse(any(r["key"] in ("previous", "next") for r in self.view._regions))

    def test_unknown_voltage_and_native_band_never_invent_percentages(self):
        self.view.rows = [
            self.reading("unknown"),
            self.reading("voltage", voltage=3.81, protocol="zowie"),
            self.reading("band", "headset", display_band="中档"),
        ]
        self.view.draw()
        labels = self.texts()
        for expected in ("—", "尚未取得电量", "3.81", "V", "缓存电压 · 采样时间未知", "中档", "设备原生档位"):
            self.assertIn(expected, labels)
        self.assertFalse(any("%" in label for label in labels))
        self.view.rows = [self.reading("cached", level=64, stale=True)]
        self.view.draw()
        self.assertIn("64", self.texts())
        self.assertIn("%", self.texts())
        self.assertIn("上次电量 · 非当前值", self.texts())
        self.assertIn("非当前值", self.texts())

    def test_keyboard_focus_draws_indicator_and_activates_navigation(self):
        self.assertEqual(self.view._focus_step(1), "break")
        self.assertEqual(self.view._focus_key, "nav_devices")
        self.assertTrue(self.view.c.find_withtag("focus"))
        self.view._focus_step(1)
        self.assertEqual(self.view._focus_key, "nav_settings")
        self.assertEqual(self.view._activate_focus(None), "break")
        self.assertEqual(self.view.page, "settings")
        self.assertIn("减少动态效果", self.texts())
        self.view._focus_key = None
        self.view._focus_step(-1)
        self.assertEqual(self.view._focus_key, "exit")

    def test_busy_refresh_is_unavailable_to_keyboard_and_pointer(self):
        self.view.busy = True
        self.view.draw()
        self.assertIn("正在同步", self.texts())
        event = self.center("refresh")
        self.view._pointer_down(event)
        self.view._pointer_up(event)
        self.view._focus_key = "refresh"
        self.view._activate_focus(None)
        self.refresh.assert_not_called()
        self.view._focus_key = "nav_settings"
        self.view._focus_step(1)
        self.assertNotEqual(self.view._focus_key, "refresh")
        self.view.busy = False
        self.view.draw()
        self.view._focus_key = "refresh"
        self.view._activate_focus(None)
        self.refresh.assert_called_once_with()

    def test_maximized_window_schedules_and_finishes_entry_animation(self):
        self.view.visible = True
        with patch.object(self.root, "state", return_value="zoomed"):
            self.view._enter_page()
            self.assertIsNotNone(self.view._animation)
            self.assertIsNotNone(self.view._entry)
            started = self.view._entry[0]
            self.frame_at(started + 0.1)
            self.assertGreater(self.view._entry[1], 0)
            self.assertLess(self.view._entry[1], 10)
            self.assertIsNotNone(self.view._animation)
            self.frame_at(started + 1)
            self.assertIsNone(self.view._entry)
            self.assertIsNone(self.view._animation)
        self.assertEqual(self.root.state(), "withdrawn")

    def test_hidden_window_cancels_timer_and_does_not_restart_animation(self):
        self.view.visible = True
        with patch.object(self.root, "state", return_value="normal"):
            self.view._ensure_motion()
            scheduled = self.view._animation
            self.assertIsNotNone(scheduled)
            self.view._unmapped(SimpleNamespace(widget=self.root))
            self.assertFalse(self.view.visible)
            self.assertIsNone(self.view._animation)
            self.assertNotIn(scheduled, self.root.tk.splitlist(self.root.tk.call("after", "info")))
            self.view._ensure_motion()
            self.assertIsNone(self.view._animation)
        self.view.visible = True
        self.view._ensure_motion()
        self.assertIsNone(self.view._animation)

    def test_notice_shows_timed_toast_without_redraw_extending_its_lifetime(self):
        self.view.visible = True
        self.view.notice = "读取失败，请重试"
        with patch.object(self.root, "state", return_value="normal"):
            with patch("glass_view.time.monotonic", return_value=100):
                self.view.draw()
            deadline = self.view._toast_until
            self.assertGreater(deadline, 100)
            self.assertIn("读取失败，请重试", " ".join(self.texts("toast")))
            self.assertIsNotNone(self.view._animation)
            with patch("glass_view.time.monotonic", return_value=100.5):
                self.view.draw()
            self.assertEqual(self.view._toast_until, deadline)
            self.frame_at(deadline + 1)
            self.assertFalse(self.view.c.find_withtag("toast"))
            self.assertIsNone(self.view._animation)
            with patch("glass_view.time.monotonic", return_value=deadline + 2):
                self.view.draw()
            self.assertFalse(self.view.c.find_withtag("toast"))

    def test_reduced_motion_keeps_feedback_and_actions_without_moving_effects(self):
        self.view.rows = [self.reading("mouse", level=20)]
        self.view.draw()
        self.view.visible = True
        with patch.object(self.root, "state", return_value="normal"):
            self.view.toggle_motion()
            self.assertTrue(self.view._reduced_motion)
            self.assertIn("已减少动态效果", " ".join(self.texts("toast")))
            self.view._enter_page()
            self.assertIsNone(self.view._entry)
            event = self.center("refresh")
            self.view._pointer_move(event)
            self.view._pointer_down(event)
            self.assertEqual(self.view._ripples, [])
            self.view._pointer_up(event)
            self.refresh.assert_called_once_with()
            self.frame_at(self.view._toast_until - 1)
            self.assertEqual(self.region("refresh")["value"], 1)
            self.view.rows[0].level = 80
            self.view.draw()
            self.assertEqual(self.view._meters, [])
            self.view.navigate("settings")
            self.view.auto = not self.view.auto
            self.view.draw()
            self.assertEqual(self.view._switches, [])

    def test_pointer_release_requires_the_original_action_target(self):
        refresh = self.center("refresh")
        self.view._pointer_down(refresh)
        self.assertTrue(self.view._ripples)
        self.view._pointer_up(self.center("filter_mouse"))
        self.refresh.assert_not_called()
        self.assertEqual(self.view._filter, "all")
        self.view._pointer_down(refresh)
        self.view._pointer_up(refresh)
        self.refresh.assert_called_once_with()

    def test_image_hover_and_ripple_animate_then_release_transient_texture(self):
        self.view.visible = True
        event = self.center("refresh")
        outline = self.region("refresh")["outline"]
        original_photo = self.view.c.itemcget(outline, "image")
        with patch.object(self.root, "state", return_value="normal"):
            with patch("glass_view.time.monotonic", return_value=100):
                self.view._pointer_move(event)
                self.view._pointer_down(event)
                ripple = self.view._ripples[0][0]
                self.view._pointer_up(event)
            self.frame_at(100.1)
            self.assertEqual(self.view.c.type(outline), "image")
            self.assertNotEqual(self.view.c.itemcget(outline, "image"), original_photo)
            self.assertGreater(self.view._item_photos[ripple].width(), 4)
            for index in range(30):
                self.frame_at(100.2 + index / 30)
            self.assertEqual(self.region("refresh")["value"], 1)
            self.assertEqual(self.view._ripples, [])
            self.assertNotIn(ripple, self.view.c.find_all())
            self.assertNotIn(ripple, self.view._item_photos)
            self.assertNotIn(ripple, self.view._shape_specs)
            self.view._pointer_leave(None)
            for index in range(30):
                self.frame_at(102 + index / 30)
            self.assertEqual(self.region("refresh")["value"], 0)
            self.assertIsNone(self.view._animation)
        self.refresh.assert_called_once_with()
        self.assert_image_references_match_canvas()

    def test_image_meter_and_switch_reach_final_geometry_without_recreating_items(self):
        self.view.rows = [self.reading("mouse", level=20)]
        self.view.draw()
        self.view.rows[0].level = 80
        self.view.visible = True
        with patch.object(self.root, "state", return_value="normal"):
            with patch("glass_view.time.monotonic", return_value=100):
                self.view.draw()
            meter, _, _, width, *_ = self.view._meters[0]
            images_before = set(self.view._item_photos)
            self.frame_at(100)
            initial_width = self.view._item_photos[meter].width()
            self.frame_at(100.2)
            middle_width = self.view._item_photos[meter].width()
            self.frame_at(101)
            final_width = self.view._item_photos[meter].width()
            self.assertLess(initial_width, middle_width)
            self.assertLess(middle_width, final_width)
            self.assertEqual(final_width, round(width * .8))
            self.assertEqual(self.view._meters, [])
            self.assertEqual(set(self.view._item_photos), images_before)

            self.view.page = "settings"
            self.view.auto = False
            self.view.draw()
            self.view.auto = True
            with patch("glass_view.time.monotonic", return_value=200):
                self.view.draw()
            track, knob, x, y, *_ = self.view._switches[0]
            images_before = set(self.view._item_photos)
            self.frame_at(200)
            initial_x = self.view.c.coords(knob)[0]
            initial_track = self.view.c.itemcget(track, "image")
            self.frame_at(200.08)
            middle_x = self.view.c.coords(knob)[0]
            self.assertNotEqual(self.view.c.itemcget(track, "image"), initial_track)
            self.frame_at(201)
            final_x = self.view.c.coords(knob)[0]
            self.assertLess(initial_x, middle_x)
            self.assertLess(middle_x, final_x)
            self.assertEqual(self.view.c.coords(knob), [round(x + 23), round(y + 3)])
            self.assertEqual(self.view._switches, [])
            self.assertEqual(set(self.view._item_photos), images_before)
            self.assertIsNone(self.view._animation)
        self.assert_image_references_match_canvas()

    def test_repeated_feedback_focus_and_ripples_do_not_retain_deleted_images(self):
        baseline = len(self.view._item_photos)
        self.view.visible = True
        with patch.object(self.root, "state", return_value="normal"):
            for cycle in range(20):
                with self.subTest(cycle=cycle):
                    now = 100 + cycle * 5
                    with patch("glass_view.time.monotonic", return_value=now):
                        self.view.feedback("同步反馈 " + str(cycle))
                        self.view._focus_step(1)
                        self.view._focus_step(1)
                        self.assertEqual(len(self.view._item_photos), baseline + 2)
                        self.assert_image_references_match_canvas()
                        event = self.center("refresh")
                        self.view._pointer_down(event)
                        self.view._pointer_up(event)
                    self.frame_at(now + .1)
                    self.assert_image_references_match_canvas()
                    self.frame_at(now + 4)
                    self.assertEqual(self.view._ripples, [])
                    self.assertFalse(self.view.c.find_withtag("toast"))
                    self.assertEqual(len(self.view._item_photos), baseline)
                    self.assert_image_references_match_canvas()
        self.assertLessEqual(len(self.view._photos), 80)
        self.assertEqual(self.refresh.call_count, 20)

    def test_image_spinner_updates_while_busy_and_refresh_remains_disabled(self):
        self.view.visible = True
        self.view.busy = True
        with patch.object(self.root, "state", return_value="normal"):
            self.view.draw()
            self.frame_at(100)
            first_frame = self.view.c.itemcget(self.view._spinner, "image")
            self.frame_at(100.125)
            self.assertEqual(self.view.c.type(self.view._spinner), "image")
            self.assertNotEqual(self.view.c.itemcget(self.view._spinner, "image"), first_frame)
            self.assertIsNotNone(self.view._animation)
            event = self.center("refresh")
            self.view._pointer_down(event)
            self.view._pointer_up(event)
            self.view._focus_key = "refresh"
            self.view._activate_focus(None)
            self.assertEqual(self.view._ripples, [])
            self.refresh.assert_not_called()
            self.view.busy = False
            self.view.draw()
            self.frame_at(self.view._toast_until + 1)
            self.assertIsNone(self.view._animation)
            self.view._activate_focus(None)
            self.refresh.assert_called_once_with()
        self.assert_image_references_match_canvas()


if __name__ == "__main__":
    unittest.main()
