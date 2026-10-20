"""Cross-process serialization per physical HID device, with bounded lock wait."""

import ctypes
import hashlib
from contextlib import contextmanager
from ctypes import wintypes
import hid_win as hw

K = ctypes.WinDLL("kernel32", use_last_error=True)
K.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
K.CreateMutexW.restype = wintypes.HANDLE
K.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
K.WaitForSingleObject.restype = wintypes.DWORD
K.ReleaseMutex.argtypes = [wintypes.HANDLE]
K.CloseHandle.argtypes = [wintypes.HANDLE]


@contextmanager
def serialized(items):
    identity = getattr(items[0], "group", "") or hw.physical_group_key(items[0].path)
    name = "Local\\PULSE.HID." + hashlib.sha256(identity.encode()).hexdigest()[:32]
    handle = K.CreateMutexW(None, False, name)
    if not handle:
        raise hw.HidError("无法创建设备查询锁")
    owned = False
    try:
        owned = K.WaitForSingleObject(handle, 1500) in (0, 0x80)
        if not owned:
            raise hw.HidError("设备正被其他电量读取器使用")
        yield
    finally:
        if owned:
            K.ReleaseMutex(handle)
        K.CloseHandle(handle)
