"""Rounded, dependency-free desktop dashboard built on the original HID backend."""
from __future__ import annotations
import ctypes
import queue
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import scrolledtext
import device_service as service
import tray_common as tc

BG = "#F3F6F7"
INK = "#172F38"
MUTED = "#72858C"
LINE = "#E3EAED"
GREEN = "#117B65"
MINT = "#BCECD8"
AMBER = "#A16B21"
BAND_COLORS = {"高档":GREEN,"中档":AMBER,"低档":"#C85850"}

def rounded(c, x, y, w, h, r=18, **kwargs):
    r = min(r, w / 2, h / 2)
    points = [x+r,y,x+w-r,y,x+w,y,x+w,y+r,x+w,y+h-r,
              x+w,y+h,x+w-r,y+h,x+r,y+h,x,y+h,x,y+h-r,x,y+r,x,y]
    return c.create_polygon(points, smooth=True, splinesteps=32, **kwargs)

class Dashboard:
    def __init__(self, root):
        self.root = root
        self.root.title(tc.UI_TITLE)
        self.root.geometry("1120x760")
        self.root.minsize(1000, 720)
        self.root.configure(bg=BG)
        self.root.update_idletasks()
        self._round_window()
        self.cfg = tc.load_config()
        self.rows = service.initial_readings()
        self.messages = queue.Queue()
        self.page = "devices"
        self.busy = False
        self.auto = self.cfg["auto_refresh"]
        self.tray = None
        self.notice = ""
        self.timer = None
        self.resize_timer = None
        self.tray_busy = False
        self.details_window = None
        self.closed = False
        self.visible = root.state() != "withdrawn"
        self.c = tk.Canvas(root, bg=BG, highlightthickness=0, bd=0)
        self.c.pack(fill="both", expand=True)
        self.c.bind("<Configure>", self.resize)
        root.bind("<Control-r>", lambda _: self.refresh())
        root.bind("<Control-comma>", lambda _: self.navigate("settings"))
        root.bind("<Escape>", lambda _: self.navigate("devices"))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(150, self.refresh)
        root.after(180, self.pump)
        self.draw()

    def _round_window(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            value = ctypes.c_int(2)  # DWMWCP_ROUND on Windows 11.
            dwm = ctypes.WinDLL("dwmapi")
            dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                                 ctypes.c_void_p, ctypes.c_uint]
            dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(value), 4)
        except (OSError, AttributeError):
            pass

    def text(self, x, y, value, size=14, color=INK, weight="normal", **kwargs):
        return self.c.create_text(x, y, text=value, fill=color, anchor="nw",
                                  font=("Microsoft YaHei UI", -size, weight), **kwargs)

    def button(self, x, y, w, h, label, command, fill=INK, color="#FFFFFF", tag=None):
        tag = tag or f"button_{len(self.c.find_all())}"
        shape = rounded(self.c, x, y, w, h, 13, fill=fill, outline="", tags=tag)
        self.c.create_text(x+w/2, y+h/2, text=label, fill=color,
                           font=("Microsoft YaHei UI", -13, "bold"), tags=tag)
        self.c.tag_bind(tag, "<Button-1>", lambda _: command())
        self.c.tag_bind(tag, "<Enter>", lambda _: self.c.configure(cursor="hand2"))
        self.c.tag_bind(tag, "<Leave>", lambda _: self.c.configure(cursor=""))
        return shape

    def pill(self, x, y, label, fill, color, width=120):
        rounded(self.c, x, y, width, 28, 14, fill=fill, outline="")
        self.c.create_oval(x+11,y+11,x+17,y+17,fill=color,outline="")
        self.text(x+24, y+5, label, 11, color)

    def resize(self, _event):
        if self.resize_timer:
            self.root.after_cancel(self.resize_timer)
        self.resize_timer = self.root.after(35, self.draw)

    def navigate(self, page):
        self.page = page
        self.draw()

    def draw(self):
        if self.closed:
            return
        self.resize_timer = None
        c = self.c
        c.delete("all")
        w, h = max(c.winfo_width(), 1000), max(c.winfo_height(), 720)
        c.create_rectangle(0,0,204,h,fill=INK,outline="")
        rounded(c,24,29,34,34,11,fill=MINT,outline="")
        c.create_polygon(43,35,35,46,41,46,38,57,49,43,42,43,fill=INK)
        self.text(68,27,"PULSE",20,"#FFFFFF","bold")
        self.text(69,53,"设备电量",10,"#A8BDC3")
        self.text(27,111,"工作台",11,"#829DA6")
        for page,label,y in [("devices","设备概览",146),("settings","偏好设置",205)]:
            selected = self.page == page
            tag = f"nav_{page}"
            rounded(c,17,y-8,170,45,13,fill="#2B444C" if selected else INK,
                    outline="",tags=tag)
            if page == "devices":
                for dx,dy in [(0,0),(9,0),(0,9),(9,9)]:
                    rounded(c,33+dx,y+5+dy,6,6,2,fill=MINT if selected else "#92ADB5",
                            outline="",tags=tag)
            else:
                c.create_oval(34,y+4,49,y+19,outline=MINT if selected else "#92ADB5",width=2,tags=tag)
                c.create_oval(39,y+9,44,y+14,fill="#92ADB5",outline="",tags=tag)
            self.text(66,y+2,label,14,"#FFFFFF" if selected else "#ACC0C7",tags=tag)
            c.tag_bind(tag,"<Button-1>",lambda _,p=page:self.navigate(p))
            c.tag_bind(tag,"<Enter>",lambda _:c.configure(cursor="hand2"))
            c.tag_bind(tag,"<Leave>",lambda _:c.configure(cursor=""))
        rounded(c,20,h-134,164,82,15,fill="#213B44",outline="")
        self.text(36,h-119,"2.4 GHz",18,MINT,"bold")
        self.text(36,h-90,"你的无线设备，一处掌握",11,"#A6BEC6")
        self.text(26,h-32,"PULSE  /  本地设备面板",10,"#7E9CA6")
        x = 240
        self.text(x,40,"设备概览" if self.page == "devices" else "偏好设置",27,INK,"bold")
        self.text(x,84,"让每一次连接，都心中有数。" if self.page == "devices"
                  else "按照你的习惯，保持电量同步。",13,MUTED)
        self.button(w-178,43,138,42,"同步中…" if self.busy else "↻  刷新电量",
                    self.refresh,fill="#637D85" if self.busy else INK,tag="refresh")
        if self.page == "devices":
            self.draw_devices(x,w,h)
        else:
            self.draw_settings(x,w,h)
        last = max((r.checked_at for r in self.rows),default=0)
        stamp = time.strftime("%H:%M:%S",time.localtime(last)) if last else "等待首次同步"
        self.text(x,h-35,"●  " + ("正在同步设备…" if self.busy else f"更新于 {stamp}"),11,MUTED)
        self.c.create_text(w-40,h-28,text=self.notice or "Ctrl + R  刷新",anchor="e",
                           font=("Microsoft YaHei UI",-11),fill=MUTED)

    def draw_devices(self, x, w, h):
        receivers = sum(r.receiver for r in self.rows)
        self.pill(x,125,f"{receivers} 个接收器已连接","#E5EEE9",GREEN,156)
        interval = self.cfg["interval"]
        self.c.create_text(w-40,139,text=(f"自动同步  ·  每 {interval} 秒" if self.auto else "自动同步已暂停"),
                           anchor="e",fill=MUTED,font=("Microsoft YaHei UI",-12))
        gap, right = 22, w-40
        cw = (right-x-gap)/2
        bottom = h-183
        for i,row in enumerate(self.rows):
            self.draw_card(x+i*(cw+gap),177,cw,bottom-177,row,i)
        y = h-157
        rounded(self.c,x,y,right-x,91,19,fill="#FFFFFF",outline=LINE)
        good = sum(r.level is not None or r.voltage is not None for r in self.rows)
        rounded(self.c,x+19,y+20,44,44,13,fill="#E5F2EB",outline="")
        self.c.create_line(x+31,y+42,x+38,y+49,x+51,y+34,fill=GREEN,width=2.4,
                           capstyle="round",joinstyle="round")
        self.text(x+79,y+17,f"已同步 {good} 台设备的电量" if not self.busy else "正在与设备同步",15,INK,"bold")
        unavailable = [r for r in self.rows if r.receiver and r.level is None and r.voltage is None]
        if unavailable:
            hint = "卓威接收器已就绪，电量暂不可用。" if unavailable[0].key == "mouse" else unavailable[0].message
        else:
            hint = "鼠标显示电量档位，耳机显示设备返回的百分比。" if receivers else "插入接收器并开启设备后，点击刷新。"
        self.text(x+79,y+46,hint,12,MUTED)

    def draw_card(self, x, y, w, h, row, index):
        c = self.c
        rounded(c,x,y+3,w,h,22,fill="#E9EEF0",outline="")
        rounded(c,x,y,w,h,22,fill="#FFFFFF",outline=LINE)
        self.text(x+25,y+22,"0"+str(index+1)+"  /  "+("MOUSE" if row.key=="mouse" else "HEADSET"),10,MUTED,"bold")
        if row.voltage is not None:
            color=BAND_COLORS.get(row.display_band,GREEN)
            label="电量"+row.display_band if row.display_band else "已读取电压"
            fill={"高档":"#E2F3EB","中档":"#F8EEDB","低档":"#F9E9E6"}.get(row.display_band,"#E2F3EB")
        elif row.level is not None:
            label,fill,color = ("正在充电","#E2F3EB",GREEN) if row.charging else ("已连接","#E2F3EB",GREEN)
        elif row.receiver:
            label,fill,color = "接收器已连接","#F8EEDB",AMBER
        else:
            label,fill,color = "未连接","#EDF0F2",MUTED
        self.pill(x+w-141,y+17,label,fill,color,116)
        self.text(x+25,y+62,row.name,21,INK,"bold")
        self.text(x+25,y+94,row.category+"  ·  "+row.connection,12,MUTED)
        art_x, art_y = x+w-105, y+176
        self.device_art(art_x,art_y,row.key)
        number = row.display_band or (f"{row.voltage:.2f}" if row.voltage is not None else ("—" if row.level is None else str(row.level)))
        color = BAND_COLORS.get(row.display_band,INK if row.level is None or row.level>=30 else "#C85850")
        self.text(x+25,y+149,number,48 if row.voltage is not None else 62,color,"bold")
        if row.display_band:
            self.text(x+28,y+207,f"最近接收 {row.voltage:.2f} V",14,MUTED)
        elif row.voltage is not None:
            self.text(x+143,y+178,"V",21,MUTED)
        elif row.level is not None:
            self.text(x+30+len(number)*37,y+182,"%",22,MUTED)
        self.text(x+28,y+235,"电压估算档位 · 采样时间未知" if row.voltage is not None else ("尚未取得电量" if row.level is None else
                  ("正在充电" if row.charging else ("电量偏低，记得充电" if row.level<30 else "剩余电量"))),12,MUTED)
        by = y+h-83
        bar_w=w-54
        for j in range(0 if row.voltage is not None else 24):
            segment=bar_w/24
            active = row.level is not None and j<round(row.level*24/100)
            col = (GREEN if row.level>=30 else "#D2675F") if active else "#EDF1F2"
            rounded(c,x+27+j*segment,by,segment-3,9,3,fill=col,outline="")
        if row.display_band:
            for j,band in enumerate(("低档","中档","高档")):
                selected=band==row.display_band
                bx=x+27+j*bar_w/3
                rounded(c,bx,by-8,bar_w/3-7,25,8,fill=BAND_COLORS[band] if selected else "#EDF1F2",outline="")
                c.create_text(bx+(bar_w/3-7)/2,by+5,text=band[0],fill="#FFFFFF" if selected else MUTED,
                              font=("Microsoft YaHei UI",-12,"bold" if selected else "normal"))
        elif row.voltage is not None:
            self.text(x+27,by-6,"灯档位推断："+row.battery_band if row.battery_band else "尚未取得设备原生百分比",11,MUTED)
        c.create_line(x+25,y+h-52,x+w-25,y+h-52,fill=LINE)
        self.text(x+26,y+h-34,row.source or "等待设备连接",11,
                  AMBER if row.level is None and row.voltage is None and row.receiver else MUTED)
        tag=f"detail_{row.key}"
        self.text(x+w-94,y+h-34,"查看详情  ›",11,INK,"bold",tags=tag)
        c.tag_bind(tag,"<Button-1>",lambda _,r=row:self.details(r))
        c.tag_bind(tag,"<Enter>",lambda _:c.configure(cursor="hand2"))
        c.tag_bind(tag,"<Leave>",lambda _:c.configure(cursor=""))

    def device_art(self,x,y,kind):
        c=self.c
        c.create_oval(x-66,y-66,x+66,y+66,fill="#EDF4F3" if kind=="headset" else "#EEF2F6",outline="")
        if kind=="mouse":
            c.create_oval(x-30,y+43,x+31,y+51,fill="#DDE6EA",outline="")
            rounded(c,x-30,y-48,62,94,29,fill="#304A55",outline="#213B46",width=2)
            c.create_line(x,y-46,x,y-13,fill="#9BAFB6",width=1.4)
            rounded(c,x-4,y-31,8,19,4,fill="#A4C3C8",outline="")
            c.create_arc(x-30,y-30,x+30,y+4,start=185,extent=170,style="arc",outline="#637F8B",width=1.3)
            c.create_line(x-27,y-10,x-27,y+10,fill="#71919A",width=3,capstyle="round")
            c.create_text(x,y+24,text="Z",fill="#A8C0C6",font=("Segoe UI",-15,"bold"))
        else:
            c.create_oval(x-41,y+43,x+43,y+51,fill="#D9E8E3",outline="")
            c.create_arc(x-41,y-49,x+41,y+45,start=0,extent=180,style="arc",outline="#254C4B",width=12)
            c.create_arc(x-33,y-41,x+33,y+37,start=0,extent=180,style="arc",outline="#88B8A7",width=3)
            rounded(c,x-48,y-4,24,48,10,fill="#355D59",outline="")
            rounded(c,x+24,y-4,24,48,10,fill="#355D59",outline="")
            rounded(c,x-43,y+2,12,35,5,fill="#81B29F",outline="")
            rounded(c,x+31,y+2,12,35,5,fill="#81B29F",outline="")
            c.create_line(x-34,y+32,x-23,y+51,x-6,y+51,fill="#254C4B",width=4,capstyle="round",smooth=True)
            rounded(c,x-9,y+47,13,7,3,fill="#254C4B",outline="")

    def toggle(self,x,y,value,callback):
        tag=f"toggle_{len(self.c.find_all())}"
        rounded(self.c,x,y,46,26,13,fill=GREEN if value else "#D6E0E3",outline="",tags=tag)
        self.c.create_oval(x+(24 if value else 4),y+4,x+(42 if value else 22),y+22,
                           fill="#FFFFFF",outline="",tags=tag)
        self.c.tag_bind(tag,"<Button-1>",lambda _:callback())
        self.c.tag_bind(tag,"<Enter>",lambda _:self.c.configure(cursor="hand2"))
        self.c.tag_bind(tag,"<Leave>",lambda _:self.c.configure(cursor=""))

    def draw_settings(self,x,w,h):
        right=w-40
        rounded(self.c,x,139,right-x,h-215,21,fill="#FFFFFF",outline=LINE)
        tx=x+28
        self.text(tx,166,"同步与启动",17,INK,"bold")
        self.text(tx,218,"自动刷新电量",14,INK,"bold")
        self.text(tx,245,"关闭窗口后仍会在后台定时同步。",12,MUTED)
        self.toggle(right-76,222,self.auto,self.toggle_auto)
        self.c.create_line(tx,282,right-28,282,fill=LINE)
        self.text(tx,305,"刷新间隔",14,INK,"bold")
        for i,(seconds,label) in enumerate([(30,"30 秒"),(60,"1 分钟"),(120,"2 分钟"),(300,"5 分钟")]):
            chosen=self.cfg["interval"]==seconds
            self.button(tx+i*111,345,101,37,label,lambda s=seconds:self.set_interval(s),
                        fill=INK if chosen else BG,color="#FFFFFF" if chosen else MUTED)
        self.c.create_line(tx,407,right-28,407,fill=LINE)
        self.text(tx,429,"后台常驻已开启",14,INK,"bold")
        self.text(tx,457,"关闭窗口仅收起面板；点击托盘图标随时恢复。",12,MUTED)
        self.button(right-170,431,140,37,"收起到托盘",self.close,
                    fill="#E8F0ED",color=GREEN)
        self.c.create_line(tx,495,right-28,495,fill=LINE)
        self.text(tx,518,"开机启动托盘",14,INK,"bold")
        self.text(tx,546,"登录 Windows 后自动显示电量图标。",12,MUTED)
        self.toggle(right-76,521,tc.is_autostart_enabled(),self.toggle_boot)
        self.text(tx,h-119,"彻底停止后台运行，请使用右侧退出按钮。",11,MUTED)
        self.button(right-170,h-129,140,37,"退出 PULSE",self.exit_app,
                    fill="#F9E9E6",color="#A4544B")

    def set_interval(self,seconds):
        self.cfg["interval"]=seconds
        tc.save_config(self.cfg)
        tc.post_to_tray(tc.MSG_RELOAD)
        self.schedule()
        self.draw()

    def toggle_auto(self):
        self.auto=not self.auto
        self.cfg["auto_refresh"]=self.auto
        tc.save_config(self.cfg)
        self.schedule()
        self.draw()

    def toggle_boot(self):
        desired=not tc.is_autostart_enabled()
        ok=tc.set_autostart(desired)
        self.cfg["autostart"]=tc.is_autostart_enabled()
        tc.save_config(self.cfg)
        self.notice=("已开启开机启动" if desired else "已关闭开机启动") if ok else "设置失败，请检查启动目录权限"
        self.draw()

    def reload_config(self):
        self.cfg=tc.load_config()
        self.auto=self.cfg["auto_refresh"]
        self.schedule()
        self.draw()

    def refresh(self):
        if self.busy or self.closed:
            return
        self.busy=True
        self.notice=""
        self.draw()
        def work():
            try:
                rows=service.read_all()
                self.messages.put(("readings",rows))
            except Exception as exc:
                self.messages.put(("error",str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def schedule(self):
        if self.timer:
            self.root.after_cancel(self.timer)
            self.timer=None
        if self.auto and not self.closed:
            self.timer=self.root.after(self.cfg["interval"]*1000,self.refresh)

    def pump(self):
        if self.closed:
            return
        try:
            while True:
                kind,payload=self.messages.get_nowait()
                if kind=="readings":
                    self.rows=payload
                    self.busy=False
                    if self.tray:
                        self.tray.update_readings(payload)
                    self.schedule()
                elif kind=="tray":
                    self.tray_busy=False
                    self.notice="托盘已启动" if payload else "托盘启动失败"
                elif kind=="error":
                    self.busy=False
                    self.notice="读取失败，请重试"
                    self.schedule()
                elif kind=="action":
                    if payload=="exit":
                        self.exit_app()
                        return
                    {"show":self.show,"hide":self.close,"refresh":self.refresh,
                     "autostart":self.toggle_boot,"reload":self.reload_config}[payload]()
                self.draw()
        except queue.Empty:
            pass
        self.root.after(200,self.pump)

    def details(self,row):
        if self.details_window and self.details_window.winfo_exists():
            self.details_window.destroy()
        win=tk.Toplevel(self.root)
        self.details_window=win
        win.title(row.name+" · 连接详情")
        win.geometry("650x480")
        win.configure(bg=BG)
        win.transient(self.root)
        tk.Label(win,text=row.name,font=("Microsoft YaHei UI",18,"bold"),
                 bg=BG,fg=INK).pack(anchor="w",padx=25,pady=(22,8))
        tk.Label(win,text=row.message,font=("Microsoft YaHei UI",10),
                 bg=BG,fg=MUTED).pack(anchor="w",padx=25,pady=(0,15))
        body=scrolledtext.ScrolledText(win,font=("Microsoft YaHei UI",10),wrap="word",
                                       relief="flat",bg="#FFFFFF",fg=INK,padx=18,pady=15)
        body.pack(fill="both",expand=True,padx=25,pady=(0,25))
        lines=[f"设备标识：{row.usb_id}",f"连接方式：{row.connection}",
               f"数据来源：{row.source or '无电量数据'}"]
        if row.firmware:
            lines.append("固件："+row.firmware)
        if row.voltage is not None:
            lines += [f"最近接收电压：{row.voltage:.2f} V", "采样时间未知；不能直接换算为精确电量百分比。"]
        if row.display_band:
            lines += ["显示档位："+row.display_band+"（根据电压估算）",
                      "参考对应：≤3.75 V 低档；3.76–3.89 V 中档；≥3.90 V 高档。",
                      "分界附近采用缓冲，避免档位反复跳动；高档不表示已充满。"]
        lines += ["", "读取过程"]+["• "+a for a in row.attempts]
        lines += ["", "检测到的接口"]+[f"• {d['collection']}\n  {d['usage']}  输入 {d['input']} / 输出 {d['output']} / Feature {d['feature']}"
                                         for d in row.interfaces]
        body.insert("1.0","\n".join(lines))
        body.configure(state="disabled")

    def close(self):
        """The title-bar close button hides the panel; the application stays alive."""
        if not self.tray or not self.tray.icons_added:
            self.notice="托盘尚未就绪，请稍后重试"
            self.draw()
            return
        if self.details_window and self.details_window.winfo_exists():
            self.details_window.destroy()
        self.root.withdraw()
        self.visible=False

    def show(self):
        if self.closed:
            return
        self.root.deiconify()
        self.visible=True
        self.root.lift()
        self.root.focus_force()
        self.draw()

    def exit_app(self):
        if self.closed:
            return
        self.closed=True
        if self.timer:
            self.root.after_cancel(self.timer)
        if self.tray:
            self.tray.shutdown()
        self.root.destroy()

def build_ui(start_hidden=False):
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Pulse.WirelessBattery.Desktop")
        ctypes.windll.user32.SetProcessDPIAware()
    except (OSError,AttributeError):
        pass
    root=tk.Tk()
    if start_hidden:
        root.withdraw()
    icon=Path(__file__).with_name("pulse.ico")
    if icon.exists():
        root.iconbitmap(str(icon))
    root.dashboard=Dashboard(root)
    return root
