"""Resident dashboard: glass presentation with independent device polling."""
from __future__ import annotations
import ctypes
import queue
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk
import device_service as service
import tray_common as tc
import zowie_profiles as profiles
from headset_recovery import HeadsetRecovery
from glass_view import GlassView, BG, SURFACE, INK, MUTED, CYAN, window_theme


class Dashboard(GlassView):
    def __init__(self, root):
        self.root=root
        root.title(tc.UI_TITLE)
        root.geometry("1200x800")
        root.minsize(1000,720)
        root.configure(bg=BG)
        window_theme(root)
        self._widget_theme()
        self.cfg=tc.load_config()
        self.rows=service.initial_readings()
        self.messages=queue.Queue()
        self.page="devices"
        self.busy=False
        self.auto=self.cfg["auto_refresh"]
        self.tray=None
        self.notice=""
        self.timer=None
        self.recovery=HeadsetRecovery()
        self.next_refresh=time.monotonic()
        self.resize_timer=None
        self.tray_busy=False
        self.details_window=None
        self.model_window=None
        self.device_page=0
        self.devices_dirty=False
        self.discovery_timer=None
        self.closed=False
        self.visible=root.state()!="withdrawn"
        self.c=tk.Canvas(root,bg=BG,highlightthickness=0,bd=0)
        self.c.pack(fill="both",expand=True)
        self._init_glass()
        self.c.bind("<Configure>",self.resize)
        root.bind("<Control-r>",lambda _:self.refresh())
        root.bind("<Control-comma>",lambda _:self.navigate("settings"))
        root.bind("<Escape>",lambda _:self.navigate("devices"))
        root.protocol("WM_DELETE_WINDOW",self.close)
        root.after(150,self.refresh)
        root.after(180,self.pump)
        self.draw()

    def choose_model(self,row=None):
        if self.model_window and self.model_window.winfo_exists():
            self.model_window.lift()
            return
        win=tk.Toplevel(self.root)
        self.model_window=win
        win.title("选择卓威鼠标型号")
        win.geometry(f"620x430+{self.root.winfo_rootx()+max(0,(self.root.winfo_width()-620)//2)}+{self.root.winfo_rooty()+90}")
        win.resizable(False,False)
        win.configure(bg=BG)
        win.transient(self.root)
        window_theme(win)
        tk.Label(win,text="按实际型号匹配固件规则",bg=BG,fg=INK,
                 font=("Microsoft YaHei UI",17,"bold")).pack(anchor="w",padx=26,pady=(24,12))
        tk.Label(win,text=(row.name+" · 仅设置此接收器" if row else "单接收器默认型号；多接收器请在各自详情中设置。"),bg=BG,fg=MUTED,
                 font=("Microsoft YaHei UI",10)).pack(anchor="w",padx=26)
        names=["未选择（仅读电压）"]+list(profiles.PROFILES)
        current=row.model if row else self.cfg['zowie_model']
        selected=tk.StringVar(value=names[list(profiles.MODEL_CHOICES).index(current)])
        combo=ttk.Combobox(win,textvariable=selected,values=names,state="readonly",style="Pulse.TCombobox",font=("Microsoft YaHei UI",12))
        combo.pack(fill="x",padx=26,pady=20)
        info=tk.Label(win,bg=BG,fg=MUTED,justify="left",anchor="nw",wraplength=560,
                      font=("Microsoft YaHei UI",10))
        info.pack(fill="x",padx=26)
        def update(_event=None):
            model=profiles.MODEL_CHOICES[names.index(selected.get())]
            p=profiles.get_profile(model)
            text=(p.evidence+"\n" if p else "选择准确型号后才启用档位。\n")
            if p and p.thresholds:
                text+="已核对的鼠标固件："+" / ".join(p.firmwares)+"\n"
            info.configure(text=text+profiles.policy_text(model)+"\n\n接收器电压为缓存；高档不代表已充满。")
        combo.bind("<<ComboboxSelected>>",update)
        def apply():
            if self.busy:
                info.configure(text="正在读取设备，请稍后点击保存。")
                return
            model=profiles.MODEL_CHOICES[names.index(selected.get())]
            if row:
                self.cfg['zowie_models']=dict(self.cfg['zowie_models'])
                self.cfg['zowie_models'][row.key.split(':',1)[1]]=model
            else:self.cfg["zowie_model"]=model
            tc.save_config(self.cfg)
            win.destroy()
            self.draw()
            self.refresh()
        ttk.Button(win,text="保存并刷新",command=apply,style="Pulse.TButton").pack(side="bottom",anchor="e",padx=26,pady=22)
        update()

    def set_interval(self,seconds):
        self.cfg["interval"]=seconds
        self.next_refresh=time.monotonic()+seconds
        tc.save_config(self.cfg)
        tc.post_to_tray(tc.MSG_RELOAD)
        self.schedule()
        self.draw()
        self.feedback(f"刷新间隔已设为 {seconds} 秒")

    def toggle_auto(self):
        self.auto=not self.auto
        self.cfg["auto_refresh"]=self.auto
        tc.save_config(self.cfg)
        self.schedule()
        self.draw()
        self.feedback("自动同步已开启" if self.auto else "自动同步已暂停")

    def toggle_boot(self):
        desired=not tc.is_autostart_enabled()
        ok=tc.set_autostart(desired)
        self.cfg["autostart"]=tc.is_autostart_enabled()
        tc.save_config(self.cfg)
        self.notice=("已开启开机启动" if desired else "已关闭开机启动") if ok else "设置失败，请检查启动目录权限"
        self.draw()
        self.feedback(self.notice)

    def reload_config(self):
        self.cfg=tc.load_config()
        self.auto=self.cfg["auto_refresh"]
        self.schedule()
        self.draw()
        self.refresh()

    def refresh(self, retry_keys=None):
        if self.busy or self.closed:
            return
        if self.timer:
            self.root.after_cancel(self.timer)
            self.timer=None
        self.busy=True
        if retry_keys is None:
            self.notice=""
            self.draw()
        def work():
            try:
                rows=service.read_all() if retry_keys is None else service.retry_headsets(retry_keys)
                self.messages.put(("readings",rows) if retry_keys is None
                                  else ("headset_readings",(retry_keys,rows)))
            except Exception as exc:
                self.messages.put(("error",(retry_keys,str(exc))))
        threading.Thread(target=work,daemon=True).start()

    def scheduled_refresh(self):
        self.timer=None
        if self.closed or not self.auto or self.busy:
            return
        now=time.monotonic()
        if now>=self.next_refresh:
            self.refresh()
        else:
            keys=self.recovery.due_keys(now)
            if keys:self.refresh(retry_keys=keys)
            else:self.schedule()

    def devices_changed(self):
        self.devices_dirty=True
        if self.discovery_timer:self.root.after_cancel(self.discovery_timer)
        self.discovery_timer=self.root.after(750,self.refresh_devices)

    def refresh_devices(self):
        self.discovery_timer=None
        if self.closed or self.busy:return
        self.devices_dirty=False
        self.refresh()

    def schedule(self):
        if self.timer:
            self.root.after_cancel(self.timer)
            self.timer=None
        if self.auto and not self.closed and not self.busy:
            delay=max(1,int((self.recovery.next_due(self.next_refresh)-time.monotonic())*1000))
            self.timer=self.root.after(delay,self.scheduled_refresh)

    def pump(self):
        if self.closed:
            return
        try:
            while True:
                kind,payload=self.messages.get_nowait()
                if kind=="readings":
                    # A CLI configuration change may race a read already in flight.
                    self.busy=False
                    zowie_rows=[r for r in payload if r.protocol=='zowie']
                    if any(r.model and r.model!=self.cfg['zowie_models'].get(r.key.split(':',1)[1],
                            self.cfg['zowie_model'] if len(zowie_rows)==1 else 'auto') for r in zowie_rows):
                        self.refresh()
                        continue
                    self.rows=payload
                    now=time.monotonic()
                    self.next_refresh=now+self.cfg["interval"]
                    self.recovery.record(payload,now,full=True)
                    if self.tray:
                        self.tray.update_readings(payload)
                    self.schedule()
                    if self.devices_dirty and self.discovery_timer is None:self.refresh_devices()
                elif kind=="headset_readings":
                    self.busy=False
                    retry_keys,payload=payload
                    self.recovery.record(payload,time.monotonic(),requested=retry_keys)
                    updates={r.key:r for r in payload}
                    changed=False
                    for index,row in enumerate(self.rows):
                        if row.key not in updates:continue
                        new=updates[row.key]
                        # Ignore timestamps when deciding whether to redraw/rebuild icons.
                        fields=("level","online","charging","stale","present","message","source","attempts")
                        changed |= any(getattr(row,f)!=getattr(new,f) for f in fields)
                        self.rows[index]=new
                    if changed and self.tray:self.tray.update_readings(self.rows)
                    self.schedule()
                    if self.devices_dirty and self.discovery_timer is None:self.refresh_devices()
                    if not changed or not self.visible:continue
                elif kind=="tray":
                    self.tray_busy=False
                    self.notice="托盘已启动" if payload else "托盘启动失败"
                elif kind=="error":
                    self.busy=False
                    retry_keys,_=payload
                    if retry_keys is None:
                        self.next_refresh=time.monotonic()+self.cfg["interval"]
                    else:
                        self.recovery.defer(retry_keys,time.monotonic())
                    self.notice="读取失败，请重试"
                    self.schedule()
                elif kind=="action":
                    if payload=="exit":
                        self.exit_app()
                        return
                    {"show":self.show,"hide":self.close,"refresh":self.refresh,
                     "autostart":self.toggle_boot,"reload":self.reload_config,
                     "devices_changed":self.devices_changed}[payload]()
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
        win.geometry(f"650x480+{self.root.winfo_rootx()+max(0,(self.root.winfo_width()-650)//2)}+{self.root.winfo_rooty()+90}")
        win.configure(bg=BG)
        win.transient(self.root)
        window_theme(win)
        tk.Label(win,text=row.name,font=("Microsoft YaHei UI",18,"bold"),
                 bg=BG,fg=INK).pack(anchor="w",padx=25,pady=(22,8))
        tk.Label(win,text=row.message,font=("Microsoft YaHei UI",10),
                 bg=BG,fg=MUTED).pack(anchor="w",padx=25,pady=(0,15))
        if row.protocol=='zowie':
            ttk.Button(win,text="设置此接收器的鼠标型号",style='Pulse.TButton',
                       command=lambda:self.choose_model(row)).pack(anchor='w',padx=25,pady=(0,12))
        frame=tk.Frame(win,bg=SURFACE)
        frame.pack(fill="both",expand=True,padx=25,pady=(0,25))
        body=tk.Text(frame,font=("Microsoft YaHei UI",10),wrap="word",
                     relief="flat",bg=SURFACE,fg=INK,insertbackground=CYAN,
                     selectbackground="#C6EFE4",selectforeground=INK,padx=18,pady=15)
        scrollbar=ttk.Scrollbar(frame,orient="vertical",command=body.yview,style="Pulse.Vertical.TScrollbar")
        scrollbar.pack(side="right",fill="y")
        body.configure(yscrollcommand=scrollbar.set)
        body.pack(side="left",fill="both",expand=True)
        lines=[f"设备标识：{row.usb_id}",f"连接方式：{row.connection}",
               f"数据来源：{row.source or '无电量数据'}"]
        if row.firmware:
            lines.append("固件："+row.firmware)
        if row.verification:
            lines.append("验证范围："+row.verification)
        if row.voltage is not None:
            lines += [f"最近接收电压：{row.voltage:.2f} V", "采样时间未知；不能直接换算为精确电量百分比。"]
        if row.level is not None:
            lines.append(f"{'上次电量（非当前值）' if row.stale else '设备电量'}：{row.level}%")
        if row.sampled_at:
            lines.append('上次成功读取时间：'+time.strftime('%H:%M:%S',time.localtime(row.sampled_at)))
        if row.raw_level is not None:
            lines.append(f"设备原始电量值：{row.raw_level}")
        if row.display_band and row.protocol=='zowie':
            lines += ["显示档位："+row.display_band+"（按所选型号固件映射推断）",
                      profiles.policy_text(row.model),
                      "分界附近采用缓冲，避免档位反复跳动；高档不表示已充满。"]
        elif row.display_band:
            lines += ['显示档位：'+row.display_band+'（设备协议直接上报，非卓威电压映射）']
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
            self.feedback(self.notice)
            return
        if self.details_window and self.details_window.winfo_exists():
            self.details_window.destroy()
        self._stop_motion()
        self.root.withdraw()
        if self.model_window and self.model_window.winfo_exists():
            self.model_window.destroy()
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
        self._stop_motion()
        self.closed=True
        if self.timer:
            self.root.after_cancel(self.timer)
        if self.discovery_timer:
            self.root.after_cancel(self.discovery_timer)
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
