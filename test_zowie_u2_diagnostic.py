"""Original U2 issue #1 fixtures and transport boundaries; no hardware I/O."""
from contextlib import nullcontext
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import zowie_u2_diagnostic as probe
import zowie_proto as dw
import hid_win as hw


def interfaces(group="one", pid=0x8008):
    return [SimpleNamespace(vid=0x04A5, pid=pid, usage_page=usage,
                            input_len=16, output_len=16, group=group,
                            path=f"private-path-{group}-{usage}")
            for usage in (0xFF04, 0xFF03)]


VOLTAGE_REPLY = bytes.fromhex("09 01 8D 00 C8").ljust(16, b"\0")


class OriginalU2Tests(unittest.TestCase):
    def test_reported_variant_is_isolated_from_dw_and_b425(self):
        self.assertEqual(probe.parse_reply(0x40, probe.VERSION_REPLY), "8027")
        self.assertEqual(probe.parse_reply(0x15, VOLTAGE_REPLY), 397)
        with self.assertRaises(hw.HidError):
            dw.parse_reply(0x40, probe.VERSION_REPLY)
        with self.assertRaises(ValueError):
            probe.parse_reply(0x25, bytes.fromhex("09 20 27 C8").ljust(16, b"\0"))
        self.assertEqual(dw.display_band(3.97, "2027", model="U2"), "")

    def test_reject_corrupt_or_unverified_reports(self):
        for index, value in ((0, 8), (1, 0x85), (2, 0x28), (3, 0), (4, 0), (15, 1)):
            packet = bytearray(probe.VERSION_REPLY)
            packet[index] = value
            with self.subTest(index=index), self.assertRaises(probe.DiagnosticError):
                probe.parse_reply(0x40, bytes(packet))
        for packet in (b"", VOLTAGE_REPLY[:-1], VOLTAGE_REPLY + b"\0",
                       bytes.fromhex("08 B4 15").ljust(16, b"\0"),
                       bytes.fromhex("09 01 F4 00 C8").ljust(16, b"\0"),
                       bytes.fromhex("09 01 8D 01 C8").ljust(16, b"\0")):
            with self.subTest(packet=packet), self.assertRaises(hw.HidError):
                probe.parse_reply(0x15, packet)

    def test_endpoint_and_physical_identity_guards(self):
        mismatched = interfaces()
        mismatched[1].group = "another-receiver"
        bad_length = interfaces()
        bad_length[1].output_len = 64
        for candidates in ([], interfaces(pid=0x800A), interfaces(pid=0x8007),
                           interfaces()[:1], interfaces() + interfaces("two"),
                           interfaces() + [interfaces()[0]], mismatched, bad_length):
            with self.subTest(candidates=candidates), self.assertRaises(probe.DiagnosticError):
                probe.select_receiver(candidates)

    def test_transport_only_sends_40_then_15_and_keeps_raw_evidence(self):
        reader, writer = MagicMock(), MagicMock()
        reader.__enter__.return_value = reader
        writer.__enter__.return_value = writer
        reader.read_input.side_effect = [probe.VERSION_REPLY, VOLTAGE_REPLY]
        with patch.object(dw, "serialized", return_value=nullcontext()), patch.object(
            hw, "HidDevice", side_effect=[reader, writer]
        ):
            result = probe.read_cache(*interfaces())
        self.assertEqual(result["raw_centivolts"], 397)
        self.assertEqual(result["voltage_interpretation_v"], 3.97)
        self.assertEqual(result["reports"][1]["reply_hex"], VOLTAGE_REPLY.hex(" "))
        self.assertEqual(reader.drain.call_count, 2)
        for call, command in zip(writer.write_output.call_args_list, (0x40, 0x15)):
            self.assertEqual(call.args, (bytes([8, 0xB4, command]).ljust(16, b"\0"),))
            self.assertEqual(call.kwargs, {"use_hidd": False})
        self.assertEqual(writer.write_output.call_count, 2)

    def test_unknown_version_stops_before_voltage_query(self):
        reader, writer = MagicMock(), MagicMock()
        reader.__enter__.return_value = reader
        writer.__enter__.return_value = writer
        reader.read_input.return_value = bytes.fromhex("09 85 07 00 C8").ljust(16, b"\0")
        with patch.object(dw, "serialized", return_value=nullcontext()), patch.object(
            hw, "HidDevice", side_effect=[reader, writer]
        ), self.assertRaises(probe.DiagnosticError) as caught:
            probe.read_cache(*interfaces())
        self.assertEqual(writer.write_output.call_count, 1)
        self.assertEqual(len(caught.exception.reports), 1)

    def test_repeated_values_and_changed_values_never_claim_new_samples(self):
        output = io.StringIO()
        with patch.object(hw, "enumerate_hid", return_value=interfaces()), patch.object(
            probe, "read_cache", side_effect=[{"raw_centivolts": n} for n in (397, 397, 396)]
        ), patch.object(probe.time, "sleep"):
            self.assertEqual(probe.collect(output, 3), 0)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        queries = [row for row in records if row["event"] == "query"]
        self.assertEqual([row["value_changed"] for row in queries], [None, False, True])
        for row in queries:
            self.assertIsNone(row["sampled_at"])
            self.assertIsNone(row["mouse_firmware"])
            self.assertEqual(row["freshness"], "unknown")
        self.assertNotIn("private-path", output.getvalue())
        self.assertEqual(records[-1]["queries_completed"], 3)

    def test_disconnect_or_replacement_ends_session_without_retry(self):
        for second in ([], interfaces("replacement")):
            output = io.StringIO()
            with patch.object(hw, "enumerate_hid", side_effect=[interfaces(), second]), patch.object(
                probe, "read_cache", return_value={"raw_centivolts": 397}
            ) as query, patch.object(probe.time, "sleep"):
                self.assertEqual(probe.collect(output, 3), 1)
            self.assertEqual(query.call_count, 1)
            records = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(records[-1], {"event": "end", "reason": "error", "queries_completed": 1})

    def test_missing_receiver_never_opens_hid(self):
        with patch.object(hw, "enumerate_hid", return_value=[]), patch.object(hw, "HidDevice") as opened:
            self.assertEqual(probe.collect(io.StringIO()), 1)
        opened.assert_not_called()

    def test_timing_gap_is_explicit_and_not_hidden_as_continuous(self):
        output = io.StringIO()
        with patch.object(hw, "enumerate_hid", return_value=interfaces()), patch.object(
            probe, "read_cache", return_value={"raw_centivolts": 397}
        ), patch.object(probe.time, "sleep"), patch.object(probe.time, "monotonic", side_effect=[0, 0, 0, 700]):
            self.assertEqual(probe.collect(output, 2), 0)
        queries = [json.loads(line) for line in output.getvalue().splitlines() if '"event": "query"' in line]
        self.assertFalse(queries[0]["timing_gap"])
        self.assertTrue(queries[1]["timing_gap"])
        self.assertEqual(queries[1]["query_gap_seconds"], 700)

    def test_interruption_and_errors_preserve_partial_log(self):
        for error, expected in ((KeyboardInterrupt(), 130), (OSError("private-path"), 1)):
            output = io.StringIO()
            with patch.object(hw, "enumerate_hid", side_effect=error):
                self.assertEqual(probe.collect(output), expected)
            self.assertNotIn("private-path", output.getvalue())
            self.assertEqual(json.loads(output.getvalue().splitlines()[-1])["queries_completed"], 0)

    def test_existing_log_and_invalid_limits_do_not_touch_hardware(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "existing.jsonl"
            path.write_text("keep", encoding="utf-8")
            with patch.object(hw, "enumerate_hid") as enumerate_hid, patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit):
                    probe.main(["--output", str(path)])
                for samples, interval in ((0, 300), (290, 300), (2, 4), (2, 3601), (289, 3600)):
                    with self.assertRaises(ValueError):
                        probe.collect(io.StringIO(), samples, interval)
                enumerate_hid.assert_not_called()
            self.assertEqual(path.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
