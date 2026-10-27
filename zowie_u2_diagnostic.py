"""Opt-in original U2 cache diagnostic; never used by automatic discovery.

Only the two requests reported in issue #1 are sent, to one 04A5:8008
receiver. A successful host query is NOT evidence of a new ADC sample.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from device_identity import device_key, physical_groups
import hid_win as hw
import zowie_proto as dw

ISSUE = "https://github.com/SHISGAY114514/pulse-wireless-battery/issues/1"
VERSION_REPLY = bytes.fromhex("09 80 27 01 C8").ljust(16, b"\0")


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class DiagnosticError(Exception):
    def __init__(self, message, reports=()):
        super().__init__(message)
        self.reports = list(reports)


def parse_reply(command, packet):
    if command == 0x40:
        # Byte 3 is 01 in this reported variant; its meaning is unknown.
        # Do not relax the DW parser, or accept other firmware variants here.
        if packet != VERSION_REPLY:
            raise DiagnosticError("Receiver reply does not match the reported 8027 variant")
        return "8027"
    if command == 0x15:
        return dw.parse_reply(command, packet)
    raise ValueError("Only B440 and B415 are allowed; B425 is unverified on 8027")


def select_receiver(interfaces):
    matches = [d for d in interfaces if (d.vid, d.pid) == (0x04A5, 0x8008)]
    groups = physical_groups(matches)
    if len(groups) != 1:
        raise DiagnosticError("Exactly one original U2 04A5:8008 receiver is required")
    group, devices = groups[0]
    outputs = [d for d in devices if d.usage_page == 0xFF03 and d.output_len == 16]
    inputs = [d for d in devices if d.usage_page == 0xFF04 and d.input_len == 16]
    if len(outputs) != 1 or len(inputs) != 1:
        raise DiagnosticError("Exactly one FF03/FF04 pair with 16-byte reports is required")
    return group, inputs[0], outputs[0]


def read_cache(reader_iface, writer_iface):
    reports = []
    try:
        with dw.serialized(), hw.HidDevice(reader_iface.path) as reader, hw.HidDevice(
            writer_iface.path, write=True
        ) as writer:
            def query(command):
                reader.drain()
                request = bytes([8, 0xB4, command]).ljust(16, b"\0")
                writer.write_output(request, use_hidd=False)
                packet = reader.read_input(timeout_ms=900)
                reports.append({"command": f"B4{command:02X}",
                                "received_at": timestamp(), "reply_hex": packet.hex(" ")})
                return parse_reply(command, packet)

            version = query(0x40)
            value = query(0x15)
        return {"receiver_firmware": version, "raw_centivolts": value,
                "voltage_interpretation_v": value / 100, "reports": reports}
    except (hw.HidError, OSError, DiagnosticError) as exc:
        # Raw HID paths can occur in OS errors; export only a stable category.
        message = str(exc) if isinstance(exc, DiagnosticError) else type(exc).__name__
        raise DiagnosticError(message, reports) from exc


def collect(output, samples=1, interval=300):
    if not 1 <= samples <= 289 or not 5 <= interval <= 3600 or (samples - 1) * interval > 86400:
        raise ValueError("Use 1..289 samples, 5..3600 seconds, at most 24 hours")

    def emit(record):
        output.write(json.dumps(record, ensure_ascii=False) + "\n")
        output.flush()

    emit({"event": "session", "schema": 1, "started_at": timestamp(),
          "source": ISSUE, "usb_id": "04A5:8008", "samples_requested": samples,
          "interval_seconds": interval, "allowed_commands": ["B440", "B415"],
          "freshness": "unknown", "sampled_at": None,
          "limitation": "Host query times only; no ADC timestamp, RF counter, input activity or continuous hotplug monitoring"})
    original = None
    previous = None
    started = time.monotonic()
    last_started = None
    completed = 0
    try:
        for index in range(samples):
            # Never catch up with a burst after sleep/suspend or slow queries.
            if last_started is not None:
                time.sleep(max(0, interval - (time.monotonic() - last_started)))
            now = time.monotonic()
            gap = None if last_started is None else now - last_started
            last_started = now
            group, reader, writer = select_receiver(hw.enumerate_hid())
            identity = (group, reader.path, writer.path)
            if original is not None and identity != original:
                raise DiagnosticError("Receiver identity or interface changed; session stopped")
            original = identity
            checked_at = timestamp()
            result = read_cache(reader, writer)
            value = result["raw_centivolts"]
            emit({"event": "query", "index": index, "checked_at": checked_at,
                  "elapsed_seconds": now - started, "query_gap_seconds": gap,
                  "timing_gap": gap is not None and gap > interval + max(5, interval * .25),
                  "receiver_key": device_key(group), "sampled_at": None,
                  "freshness": "unknown", "mouse_firmware": None,
                  "value_changed": None if previous is None else value != previous,
                  **result})
            previous = value
            completed += 1
    except (DiagnosticError, OSError, hw.HidError) as exc:
        emit({"event": "error", "checked_at": timestamp(),
              "message": str(exc) if isinstance(exc, DiagnosticError) else type(exc).__name__,
              "reports": getattr(exc, "reports", [])})
        emit({"event": "end", "reason": "error", "queries_completed": completed})
        return 1
    except KeyboardInterrupt:
        emit({"event": "end", "reason": "interrupted", "queries_completed": completed})
        return 130
    emit({"event": "end", "reason": "completed", "queries_completed": completed})
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New JSONL file; existing files are never overwritten")
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--interval", type=int, default=300, help="Seconds between query starts (default: 300)")
    args = parser.parse_args(argv)
    if not 1 <= args.samples <= 289 or not 5 <= args.interval <= 3600 or (args.samples - 1) * args.interval > 86400:
        parser.error("Use 1..289 samples, 5..3600 seconds, at most 24 hours")
    try:
        with args.output.open("x", encoding="utf-8") as output:
            return collect(output, args.samples, args.interval)
    except OSError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
