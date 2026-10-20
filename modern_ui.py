"""Dark gaming dashboard; native Tk canvas artwork and Windows integration."""
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

BG = "#101116"
SIDEBAR = "#14141B"
SURFACE = "#1C1E26"
INK = "#F1F3F5"
MUTED = "#949BA9"
LINE = "#30333E"
CYAN = "#3AE4E5"
LIME = "#D5F647"
GREEN = "#66D9AA"
MINT = CYAN
AMBER = "#FFC16B"
RED = "#FF7788"
BAND_COLORS = {"高档":GREEN,"中档":AMBER,"低档":RED}


def mix(a, b, amount):
    rgb = [round(int(a[i:i+2],16)*(1-amount)+int(b[i:i+2],16)*amount) for i in (1,3,5)]
    return "#"+"".join(f"{v:02x}" for v in rgb)


def rounded(c, x, y, w, h, r=18, **kwargs):
    r = min(r, w / 2, h / 2)
    points = [x+r,y,x+w-r,y,x+w,y,x+w,y+r,x+w,y+h-r,
              x+w,y+h,x+w-r,y+h,x+r,y+h,x,y+h,x,y+h-r,x,y+r,x,y]
    return c.create_polygon(points, smooth=True, splinesteps=32, **kwargs)


def cut_panel(c, x, y, w, h, cut=12, **kwargs):
    return c.create_polygon(x+cut,y,x+w,y,x+w,y+h-cut,x+w-cut,y+h,
                            x,y+h,x,y+cut, smooth=False, **kwargs)


def window_theme(window):
    """Keep the system title bar, including native minimize/close behavior."""
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p,ctypes.c_uint,ctypes.c_void_p,ctypes.c_uint]
        for attribute, value in ((20,1),(33,2),(35,0x1B1414),(36,0xF5F3F1)):
            setting=ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd,attribute,ctypes.byref(setting),4)
    except (OSError,AttributeError):
        pass


