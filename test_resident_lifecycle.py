"""Exercise the actual resident app over its own IPC; no mouse/keyboard automation.

python test_resident_lifecycle.py [path/to/PULSE.exe]
Requires no existing v2 resident. Startup shortcuts use a temporary test directory.
"""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import resident_app as resident
import tray_common as tc
import tray_app as native
import win_shell

def check(condition, message):
    if not condition:
        raise AssertionError(message)

def wait_for(predicate, label, timeout=12):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        state=resident.runtime_status()
        if predicate(state):
            return state
        time.sleep(.08)
    raise AssertionError(label+": "+json.dumps(state,ensure_ascii=False))

check(not resident.runtime_status()["running"], "先退出正在运行的 v2 PULSE")
command=[sys.argv[1]] if len(sys.argv)>1 else [sys.executable,str(Path(__file__).with_name("pulse_app.py"))]
env=dict(os.environ,PYINSTALLER_RESET_ENVIRONMENT="1")
process=subprocess.Popen(command+["--background"],env=env)
results=[]
try:
    wait_for(lambda s:s["running"] and s["tray_ready"] and not s["panel_visible"],"后台启动")
    results.append("后台启动：无面板，两个托盘图标已创建")
    hwnd=tc.tray_hwnd()
    for index in range(3):
        # Exactly the callback Windows sends for clicking either notification icon.
        tc.USER32.PostMessageW(hwnd,native.MSG_TRAY_CALLBACK,1,native.WM_LBUTTONUP)
        wait_for(lambda s:s["panel_visible"],"托盘点击打开")
        tc.post_to_tray(tc.MSG_HIDE)
        wait_for(lambda s:s["running"] and not s["panel_visible"],"关闭面板保留后台")
        check(tc.tray_hwnd()==hwnd,"恢复面板不应创建新的后台窗口")
    results.append("连续三次：托盘点击打开 → 关闭面板 → 保持同一后台实例")
    duplicate=subprocess.Popen(command,env=env)
    duplicate.wait(timeout=12)
    wait_for(lambda s:s["panel_visible"],"重复启动恢复面板")
    check(tc.tray_hwnd()==hwnd,"重复启动创建了多个实例")
    results.append("重复双击启动：恢复已有面板，没有第二个后台实例")
    tc.post_to_tray(tc.MSG_HIDE)
    wait_for(lambda s:not s["panel_visible"] and not s["reading"],"隐藏后读取")
    tc.post_to_tray(tc.MSG_REFRESH)
    wait_for(lambda s:s["headset_readable"] and not s["reading"],"后台读取耳机电量")
    results.append("窗口隐藏时仍能直接读取耳机电量")
    wait_for(lambda s:s["mouse_voltage_readable"] and not s["reading"],"后台读取卓威电压")
    results.append("窗口隐藏时仍能读取卓威接收器电压缓存")
    # Exercise real COM creation/readback/deletion without touching the user's Startup.
    original_link=tc.startup_lnk
    original_set=tc._set_startup_approved
    original_clear=tc._clear_startup_approved
    original_legacy=tc._clear_legacy_autostart
    original_frozen=tc.FROZEN
    original_executable=sys.executable
    try:
        with tempfile.TemporaryDirectory(prefix="pulse-startup-test-") as folder:
            tc.startup_lnk=lambda:os.path.join(folder,"PULSE-test.lnk")
            tc._set_startup_approved=lambda _enabled:None
            tc._clear_startup_approved=lambda:None
            tc._clear_legacy_autostart=lambda:False
            if len(sys.argv)>1:
                tc.FROZEN=True
                sys.executable=str(Path(sys.argv[1]).resolve())
            check(tc.set_autostart(True),"自启快捷方式创建失败")
            link=win_shell.read_shortcut(tc.startup_lnk())
            check("--background" in link.get("arguments",""),"自启参数不是后台模式")
            check(os.path.normcase(link["target"])==os.path.normcase(tc.tray_command()[0]),"自启目标不正确")
            check(tc.set_autostart(False),"自启快捷方式删除失败")
    finally:
        tc.startup_lnk=original_link
        tc._set_startup_approved=original_set
        tc._clear_startup_approved=original_clear
        tc._clear_legacy_autostart=original_legacy
        tc.FROZEN=original_frozen
        sys.executable=original_executable
    results.append("开机自启：真实快捷方式创建/参数读回/删除通过（临时测试目录）")
    tc.post_to_tray(tc.MSG_EXIT)
    wait_for(lambda s:not s["running"],"彻底退出")
    process.wait(timeout=12)
    check(process.returncode==0,"退出代码非零")
    check(not Path(tc.PID_FILE).exists(),"退出后残留 PID 文件")
    results.append("彻底退出：托盘窗口与进程退出，无残留 PID 文件")
finally:
    if resident.runtime_status()["running"]:
        tc.post_to_tray(tc.MSG_EXIT)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
for result in results:
    print("PASS",result)
