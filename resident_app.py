"""One persistent Tk process owns both the panel and native notification icons."""
from __future__ import annotations
import ctypes
import os
import time
import threading
from ctypes import wintypes
import tray_common as tc
import tray_app as native
import win_icon
import hid_win as hw

U, K = tc.USER32, tc.KERNEL32
U.CreatePopupMenu.restype = ctypes.c_void_p
U.CreatePopupMenu.argtypes = []
U.AppendMenuW.restype = wintypes.BOOL
U.AppendMenuW.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
U.DestroyMenu.argtypes = [ctypes.c_void_p]
U.DestroyMenu.restype = wintypes.BOOL
U.TrackPopupMenu.restype = wintypes.UINT
U.TrackPopupMenu.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
U.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
U.GetCursorPos.restype = wintypes.BOOL
U.SendMessageTimeoutW.restype = ctypes.c_ssize_t
U.SendMessageTimeoutW.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_size_t,
                                 ctypes.c_ssize_t, wintypes.UINT, wintypes.UINT,
                                 ctypes.POINTER(ctypes.c_size_t)]
OPEN, REFRESH, AUTOSTART, EXIT = 1101, 1102, 1103, 1104
UPDATE_ICONS, STOP_NATIVE = native.WM_APP + 20, native.WM_APP + 21

class DeviceNotificationFilter(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('kind',wintypes.DWORD),('reserved',wintypes.DWORD),
              ('class_guid',hw.GUID),('name',ctypes.c_wchar*1)]

U.RegisterDeviceNotificationW.restype=ctypes.c_void_p
U.RegisterDeviceNotificationW.argtypes=[ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD]
U.UnregisterDeviceNotification.restype=wintypes.BOOL
U.UnregisterDeviceNotification.argtypes=[ctypes.c_void_p]

def runtime_status():
    hwnd = tc.tray_hwnd()
    bits = ctypes.c_size_t()
    response = 0
    if hwnd:
        msg = U.RegisterWindowMessageW(tc.MSG_STATUS)
        response = U.SendMessageTimeoutW(hwnd, msg, 0, 0, 2, 1000, ctypes.byref(bits))
    return {"running": bool(response and bits.value & 1),
            "panel_visible": bool(bits.value & 2), "tray_ready": bool(bits.value & 4),
            "reading": bool(bits.value & 8), "headset_readable": bool(bits.value & 16),
            "mouse_voltage_readable": bool(bits.value & 64),
            "razer_readable": bool(bits.value & 128),
            "device_notifications": bool(bits.value & 256),
            "device_count": (bits.value >> 16) & 255,
            "receiver_count": (bits.value >> 24) & 255,
            "icon_count": (bits.value >> 32) & 255,
            "auto_refresh": bool(bits.value & 32),
            "autostart": tc.is_autostart_enabled(), "config_path": tc.CONFIG_PATH}

