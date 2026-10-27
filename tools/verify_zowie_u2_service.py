"""Execute the known U2 2511 service counter offline, never accessing HID.

This does not establish the real timer cadence, ADC cadence, RF delivery,
or any behavior of the original U2 2027 / receiver 8027.
Usage: python tools/verify_zowie_u2_service.py extracted_Mouse_Tx_U2.bin
Requires the optional research dependency unicorn==2.1.4.
"""
import hashlib
import json
from pathlib import Path
import struct
import sys


def verify(path):
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_SP

    blob = Path(path).read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != "340475345c20b6eabadc67fd71ba9df91a08f4afb004aaad42ce7e1cc55514ff":
        raise ValueError("Unknown firmware hash; analyze its layout first")
    machine = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    machine.mem_map(0, 0x20000)
    machine.mem_write(0x1000, blob)
    machine.mem_map(0x20000000, 0x10000)
    mode = 0

    def hook(cpu, address, size, data):
        if address == 0x9D7C:
            cpu.reg_write(UC_ARM_REG_R0, mode)
            cpu.reg_write(UC_ARM_REG_PC, cpu.reg_read(UC_ARM_REG_LR))

    machine.hook_add(UC_HOOK_CODE, hook)
    base = 0x200000EC
    cases = 0
    for mode in (0, 4):
        for enabled in (0, 1):
            for state in range(8):
                for count in (0, 498, 499, 500, 65535):
                    machine.mem_write(0x20000000, bytes(0x10000))
                    machine.mem_write(base + 3, bytes([enabled]))
                    machine.mem_write(base + 0x56, bytes([state]))
                    machine.mem_write(base + 0x14, struct.pack("<H", count))
                    machine.reg_write(UC_ARM_REG_SP, 0x2000FFF0)
                    machine.reg_write(UC_ARM_REG_LR, 0x18001)
                    machine.emu_start(0xDC39, 0x18000, count=5000)
                    if machine.reg_read(UC_ARM_REG_PC) != 0x18000:
                        raise AssertionError("Service did not return")
                    active = mode != 4 and enabled and state in (3, 5)
                    next_count = ((count + 1) & 65535) if active else count
                    pending = int(bool(active and next_count == 500))
                    expected_count = 0 if pending else next_count
                    actual_count = struct.unpack("<H", machine.mem_read(base + 0x14, 2))[0]
                    actual_pending = machine.mem_read(base + 9, 1)[0]
                    if (actual_count, actual_pending) != (expected_count, pending):
                        raise AssertionError((mode, enabled, state, count, actual_count, actual_pending))
                    cases += 1
    return {"mouse_firmware": "2511", "sha256": digest, "flash_base": "0x1000",
            "service_address": "0xdc38", "mode_getter_stub": "0x9d7c -> 0 or 4",
            "cases": cases, "mismatches": 0,
            "limitations": "Isolated service counter with synthetic RAM and stubbed mode getter; no timer, ADC acquisition, RF or hardware validation; not 2027/8027"}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Provide the extracted U2 2511 firmware image")
    print(json.dumps(verify(sys.argv[1]), indent=2))