class Dashboard:
    def __init__(self, root):
        self.root=root
        root.title(tc.UI_TITLE)
        root.geometry("1120x760")
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
        self.c.bind("<Configure>",self.resize)
        root.bind("<Control-r>",lambda _:self.refresh())
        root.bind("<Control-comma>",lambda _:self.navigate("settings"))
        root.bind("<Escape>",lambda _:self.navigate("devices"))
        root.protocol("WM_DELETE_WINDOW",self.close)
        root.after(150,self.refresh)
        root.after(180,self.pump)
        self.draw()

    def _widget_theme(self):
        style=ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Pulse.TCombobox",fieldbackground=SURFACE,background=LINE,
                        foreground=INK,arrowcolor=CYAN,bordercolor=LINE,lightcolor=LINE,
                        darkcolor=LINE,padding=10,selectbackground="#23454C",selectforeground=INK)
        style.map("Pulse.TCombobox",fieldbackground=[("readonly",SURFACE)],
                  foreground=[("readonly",INK)],bordercolor=[("focus",CYAN)])
        style.configure("Pulse.TButton",background=LIME,foreground=BG,borderwidth=0,
                        padding=(18,10),font=("Microsoft YaHei UI",10,"bold"))
        style.map("Pulse.TButton",background=[("active","#E4FF78")],foreground=[("active",BG)])
        style.configure("Pulse.Vertical.TScrollbar",background=LINE,troughcolor=BG,
                        arrowcolor=MUTED,bordercolor=BG,lightcolor=LINE,darkcolor=LINE,
                        gripcount=0,arrowsize=12)
        style.map("Pulse.Vertical.TScrollbar",background=[("active","#4C6269")])
        self.root.option_add("*TCombobox*Listbox.background",SURFACE)
        self.root.option_add("*TCombobox*Listbox.foreground",INK)
        self.root.option_add("*TCombobox*Listbox.selectBackground","#27555B")
        self.root.option_add("*TCombobox*Listbox.selectForeground",INK)

    def text(self,x,y,value,size=14,color=INK,weight="normal",**kwargs):
        family=kwargs.pop("family","Microsoft YaHei UI")
        return self.c.create_text(x,y,text=value,fill=color,anchor="nw",
                                  font=(family,-size,weight),**kwargs)

    def bind_action(self,tag,command,shape=None,base=None,hover=None):
        self.c.tag_bind(tag,"<Button-1>",lambda _:command())
        def enter(_):
            self.c.configure(cursor="hand2")
            if shape and hover:self.c.itemconfigure(shape,fill=hover)
        def leave(_):
            self.c.configure(cursor="")
            if shape and base:self.c.itemconfigure(shape,fill=base)
        self.c.tag_bind(tag,"<Enter>",enter)
        self.c.tag_bind(tag,"<Leave>",leave)

    def button(self,x,y,w,h,label,command,fill=SURFACE,color=INK,tag=None):
        tag=tag or f"button_{len(self.c.find_all())}"
        shape=cut_panel(self.c,x,y,w,h,7,fill=fill,outline=LINE if fill==SURFACE else fill,tags=tag)
        self.c.create_text(x+w/2,y+h/2,text=label,fill=color,
                           font=("Microsoft YaHei UI",-12,"bold"),tags=tag)
        self.bind_action(tag,command,shape,fill,mix(fill,"#FFFFFF",.09))
        return shape

    def pill(self,x,y,label,fill,color,width=120):
        cut_panel(self.c,x,y,width,26,5,fill=fill,outline=mix(fill,color,.25))
        self.c.create_rectangle(x+10,y+11,x+14,y+15,fill=color,outline="")
        self.text(x+22,y+4,label,11,color)

    def resize(self,_event):
        if self.resize_timer:self.root.after_cancel(self.resize_timer)
        self.resize_timer=self.root.after(35,self.draw)

    def navigate(self,page):
        self.page=page
        self.draw()

    def draw(self):
        if self.closed:return
        self.resize_timer=None
        c=self.c
        c.delete("all")
        w,h=max(c.winfo_width(),1000),max(c.winfo_height(),720)
        # A quiet diagonal lattice, with restrained violet and cyan depth.
        for y in range(0,h,4):
            c.create_rectangle(184,y,w,y+4,fill=mix("#1A141F",BG,min(1,y/(h*.8))),outline="")
        for gx in range(184,w,48):c.create_line(gx,100,gx,h,fill="#1D1D27")
        for gy in range(100,h,48):c.create_line(184,gy,w,gy,fill="#1D1D27")
        for gx in range(184-(h-100),w,140):
            sx,ex=max(184,gx),min(w,gx+h-100)
            if sx<ex:c.create_line(sx,100+sx-gx,ex,100+ex-gx,fill="#23202A")
        c.create_rectangle(0,0,184,h,fill=SIDEBAR,outline="")
        c.create_line(184,0,184,h,fill=LINE)
        c.create_polygon(24,31,30,31,23,53,17,53,fill=LIME)
        c.create_polygon(34,31,38,31,31,53,27,53,fill=LIME)
        self.text(46,25,"PULSE",26,INK,"bold",family="Bahnschrift")
        self.text(25,66,"无线设备电量",11,MUTED)
        c.create_line(24,103,160,103,fill=LINE)
        self.text(25,126,"设备管理",10,"#707786")
        for page,label,y in [("devices","设备概览",168),("settings","偏好设置",226)]:
            chosen=self.page==page
            tag=f"nav_{page}"
            shape=cut_panel(c,14,y-10,156,45,8,fill="#222B32" if chosen else SIDEBAR,
                            outline="#35484C" if chosen else SIDEBAR,tags=tag)
            if chosen:c.create_rectangle(14,y+2,17,y+21,fill=CYAN,outline="",tags=tag)
            col=CYAN if chosen else MUTED
            if page=="devices":
                for dx,dy in [(0,0),(10,0),(0,10),(10,10)]:
                    c.create_rectangle(31+dx,y+3+dy,37+dx,y+9+dy,outline=col,tags=tag)
            else:
                c.create_oval(31,y+3,47,y+19,outline=col,width=1.5,tags=tag)
                c.create_oval(37,y+9,41,y+13,fill=col,outline="",tags=tag)
            self.text(62,y,label,13,INK if chosen else MUTED,"bold" if chosen else "normal",tags=tag)
            self.bind_action(tag,lambda p=page:self.navigate(p),shape,"#222B32" if chosen else SIDEBAR,"#242832")
        cut_panel(c,18,h-146,148,86,10,fill="#1B2429",outline="#2D4145")
        self.text(31,h-130,"2.4",25,CYAN,"bold",family="Bahnschrift")
        self.text(79,h-120,"GHz",12,CYAN,family="Consolas")
        self.text(31,h-93,"后台常驻  /  已就绪" if self.tray else "本地设备连接",10,MUTED)
        self.text(25,h-31,"PULSE  /  DESKTOP",9,"#646C7B",family="Consolas")
        x=216
        self.text(x,26,"设备概览" if self.page=="devices" else "偏好设置",27,INK,"bold")
        self.text(x,68,"设备状态与电量" if self.page=="devices" else "同步、型号与启动选项",11,MUTED)
        self.button(w-168,31,136,42,"同步中…" if self.busy else "↻  刷新电量",self.refresh,
                    fill="#3C4543" if self.busy else LIME,color=BG if not self.busy else INK,tag="refresh")
        c.create_line(216,100,w-32,100,fill=LINE)
        c.create_line(w-168,100,w-32,100,fill=LIME,width=2)
        if self.page=="devices":self.draw_devices(x,w,h)
        else:self.draw_settings(x,w,h)
        last=max((r.checked_at for r in self.rows),default=0)
        stamp=time.strftime("%H:%M:%S",time.localtime(last)) if last else "等待首次同步"
        c.create_rectangle(x,h-30,x+4,h-26,fill=CYAN,outline="")
        self.text(x+13,h-36,"正在同步设备…" if self.busy else f"最近查询  {stamp}",10,MUTED)
        c.create_text(w-32,h-29,text=self.notice or "Ctrl + R  刷新",anchor="e",
                      font=("Microsoft YaHei UI",-10),fill=MUTED)

    def draw_devices(self,x,w,h):
        c=self.c
        receivers=service.receiver_count(self.rows)
        self.pill(x,119,f"{receivers} 个接收器已连接","#1A2C2C",CYAN,158)
        c.create_text(w-32,132,text=f"自动同步 / {self.cfg['interval']} 秒" if self.auto else "自动同步已暂停",
                      anchor="e",fill=MUTED,font=("Microsoft YaHei UI",-11))
        gap=20
        pages=max(1,(len(self.rows)+2)//3)
        self.device_page=min(self.device_page,pages-1)
        visible=self.rows[self.device_page*3:self.device_page*3+3]
        columns=max(1,len(visible))
        card_w=min(480,(w-32-x-gap*(columns-1))/columns)
        card_h=h-282
        for i,row in enumerate(visible):
            draw=self.draw_compact_card if columns==3 else self.draw_card
            draw(x+i*(card_w+gap),165,card_w,card_h,row,self.device_page*3+i)
        if not visible:
            cut_panel(c,x,165,w-32-x,card_h,16,fill=SURFACE,outline=LINE)
            self.text(x+28,199,"尚未发现电量设备",22,INK,"bold")
            self.text(x+28,244,"插入无线接收器或支持标准 HID 电池字段的外设后，将自动显示。",12,MUTED,width=w-100-x)
        y=h-95
        c.create_line(x,y-10,w-32,y-10,fill=LINE)
        good=sum(not r.stale and r.available for r in self.rows)
        self.text(x,y+4,f"{good:02d}",24,CYAN,"bold",family="Bahnschrift")
        self.text(x+42,y+3,"台设备已同步" if not self.busy else "设备同步中",12,INK,"bold")
        unavailable=[r for r in self.rows if r.receiver and not r.available]
        hint=unavailable[0].message if unavailable else f"已发现 {len(self.rows)} 台设备 · 按设备实际协议读取"
        self.text(x+42,y+26,hint,10,MUTED,width=max(240,w-x-270))
        if pages>1:
            self.button(w-210,y+5,36,30,"‹",lambda:self.change_device_page(-1))
            c.create_text(w-129,y+20,text=f"{self.device_page+1} / {pages}",fill=MUTED,font=("Consolas",-12))
            self.button(w-68,y+5,36,30,"›",lambda:self.change_device_page(1))

    def change_device_page(self,delta):
        pages=max(1,(len(self.rows)+2)//3)
        self.device_page=(self.device_page+delta)%pages
        self.draw()

    def draw_compact_card(self,x,y,w,h,row,index):
        c=self.c
        accent=LIME if row.kind=='mouse' and row.protocol=='zowie' else GREEN if row.protocol=='razer' else CYAN
        cut_panel(c,x,y,w,h,14,fill=SURFACE,outline=LINE)
        c.create_line(x+15,y,x+w-15,y,fill=accent,width=2)
        labels={'mouse':'MOUSE','headset':'HEADSET','keyboard':'KEYBOARD','gamepad':'CONTROLLER'}
        self.text(x+18,y+20,f"{index+1:02d} / {labels.get(row.kind,'DEVICE')}",9,accent,"bold",family='Consolas')
        self.text(x+18,y+51,row.name,16,INK,"bold",width=w-36)
        number=row.display_band or (str(row.level) if row.level is not None else f'{row.voltage:.2f}' if row.voltage is not None else '—')
        color=MUTED if row.stale else BAND_COLORS.get(row.display_band,RED if row.level is not None and row.level<30 else INK)
        self.text(x+18,y+110,number,38 if row.display_band else 48,color,'bold',
                  family='Microsoft YaHei UI' if row.display_band else 'Bahnschrift')
        if row.level is not None:
            self.text(x+20+len(number)*26,y+139,'%',15,MUTED,family='Bahnschrift')
        elif row.voltage is not None:
            self.text(x+20,y+161,f'最近接收 {row.voltage:.2f} V',10,MUTED)
        self.device_art(x+w/2,y+230,row.kind,accent,scale=.65)
        note=(f"上次电量 · {time.strftime('%H:%M:%S',time.localtime(row.sampled_at))} · 非当前值" if row.stale else
              '缓存电压 · 采样时间未知' if row.protocol=='zowie' and row.voltage is not None else
              '正在充电' if row.charging else '设备原生档位' if row.display_band else '设备直接返回电量' if row.level is not None else row.message)
        self.text(x+18,y+h-149,note,10,MUTED,width=w-36)
        by=y+h-106
        if row.display_band:
            for j,band in enumerate(('低档','中档','高档')):
                bw=(w-36)/3
                col=BAND_COLORS[band] if row.display_band==band and not row.stale else MUTED
                cut_panel(c,x+18+j*bw,by,bw-4,25,4,fill='#253036',outline=col if row.display_band==band else LINE)
                c.create_text(x+18+(j+.5)*bw-2,by+12,text=band[0],fill=col,font=('Microsoft YaHei UI',-10))
        else:
            for j in range(20):
                bw=(w-36)/20
                active=row.level is not None and j<round(row.level*20/100)
                c.create_rectangle(x+18+j*bw,by+8,x+18+(j+1)*bw-3,by+17,fill=(MUTED if row.stale else RED if row.level<30 else accent) if active else LINE,outline='')
        c.create_line(x+18,y+h-53,x+w-18,y+h-53,fill=LINE)
        self.text(x+18,y+h-38,'2.4G 接收器' if row.receiver else row.connection,9,MUTED)
        tag=f'detail_{row.key}'
        self.text(x+w-72,y+h-38,'详情  ↗',10,accent,'bold',tags=tag)
        self.bind_action(tag,lambda:self.details(row))

    def draw_card(self,x,y,w,h,row,index):
        c=self.c
        accent=LIME if row.protocol=='zowie' else GREEN if row.protocol=='razer' else CYAN
        cut_panel(c,x,y,w,h,16,fill=SURFACE,outline=LINE)
        # Bottom color field is clipped to the card's cut corner.
        for dy in range(0,126,3):
            yy=y+h-126+dy
            end=x+w-max(0,16-(126-dy))
            c.create_line(x+1,yy,end-1,yy,fill=mix(SURFACE,"#173330" if row.kind=="mouse" else "#13383C",dy/150),width=3)
        cut_panel(c,x,y,w,h,16,fill="",outline=LINE)
        c.create_line(x+17,y,x+w-20,y,fill=accent,width=2)
        self.text(x+24,y+22,f"{index+1:02d}  /  "+{'mouse':'MOUSE','headset':'HEADSET','keyboard':'KEYBOARD'}.get(row.kind,'DEVICE'),10,accent,"bold",family="Consolas")
        c.create_text(x+w-24,y+29,text="2.4G" if row.connection=="2.4G 无线" else "USB",anchor="e",
                      fill=MUTED,font=("Consolas",-10))
        # Fit long model names (including Glossy) without clipping at minimum size.
        from tkinter import font as tkfont
        size=21
        while size>15 and tkfont.Font(family="Microsoft YaHei UI",size=-size,weight="bold").measure(row.name)>w-48:size-=1
        self.text(x+24,y+59,row.name,size,INK,"bold")
        self.text(x+24,y+94,row.category,11,MUTED)
        art_x,art_y=x+w-104,y+204
        self.device_art(art_x,art_y,row.kind,accent)
        voltage_only=row.voltage is not None and row.level is None
        number=row.display_band or (str(row.level) if row.level is not None else f"{row.voltage:.2f}" if voltage_only else "—")
        color=MUTED if row.stale else BAND_COLORS.get(row.display_band,INK if row.level is None or row.level>=30 else RED)
        self.text(x+24,y+146,number,47 if voltage_only else 65,color,"bold",
                  family="Microsoft YaHei UI" if row.display_band else "Bahnschrift")
        if row.display_band and row.voltage is not None:
            self.text(x+27,y+211,f"最近接收  {row.voltage:.2f} V",12,MUTED)
        elif row.level is not None:
            self.text(x+26+len(number)*35,y+183,"%",18,MUTED,family="Bahnschrift")
        elif voltage_only:
            self.text(x+142,y+175,"V",18,MUTED,family="Bahnschrift")
        label=(f"上次电量 · {time.strftime('%H:%M:%S',time.localtime(row.sampled_at))} · 非当前值" if row.stale else
               "设备原生档位" if row.display_band and row.voltage is None else "缓存电压 · 采样时间未知" if row.protocol=='zowie' and voltage_only else "设备电压" if voltage_only else "尚未取得电量" if row.level is None
               else "正在充电" if row.charging else "剩余电量")
        self.text(x+27,y+244,label,10,MUTED)
        by=y+h-107
        bar_w=w-48
        if row.display_band:
            for j,band in enumerate(("低档","中档","高档")):
                selected=band==row.display_band
                bx=x+24+j*bar_w/3
                col=MUTED if row.stale else BAND_COLORS[band]
                cut_panel(c,bx,by,bar_w/3-6,28,5,fill=mix("#1D272C",col,.18) if selected else "#222830",
                          outline=col if selected else "#303941")
                c.create_text(bx+(bar_w/3-6)/2,by+14,text=band,
                              fill=col if selected else MUTED,font=("Microsoft YaHei UI",-11,"bold" if selected else "normal"))
        elif voltage_only:
            self.text(x+24,by+6,"档位未验证 · 查看详情",11,AMBER)
        else:
            self.text(x+24,by-20,"电量",10,MUTED)
            for j in range(28):
                segment=bar_w/28
                active=row.level is not None and j<round(row.level*28/100)
                col=(MUTED if row.stale else CYAN if row.level>=30 else RED) if active else "#30363D"
                c.create_rectangle(x+24+j*segment,by+3,x+24+(j+1)*segment-3,by+13,fill=col,outline="")
        c.create_line(x+24,y+h-53,x+w-24,y+h-53,fill="#344047")
        valid=row.voltage is not None or row.level is not None or bool(row.display_band)
        state="非当前值" if row.stale else "正在充电" if row.charging else "已同步" if valid else "等待设备"
        dot=accent if valid and not row.stale else MUTED
        c.create_rectangle(x+25,y+h-31,x+29,y+h-27,fill=dot,outline="")
        self.text(x+37,y+h-38,state,10,MUTED)
        tag=f"detail_{row.key}"
        self.text(x+w-95,y+h-38,"连接详情  ↗",10,accent,"bold",tags=tag)
        self.bind_action(tag,lambda r=row:self.details(r))

    def device_art(self,x,y,kind,accent=CYAN,scale=1):
        c=self.c
        previous=c.find_all()[-1] if c.find_all() else 0
        for radius in range(78,35,-7):
            c.create_oval(x-radius,y-radius,x+radius,y+radius,fill=mix(SURFACE,"#20373A",(78-radius)/100),outline="")
        c.create_oval(x-76,y-76,x+76,y+76,outline="#34434A",dash=(2,7))
        for dx in (-1,1):
            c.create_line(x+dx*65,y-58,x+dx*76,y-58,x+dx*76,y-46,fill=mix(SURFACE,accent,.6))
        c.create_oval(x-47,y+58,x+47,y+65,fill="#11171C",outline="")
        if kind=="mouse":
            rounded(c,x-34,y-58,68,116,31,fill="#10151C",outline="#56616C",width=1.5)
            rounded(c,x-29,y-54,58,107,27,fill="#252C35",outline="")
            c.create_line(x,y-55,x,y-9,fill="#65727B",width=1)
            c.create_arc(x-29,y-37,x+29,y+3,start=190,extent=160,style="arc",outline="#596672")
            rounded(c,x-5,y-36,10,23,4,fill=accent,outline="")
            c.create_line(x-31,y-7,x-31,y+15,fill="#74808A",width=3,capstyle="round")
            c.create_text(x,y+30,text="Z",fill="#B6C4C9",font=("Bahnschrift",-18,"bold"))
        elif kind=='headset':
            c.create_arc(x-44,y-58,x+44,y+44,start=0,extent=180,style="arc",outline="#0D1218",width=17)
            c.create_arc(x-44,y-58,x+44,y+44,start=0,extent=180,style="arc",outline="#69727D",width=2)
            c.create_arc(x-34,y-47,x+34,y+37,start=0,extent=180,style="arc",outline="#35414B",width=6)
            for dx in (-1,1):
                cx=x+dx*39
                rounded(c,cx-14,y-10,28,60,9,fill="#11171E",outline="#606E79")
                rounded(c,cx-10,y-5,20,49,7,fill="#303A45",outline="")
                c.create_line(cx+dx*6,y+5,cx+dx*6,y+30,fill=accent,width=2)
            c.create_line(x-39,y+37,x-28,y+57,x-7,y+57,fill="#899BA6",width=3,smooth=True)
            rounded(c,x-10,y+54,16,6,3,fill=accent,outline="")
        else:
            cut_panel(c,x-55,y-28,110,68,8,fill='#202A33',outline='#64717E')
            for r in range(3):
                for k in range(8):
                    c.create_rectangle(x-46+k*12,y-18+r*15,x-38+k*12,y-10+r*15,fill=accent if r==0 else '#4D5C66',outline='')
        if scale!=1:
            for item in c.find_all():
                if item>previous:c.scale(item,x,y,scale,scale)

    def toggle(self,x,y,value,callback):
        tag=f"toggle_{len(self.c.find_all())}"
        cut_panel(self.c,x,y,46,26,6,fill="#233F40" if value else "#292C35",outline=CYAN if value else "#474B58",tags=tag)
        self.c.create_rectangle(x+(26 if value else 5),y+5,x+(40 if value else 19),y+21,
                                fill=CYAN if value else MUTED,outline="",tags=tag)
        self.bind_action(tag,callback)

    def draw_settings(self,x,w,h):
        right=w-32
        c=self.c
        cut_panel(c,x,128,right-x,h-189,16,fill=SURFACE,outline=LINE)
        c.create_line(x+17,128,x+150,128,fill=CYAN,width=2)
        tx=x+28
        self.text(tx,149,"偏好设置",17,INK,"bold")
        self.text(tx,193,"自动刷新电量",13,INK,"bold")
        self.text(tx,219,"关闭窗口后仍按设定间隔查询。",11,MUTED)
        self.toggle(right-76,197,self.auto,self.toggle_auto)
        c.create_line(tx,257,right-28,257,fill=LINE)
        self.text(tx,277,"刷新间隔",13,INK,"bold")
        for i,(seconds,label) in enumerate([(30,"30 秒"),(60,"1 分钟"),(120,"2 分钟"),(300,"5 分钟")]):
            chosen=self.cfg["interval"]==seconds
            self.button(tx+i*112,312,102,36,label,lambda s=seconds:self.set_interval(s),
                        fill=LIME if chosen else "#272B34",color=BG if chosen else MUTED)
        c.create_line(tx,371,right-28,371,fill=LINE)
        self.text(tx,394,"卓威鼠标型号",13,INK,"bold")
        self.text(tx,422,self.cfg["zowie_model"].replace("auto","未选择（仅读电压）"),11,MUTED)
        self.button(right-164,394,136,37,"选择型号  ↗",self.choose_model,fill="#24383C",color=CYAN)
        c.create_line(tx,462,right-28,462,fill=LINE)
        self.text(tx,485,"开机启动托盘",13,INK,"bold")
        self.text(tx,513,"登录 Windows 后在后台运行。",11,MUTED)
        self.toggle(right-76,490,tc.is_autostart_enabled(),self.toggle_boot)
        c.create_line(tx,554,right-28,554,fill=LINE)
        self.text(tx,h-106,"关闭窗口收起到托盘；退出将停止后台运行。",10,MUTED)
        self.button(right-164,h-117,136,37,"退出 PULSE",self.exit_app,fill="#38262E",color=RED)


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
        self.refresh()

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
        if self.auto and not self.closed:
            self.timer=self.root.after(self.cfg["interval"]*1000,self.refresh)

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
                    if self.tray:
                        self.tray.update_readings(payload)
                    self.schedule()
                    if self.devices_dirty and self.discovery_timer is None:self.refresh_devices()
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
                     selectbackground="#27555B",padx=18,pady=15)
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
            return
        if self.details_window and self.details_window.winfo_exists():
            self.details_window.destroy()
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