class ResidentTray(native.TrayApp):
    def __init__(self, dashboard):
        super().__init__()
        self.dashboard = dashboard
        self.root = dashboard.root
        self.stopping = False
        self.icon_ids = set()
        self.icon_entries = [(1, "app", "PULSE", False)]
        self.device_uids = {}
        self.next_uid = 2
        self.pending_rows = ()
        self.device_notification = None
        self.started = threading.Event()
        self.thread = None
        self.start_error = None
        self.messages = {name: U.RegisterWindowMessageW(value) for name, value in
                         {"show":tc.MSG_SHOW,"hide":tc.MSG_HIDE,"status":tc.MSG_STATUS,
                          "refresh":tc.MSG_REFRESH,"exit":tc.MSG_EXIT}.items()}

    def start(self):
        self.thread = threading.Thread(target=self._native_loop,name="PULSE tray",daemon=True)
        self.thread.start()
        if not self.started.wait(8):
            raise OSError("PULSE 托盘启动超时")
        if self.start_error:
            raise self.start_error

    def _native_loop(self):
        try:
            self._create_window()
            self.started.set()
            msg = native.MSG()
            while U.GetMessageW(ctypes.byref(msg),None,0,0)>0:
                U.TranslateMessage(ctypes.byref(msg))
                U.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            self.start_error = exc
            self.started.set()
        finally:
            self._destroy_native()

    def _create_window(self):
        self.wndproc_ref = native.WNDPROC(self.wnd_proc)
        wc = native.WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(wc)
        wc.lpfnWndProc = self.wndproc_ref
        wc.hInstance = K.GetModuleHandleW(None)
        wc.lpszClassName = tc.WND_CLASS
        if not U.RegisterClassExW(ctypes.byref(wc)):
            raise OSError(ctypes.get_last_error(), "无法注册 PULSE 后台窗口")
        self.hwnd = U.CreateWindowExW(0,tc.WND_CLASS,tc.APP_NAME,0,0,0,0,0,
                                      None,None,wc.hInstance,None)
        if not self.hwnd:
            raise OSError(ctypes.get_last_error(), "无法启动 PULSE 托盘")
        self.add_icons()
        if not self.icons_added:
            raise OSError("无法创建完整托盘图标，已停止后台启动")
        device_filter=DeviceNotificationFilter()
        device_filter.size=ctypes.sizeof(device_filter)
        device_filter.kind=5  # DBT_DEVTYP_DEVICEINTERFACE
        device_filter.class_guid=hw.HID_GUID
        self.device_notification=U.RegisterDeviceNotificationW(self.hwnd,ctypes.byref(device_filter),0)
        os.makedirs(tc.DATA_DIR,exist_ok=True)
        tc.write_pid()

    def add_icons(self):
        self.icon_ids.clear()
        for uid,key,title,plate in self.icon_entries:
            if key == 'app':
                self.levels[key] = (None, '尚未发现电量设备；点击打开面板')
            if self._apply_icon(native.NIM_ADD,uid,key,title,plate):
                self.icon_ids.add(uid)
        self.icons_added = bool(self.icon_ids)

    def remove_icons(self):
        for uid in list(self.icon_ids):
            nid=self._nid(uid)
            nid.uFlags=0
            native.SHELL32.Shell_NotifyIconW(native.NIM_DELETE,ctypes.byref(nid))
        self.icon_ids.clear()
        self.icons_added=False

    def update_icons(self):
        rows=self.pending_rows
        entries=[]
        levels={}
        voltages={}
        bands={}
        for row in rows:
            if not row.present:continue
            if row.key not in self.device_uids:
                self.device_uids[row.key]=self.next_uid
                self.next_uid+=1
            uid=self.device_uids[row.key]
            entries.append((uid,row.key,row.name,row.kind!='mouse' or row.protocol!='zowie'))
            levels[row.key]=(row.level,row.message)
            voltages[row.key]=row.voltage
            bands[row.key]=row.display_band
        if not entries:
            entries=[(1,'app','PULSE',False)]
            levels['app']=(None,'尚未发现电量设备；点击打开面板')
        desired={e[0] for e in entries}
        for uid in self.icon_ids-desired:
            nid=self._nid(uid);nid.uFlags=0
            native.SHELL32.Shell_NotifyIconW(native.NIM_DELETE,ctypes.byref(nid))
        self.icon_ids.intersection_update(desired)
        self.icon_entries=entries
        self.levels,self.voltages,self.bands=levels,voltages,bands
        for uid,key,title,plate in entries:
            action=native.NIM_MODIFY if uid in self.icon_ids else native.NIM_ADD
            if self._apply_icon(action,uid,key,title,plate):self.icon_ids.add(uid)
            else:self.icon_ids.discard(uid)
        self.icons_added=bool(self.icon_ids)

    def update_readings(self, rows):
        # Publish an immutable snapshot. The native thread owns icon state;
        # it never calls Tk and never consumes half-updated dictionaries.
        self.pending_rows=tuple(rows)
        if self.hwnd:U.PostMessageW(self.hwnd,UPDATE_ICONS,0,0)

    def _apply_icon(self, action, uID, key, title, plate):
        voltage=getattr(self,"voltages",{}).get(key)
        if voltage is None or self.levels.get(key,(None,''))[0] is not None:
            return super()._apply_icon(action,uID,key,title,plate)
        nid=self._nid(uID)
        band=getattr(self,"bands",{}).get(key,"")
        glyph,color={"高档":("H",(93,220,138)),"中档":("M",(245,196,81)),
                     "低档":("L",(255,107,107))}.get(band,("V",(150,155,160)))
        nid.hIcon=win_icon.make_hicon(glyph,color,plate)
        nid.szTip=f"{title}：{band or '档位未知'} · {voltage:.2f} V；电压估算；缓存采样时间未知"
        return bool(native.SHELL32.Shell_NotifyIconW(action,ctypes.byref(nid)))

    def open_ui(self):
        self.dashboard.messages.put(("action","show"))

    def refresh_now(self):
        self.dashboard.messages.put(("action","refresh"))

    def handle_command(self, command):
        if command == OPEN:
            self.open_ui()
        elif command == REFRESH:
            self.refresh_now()
        elif command == AUTOSTART:
            self.dashboard.messages.put(("action","autostart"))
        elif command == EXIT:
            self.dashboard.messages.put(("action","exit"))

    def show_menu(self):
        menu = U.CreatePopupMenu()
        if not menu:
            return
        try:
            U.AppendMenuW(menu,0,OPEN,"打开电量面板")
            U.AppendMenuW(menu,0,REFRESH,"刷新电量")
            U.AppendMenuW(menu,8 if tc.is_autostart_enabled() else 0,AUTOSTART,"开机自动运行")
            U.AppendMenuW(menu,0x800,0,None)
            U.AppendMenuW(menu,0,EXIT,"退出 PULSE")
            point = wintypes.POINT()
            U.GetCursorPos(ctypes.byref(point))
            U.SetForegroundWindow(self.hwnd)
            command = U.TrackPopupMenu(menu,0x100|0x2,point.x,point.y,0,self.hwnd,None)
            U.PostMessageW(self.hwnd,native.WM_NULL,0,0)
            self.handle_command(command)
        finally:
            U.DestroyMenu(menu)

    def wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == self.messages.get("status"):
            return (1 | (2 if self.dashboard.visible else 0)
                    | (4 if self.icons_added else 0) | (8 if self.dashboard.busy else 0)
                    | (16 if any(r.protocol=="edifier" and r.level is not None for r in self.dashboard.rows) else 0)
                    | (64 if any(r.protocol=="zowie" and r.voltage is not None for r in self.dashboard.rows) else 0)
                    | (128 if any(r.protocol=="razer" and r.level is not None for r in self.dashboard.rows) else 0)
                    | (256 if self.device_notification else 0)
                    | (32 if self.dashboard.auto else 0)
                    | (min(255,sum(r.present for r in self.dashboard.rows)) << 16)
                    | (min(255,sum(r.receiver for r in self.dashboard.rows)) << 24)
                    | (min(255,len(self.icon_ids)) << 32))
        if msg == self.messages.get("show"):
            self.open_ui()
        elif msg == self.messages.get("hide"):
            self.dashboard.messages.put(("action","hide"))
        elif msg == self.messages.get("refresh"):
            self.refresh_now()
        elif msg in (self.messages.get("exit"),self.msg_quit):
            self.dashboard.messages.put(("action","exit"))
        elif msg == 0x0219:  # WM_DEVICECHANGE; input remains on the Tk queue.
            if int(wparam) in (7,0x8000,0x8004):
                self.dashboard.messages.put(("action","devices_changed"))
            return 1
        elif msg == native.MSG_TRAY_CALLBACK:
            event = int(lparam) & 0xFFFF
            if event in (native.WM_LBUTTONUP,native.WM_LBUTTONDBLCLK):
                self.open_ui()
            elif event == native.WM_RBUTTONUP:
                self.show_menu()
        elif msg == self.msg_reload:
            self.dashboard.messages.put(("action","reload"))
        elif msg == UPDATE_ICONS:
            self.update_icons()
        elif msg == STOP_NATIVE:
            self.remove_icons()
            self.icon_ids.clear()
            self.icons_added=False
            U.DestroyWindow(hwnd)
            self.hwnd=None
        elif msg == self.taskbar_created_msg:
            self.add_icons()
        elif msg == native.WM_DESTROY:
            U.PostQuitMessage(0)  # Ends only the separate native tray thread's loop.
            return 0
        else:
            return U.DefWindowProcW(hwnd,msg,wparam,lparam)
        return 0

    def shutdown(self):
        if self.stopping:
            return
        self.stopping = True
        if self.hwnd:
            U.PostMessageW(self.hwnd,STOP_NATIVE,0,0)
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=4)

    def _destroy_native(self):
        if self.device_notification:
            U.UnregisterDeviceNotification(self.device_notification)
            self.device_notification=None
        if self.hwnd:
            self.remove_icons()
            hwnd,self.hwnd = self.hwnd,None
            U.DestroyWindow(hwnd)
        self.icon_ids.clear()
        win_icon.destroy_all()
        win_icon.cleanup_files()
        tc.remove_pid()

def run(background=False):
    mutex = K.CreateMutexW(None,False,tc.MUTEX_NAME)
    if not mutex:
        raise OSError("无法创建 PULSE 单实例锁")
    try:
        if ctypes.get_last_error()==183:
            # The first instance may still be constructing Tk and its tray window.
            until=time.monotonic()+8
            while not tc.tray_hwnd() and time.monotonic()<until:
                time.sleep(.05)
            if not tc.tray_hwnd():
                raise OSError("已有 PULSE 实例未响应，请退出旧实例后重试")
            if not background:
                tc.post_to_tray(tc.MSG_SHOW)
            return
        from modern_ui import build_ui
        root = build_ui(start_hidden=background)
        tray = ResidentTray(root.dashboard)
        root.dashboard.tray = tray
        try:
            tray.start()
            root.mainloop()
        finally:
            tray.shutdown()
    finally:
        K.CloseHandle(mutex)
