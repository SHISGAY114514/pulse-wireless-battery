"""Offline RK98 candidate analysis; stdlib only, no USB or updater execution.

Accepts the exact official package or its decoded image. Firmware is supplied
locally by the researcher and is never bundled with PULSE. See RK_R98_RESEARCH.md.
"""
import argparse
import hashlib
import json
from pathlib import Path

PACKAGE_SHA = '9542375b53d0cccea12e796302043487a881781431035a2a88534e8806f42bd3'
IMAGE_SHA = '1f25668a41bedd6a39550a414ea289b72cd9ef9a9198a8e45161e6f98a673b18'


def load_image(path):
    raw = Path(path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest == IMAGE_SHA:
        return raw
    if digest != PACKAGE_SHA:
        raise ValueError('Unknown image/package: offsets are valid only for the recorded candidate')
    start = raw.rfind(b'\x5a\xa5SINO')
    header = raw[start:start + 12]
    if header[6:10] != b'\x25\x8a\x00\xc1':
        raise ValueError('Unexpected SINO identity')
    size = int.from_bytes(header[10:12], 'big')
    if size != 0xf000:
        raise ValueError('Unexpected payload size')
    cipher = raw[start + 12:start + 12 + size]
    key = raw[start + 12 + size:start + 16 + size]
    delta, mask = 0x9e3769b9, 0xffffffff
    if len(key) != 4 or any(v.to_bytes(4, 'little') not in raw[:start]
                            for v in (delta * 32 & mask, -delta & mask)):
        raise ValueError('Updater arithmetic constants differ')
    decoded = bytearray()
    for offset in range(0, size, 8):
        halves = [int.from_bytes(cipher[offset + i:offset + i + 4], 'big') for i in (0, 4)]
        total = delta * 32 & mask
        for _ in range(32):
            for j in (1, 0):
                other = halves[1 - j]
                k = 2 if j else 0
                term = ((other << 4) + key[k]) ^ (other + total) ^ ((other >> 5) + key[k + 1])
                halves[j] = (halves[j] - term) & mask
            total = (total - delta) & mask
        decoded.extend(b''.join(v.to_bytes(4, 'big') for v in halves))
    if hashlib.sha256(decoded).hexdigest() != IMAGE_SHA:
        raise ValueError('Decoded image hash differs')
    return bytes(decoded)


class Branch8051:
    """Bounded instruction execution of two branches, not a complete MCU model.

    Only RAM/register effects are modeled. Unknown instructions, calls, or
    escapes fail immediately; no SFR peripherals or I/O are emulated.
    """
    def __init__(self, rom):
        self.rom = rom
        self.ram = bytearray(256)
        self.xram = {}
        self.a = self.carry = self.dptr = 0

    def direct(self, address):
        return self.a if address == 0xe0 else self.ram[address]

    def run(self, start, stop, lower, upper):
        self.pc = start
        for _ in range(1000):
            if self.pc == stop:
                return
            if not lower <= self.pc < upper:
                raise ValueError(f'Branch escaped at {self.pc:04x}')
            op = self.take()
            if op == 0x78: self.ram[0] = self.take()
            elif op == 0xe6: self.a = self.ram[self.ram[0]]
            elif op in (0x20, 0x30):
                bit, rel = self.take(), self.take()
                address = 0x20 + bit // 8 if bit < 128 else bit & 0xf8
                if ((self.direct(address) >> (bit & 7)) & 1) == (op == 0x20): self.jump(rel)
            elif op == 0x02: self.pc = self.take() * 256 + self.take()
            elif op in (0xc2, 0xd2):
                bit = self.take()
                address = 0x20 + bit // 8 if bit < 128 else bit & 0xf8
                if address == 0xe0: raise ValueError('ACC bit write is outside this subset')
                if op == 0xd2: self.ram[address] |= 1 << (bit & 7)
                else: self.ram[address] &= ~(1 << (bit & 7))
            elif op == 0xe5: self.a = self.direct(self.take())
            elif op == 0xf5: self.ram[self.take()] = self.a
            elif 0xe8 <= op <= 0xef: self.a = self.ram[op - 0xe8]
            elif 0xf8 <= op <= 0xff: self.ram[op - 0xf8] = self.a
            elif op == 0xc3: self.carry = 0
            elif op == 0x94:
                result = self.a - self.take() - self.carry
                self.a, self.carry = result & 255, int(result < 0)
            elif 0x98 <= op <= 0x9f:
                result = self.a - self.ram[op - 0x98] - self.carry
                self.a, self.carry = result & 255, int(result < 0)
            elif op == 0x25:
                result = self.a + self.direct(self.take())
                self.a, self.carry = result & 255, int(result > 255)
            elif 0x28 <= op <= 0x2f:
                result = self.a + self.ram[op - 0x28]
                self.a, self.carry = result & 255, int(result > 255)
            elif op in (0x50, 0x70, 0x80):
                rel = self.take()
                if op == 0x80 or (op == 0x50 and not self.carry) or (op == 0x70 and self.a): self.jump(rel)
            elif op == 0x90: self.dptr = self.take() * 256 + self.take()
            elif op == 0x74: self.a = self.take()
            elif op == 0xe0: self.a = self.xram.get(self.dptr, 0)
            elif op == 0xf0: self.xram[self.dptr] = self.a
            elif op == 0xa3: self.dptr = (self.dptr + 1) & 65535
            elif op == 0x54: self.a &= self.take()
            elif op == 0x64: self.a ^= self.take()
            elif op == 0x44: self.a |= self.take()
            elif 0x68 <= op <= 0x6f: self.a ^= self.ram[op - 0x68]
            elif op == 0x43:
                address, value = self.take(), self.take()
                self.ram[address] |= value
            elif op == 0x13:
                self.a, self.carry = (self.a >> 1) | (self.carry << 7), self.a & 1
            else: raise ValueError(f'Unsupported instruction {op:02x} at {self.pc - 1:04x}')
        raise ValueError('Instruction budget exceeded')

    def take(self):
        value = self.rom[self.pc]
        self.pc += 1
        return value

    def jump(self, rel):
        self.pc += rel if rel < 128 else rel - 256


def led_count(rom, step):
    cpu = Branch8051(rom)
    cpu.ram[0x1f], cpu.ram[0x45] = step, 8
    cpu.ram[0x24] = 4  # bit 22: skip unrelated framebuffer-clear call
    cpu.ram[0x27] = 2  # bit 39: indicator branch enabled
    cpu.run(0x5769, 0x599a, 0x5769, 0x599a)
    return sum(cpu.xram.get(a) == 3 and cpu.xram.get(a + 1) == 0xfc
               for a in range(0x2c, 0x40, 2))


def radio_step(rom, wire):
    cpu = Branch8051(rom)
    cpu.ram[0x1f] = 0xfe  # sentinel: a rejected packet must not change it
    cpu.xram.update({0x96f: wire[0], 0x970: wire[1], 0x972: wire[2], 0x971: wire[3]})
    cpu.dptr = 0x971
    # End immediately before RET on a rejected packet, or after step assembly.
    # run() permits the RET address only as a stop, never as an instruction.
    if wire[0] != 0xbb or wire[1] != (0x55 - wire[2] - wire[3]) & 255:
        cpu.run(0x9754, 0x97c8, 0x9754, 0x97c9)
    else:
        cpu.run(0x9754, 0x97a8, 0x9754, 0x97a8)
    return cpu.ram[0x1f]


def verify(rom):
    if hashlib.sha256(rom).hexdigest() != IMAGE_SHA: raise ValueError('Image hash differs')
    if rom[0x98a5:0x98a8] != bytes([0x75, 0x1f, 9]): raise ValueError('Default step differs')
    counts = [led_count(rom, step) for step in range(16)]
    if counts != [2, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 10, 10]:
        raise ValueError('LED count branch differs')
    for b2 in range(256):
        for b3 in range(256):
            checksum = (0x55 - b2 - b3) & 255
            if radio_step(rom, [0xbb, checksum, b2, b3]) != 2 * (b2 & 7) + (b3 >> 7):
                raise ValueError('Radio formula differs')
    for b2, b3 in ((0, 0), (4, 128), (255, 255)):
        checksum = (0x55 - b2 - b3) & 255
        for wire in ([0, checksum, b2, b3], [0xbb, checksum ^ 1, b2, b3]):
            if radio_step(rom, wire) != 0xfe: raise ValueError('Invalid radio frame accepted')
    descriptor = rom[0x4fee:0x5000]
    if descriptor[8:12] != b'\x8a\x25\xc1\x00': raise ValueError('USB identity differs')
    return {
        'package_sha256': PACKAGE_SHA, 'image_sha256': IMAGE_SHA,
        'usb_vid_pid': '258A:00C1', 'candidate_bcd_device': f'{int.from_bytes(descriptor[12:14], "little"):04X}',
        'observed_device_bcd_device': '1023', 'initial_step': 9,
        'step_location': 'IRAM direct 0x1F', 'radio_branch': '0x9754..0x97A8',
        'wire_byte_order': ['header', 'checksum', 'payload0', 'payload1'],
        'radio_formula_zero_based': 'step = 2 * (wire[2] & 7) + ((wire[3] >> 7) & 1)',
        'valid_radio_cases': 65536, 'invalid_radio_cases': 6,
        'led_branch': '0x5769..0x599A', 'led_counts_by_step_0_to_15': counts,
        'scope': 'Candidate 1024 RAM-only branch execution; not live 1023 verification. LED addresses counted; physical key mapping not fully traced.',
        'voltage_thresholds': None, 'usb_battery_query': None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('firmware', type=Path)
    parser.add_argument('--output', type=Path, help='Write non-binary evidence JSON')
    args = parser.parse_args()
    result = json.dumps(verify(load_image(args.firmware)), indent=2)
    if args.output: args.output.write_text(result + '\n', encoding='utf-8')
    print(result)


if __name__ == '__main__': main()
