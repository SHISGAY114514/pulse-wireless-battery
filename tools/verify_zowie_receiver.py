"""Execute only known DW receiver read dispatch paths offline; no HID access.

Usage: python tools/verify_zowie_receiver.py extracted_receiver.bin
Optional dependency: unicorn==2.1.4. Firmware is not distributed.
"""
import hashlib
import json
from pathlib import Path
import struct
import sys
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import *

if len(sys.argv) != 2:
    raise SystemExit('Provide an extracted receiver firmware image.')
blob = Path(sys.argv[1]).read_bytes()
digest = hashlib.sha256(blob).hexdigest()
layouts = {
    '8377885f2bcc4782cff0234f5ce4b00874ae8499334feca35f928c3cc0051488': ('8511', 0x2F4C, 0x2D88),
    '3fd4503766f2740be37bb12cd160fcc3c3b35b6a6964348533302ebdf903d093': ('8507', 0x2F44, 0x2D80),
}
if digest not in layouts:
    raise SystemExit('Unknown firmware hash; analyze its layout first.')
version, dispatch, send = layouts[digest]
u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
u.mem_map(0, 0x20000)
u.mem_write(0x1000, blob)
u.mem_map(0x20000000, 0x10000)
sent = []


def hook(machine, address, size, _):
    if address == send:
        pointer = machine.reg_read(UC_ARM_REG_R0)
        length = machine.reg_read(UC_ARM_REG_R1)
        sent.append(bytes(machine.mem_read(pointer, length)))
        machine.emu_stop()


u.hook_add(UC_HOOK_CODE, hook)
cases = 0
for value in sorted(set(range(0, 65536, 257)) | {300, 301, 388, 499, 500, 0x2511, 0x2524, 0x2526}):
    for command in (0x40, 0x25, 0x15):
        u.mem_write(0x20000000, bytes(0x10000))
        # Cache layout identified in both receiver images.
        u.mem_write(0x20000046, struct.pack('<H', value))
        u.mem_write(0x2000004A, struct.pack('<H', value))
        before = bytes(u.mem_read(0x2000002C, 0x30))
        u.mem_write(0x20002000, bytes([8, 0xB4, command]).ljust(16, b'\0'))
        u.mem_write(0x2000F000, struct.pack('<II', 0x20002201, 0x20002202))
        for reg, data in ((UC_ARM_REG_R0, 0x20002000), (UC_ARM_REG_R1, 0x20002100),
                          (UC_ARM_REG_R2, 0), (UC_ARM_REG_R3, 0x20002200),
                          (UC_ARM_REG_SP, 0x2000F000), (UC_ARM_REG_LR, 0x18001)):
            u.reg_write(reg, data)
        sent.clear()
        u.emu_start(dispatch | 1, 0x18000, count=5000)
        assert len(sent) == 1
        if command == 0x40:
            expected = bytes.fromhex('09' + version + '00c8').ljust(16, b'\0')
        elif command == 0x25:
            expected = (b'\x09' + value.to_bytes(2, 'big') + b'\xc8').ljust(16, b'\0')
        else:
            expected = (b'\x09' + value.to_bytes(2, 'big') + b'\x00\xc8').ljust(16, b'\0')
        assert sent[0] == expected, (command, value, sent[0].hex())
        assert bytes(u.mem_read(0x2000002C, 0x30)) == before
        cases += 1
print(json.dumps(dict(sha256=digest, receiver_firmware=version, dispatch=hex(dispatch),
                     query_cases=cases, mismatches=0, cache_modified=False,
                     limitations='Offline dispatch, not USB or wireless hardware validation.'), indent=2))
