"""Verify a known ZOWIE firmware image offline. Never opens any HID device.

Optional dependency: pip install -r requirements-research.txt
Usage: python tools/verify_zowie_firmware.py path/to/extracted.bin
Firmware binaries are deliberately not distributed with PULSE.
"""
import sys,json,struct,hashlib
from pathlib import Path
if len(sys.argv) != 2:
    raise SystemExit('Usage: python tools/verify_zowie_firmware.py extracted.bin')
image=Path(sys.argv[1])
blob=image.read_bytes()
digest=hashlib.sha256(blob).hexdigest()
evidence=json.loads((Path(__file__).resolve().parent.parent/'docs'/'firmware-evidence.json').read_text(encoding='utf8'))
known=next((r for r in evidence['images'] if r['sha256']==digest),None)
if known is None:
    raise SystemExit('Unknown firmware hash. Analyze its layout before executing it.')
version=known['mouse_image_version']
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_THUMB,UC_MODE_MCLASS,UC_HOOK_CODE
from unicorn.arm_const import *

u=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
u.mem_map(0,0x20000); u.mem_write(0x1000,blob)
u.mem_map(0x20000000,0x10000)
names={0x3140:'three_leds',0x32ec:'two_leds',0x325c:'one_led',
       0x337c:'low_blink',0x3444:'off',0x2fb0:'charging_high',
       0x30f0:'charging_mid',0x30b4:'charging_low',0x2f18:'full',
       0x300c:'special_charge',0x31d0:'special_wireless'}
if version=='2524': names={address-0x28:name for address,name in names.items()}
selector=0x3454 if version=='2524' else 0x347c
encode=0x38c0 if version=='2524' else 0x3908
special=0x4064 if version=='2524' else 0x40ac
gpio=0x39d2 if version=='2524' else 0x3a1a
ram=0x200000a8
startup_offset=3
correction_offset=10
convert=0x275c
if version=='2511':
    assert hashlib.sha256(blob).hexdigest()=='340475345c20b6eabadc67fd71ba9df91a08f4afb004aaad42ce7e1cc55514ff'
    names={0x2f28:'three_leds',0x3088:'two_leds',0x2ff8:'one_led',
           0x3118:'low_blink',0x3180:'off',0x2e00:'charging_high',
           0x2eec:'charging_mid',0x2eb0:'charging_low',0x2dc0:'full',
           0x2e3c:'special_charge',0x2fb8:'special_wireless'}
    selector,encode,special,gpio,convert=0x31b8,0x3580,0x3ce4,0x3692,0x271c
    ram,startup_offset,correction_offset=0x200000a4,2,9
chosen=None
def hook(machine,address,size,user):
    global chosen
    if address in names:
        chosen=names[address]; machine.emu_stop()
    elif address in (special,gpio):
        machine.reg_write(UC_ARM_REG_R0,0)
        machine.reg_write(UC_ARM_REG_PC,machine.reg_read(UC_ARM_REG_LR))
u.hook_add(UC_HOOK_CODE,hook)
def call(address,*args):
    global chosen
    chosen=None
    u.reg_write(UC_ARM_REG_SP,0x2000fff0)
    u.reg_write(UC_ARM_REG_LR,0x18001)
    for reg,value in zip((UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2),args):
        u.reg_write(reg,value)
    u.emu_start(address|1,0x18000,count=5000)
    if chosen is None and u.reg_read(UC_ARM_REG_PC)!=0x18000:
        raise RuntimeError('Unexpected termination')
    return u.reg_read(UC_ARM_REG_R0)

transitions={}
for mode,charge,startup in [('startup',0,1),('running',0,0),('charging',1,0)]:
    changes=[]; last=None
    for adc in range(4096):
        u.mem_write(ram,bytes(0x40))
        u.mem_write(ram+startup_offset,bytes([startup]))
        call(selector,adc,charge,0)
        if chosen!=last:
            changes.append(dict(adc=adc,handler=chosen,mv=adc*0x3ca50//0x38000))
            last=chosen
    transitions[mode]=changes
errors=[]
for correction in (0,1):
    for adc in range(4096):
        u.mem_write(ram,bytes(0x40))
        u.mem_write(ram+correction_offset,bytes([1-correction]))
        mv=call(convert,adc,1)
        encoded=call(encode)+300
        expected_mv=adc*0x3ca50//0x38000
        expected=max(300,min(500,expected_mv//10))
        if correction and expected in (368,369):expected=370
        if (mv,encoded)!=(expected_mv,expected): errors.append([adc,correction,mv,encoded])
result=dict(sha256=hashlib.sha256(blob).hexdigest(),flash_base='0x1000',
            mouse_image_version=version,led_transitions=transitions,
            arithmetic_cases=8192,arithmetic_mismatches=errors,
            limitations=['Firmware image was not read back from attached mouse flash.',
                        'Voltage unit inferred from conversion and battery range.',
                        'This verifies code behavior, not remaining physical capacity.'])
result['family']=known['family']
if transitions != known['led_transitions'] or errors:
    raise SystemExit('Firmware behavior differs from recorded evidence.')
print(json.dumps(result,indent=2))
