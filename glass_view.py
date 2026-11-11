"""PULSE's glass presentation layer. Hardware work stays in Dashboard."""
from __future__ import annotations

import ctypes
import time
from collections import OrderedDict
from tkinter import ttk, font as tkfont
from PIL import ImageTk

import device_service as service
import tray_common as tc
from glass_renderer import GlassRenderer

BG = '#E6EDF0'
SIDEBAR = '#EAF0F1'
SURFACE = '#F1F6F6'
INK = '#203B43'
MUTED = '#647A83'
LINE = '#CBDCDE'
CYAN = '#087F79'
LIME = '#C6EFE4'
GREEN = '#167B60'
MINT = CYAN
AMBER = '#986119'
RED = '#B64758'
BAND_COLORS = {'高档': GREEN, '中档': AMBER, '低档': RED}


def mix(a, b, amount):
    amount = max(0, min(1, amount))
    return '#' + ''.join(f'{round(int(a[i:i+2],16)*(1-amount)+int(b[i:i+2],16)*amount):02x}' for i in (1,3,5))


def window_theme(window):
    """Keep native resizing, snapping and title-bar controls."""
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        dwm = ctypes.WinDLL('dwmapi')
        dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p,ctypes.c_uint,ctypes.c_void_p,ctypes.c_uint]
        for attribute, value in ((20,0),(33,2),(35,0xF0EDE6),(36,0x433B20)):
            setting = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd,attribute,ctypes.byref(setting),4)
    except (OSError,AttributeError):
        pass


class GlassView:
    def _init_glass(self):
        self.renderer = GlassRenderer()
        self._photos = OrderedDict()
        self._photo_bytes = 0
        self._item_photos = {}
        self._shape_specs = {}
        self._regions = []
        self._hover = None
        self._pressed = None
        self._focus_key = None
        self._keyboard_focus = False
        self._animation = None
        self._ripples = []
        self._entry = None
        self._meters = []
        self._switches = []
        self._switch_values = {}
        self._specular = []
        self._pointer = (-1, -1)
        self._previous_levels = {}
        self._last_busy = False
        self._toast_until = 0
        self._toast_text = ''
        self._notice_seen = ''
        self._reduced_motion = False
        self._filter = 'all'
        self._fonts = {}
        self.c.configure(takefocus=True)
        self.c.bind('<Motion>', self._pointer_move)
        self.c.bind('<Leave>', self._pointer_leave)
        self.c.bind('<ButtonPress-1>', self._pointer_down)
        self.c.bind('<ButtonRelease-1>', self._pointer_up)
        self.c.bind('<Tab>', lambda e: self._focus_step(1))
        self.c.bind('<Shift-Tab>', lambda e: self._focus_step(-1))
        self.c.bind('<Return>', self._activate_focus)
        self.c.bind('<space>', self._activate_focus)
        self.root.bind('<Map>', self._mapped, add='+')
        self.root.bind('<Unmap>', self._unmapped, add='+')
        self.root.bind('<Destroy>', self._destroyed, add='+')

    def _widget_theme(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('Pulse.TCombobox',fieldbackground=SURFACE,background=LINE,
            foreground=INK,arrowcolor=CYAN,bordercolor=LINE,lightcolor=LINE,darkcolor=LINE,
            padding=10,selectbackground=LIME,selectforeground=INK)
        style.map('Pulse.TCombobox',fieldbackground=[('readonly',SURFACE)],foreground=[('readonly',INK)])
        style.configure('Pulse.TButton',background=CYAN,foreground='#FFFFFF',borderwidth=0,
            padding=(18,10),font=('Microsoft YaHei UI',10,'bold'))
        style.map('Pulse.TButton',background=[('active','#096B69')])
        style.configure('Pulse.Vertical.TScrollbar',background=LINE,troughcolor=SURFACE,
            arrowcolor=MUTED,bordercolor=SURFACE,lightcolor=LINE,darkcolor=LINE,arrowsize=12)
        self.root.option_add('*TCombobox*Listbox.background',SURFACE)
        self.root.option_add('*TCombobox*Listbox.foreground',INK)
        self.root.option_add('*TCombobox*Listbox.selectBackground',LIME)
        self.root.option_add('*TCombobox*Listbox.selectForeground',INK)

    def text(self,x,y,value,size=14,color=INK,weight='normal',**kwargs):
        family = kwargs.pop('family','Microsoft YaHei UI')
        anchor = kwargs.pop('anchor','nw')
        return self.c.create_text(x,y,text=value,fill=color,anchor=anchor,
            font=(family,-size,weight),**kwargs)

    def _fit_text(self, value, width, size=17):
        while size > 10:
            key = (size,'bold')
            if key not in self._fonts:
                self._fonts[key] = tkfont.Font(root=self.root,family='Microsoft YaHei UI',size=-size,weight='bold')
            if self._fonts[key].measure(value) <= width:
                break
            size -= 1
        return size

    def _photo(self, image):
        key = id(image)
        if key not in self._photos:
            self._photos[key] = (image, ImageTk.PhotoImage(image,master=self.root))
            self._photo_bytes += image.width*image.height*4
            while len(self._photos)>1 and (len(self._photos)>80 or self._photo_bytes>48*1024*1024):
                _,(evicted,_) = self._photos.popitem(last=False)
                self._photo_bytes -= evicted.width*evicted.height*4
        else:
            self._photos.move_to_end(key)
        return self._photos[key][1]

    def _image(self, image, x, y, tags=(), state='normal'):
        photo=self._photo(image)
        item=self.c.create_image(round(x),round(y),image=photo,anchor='nw',tags=tags,state=state)
        self._item_photos[item]=photo
        return item

    def _replace_image(self,item,image):
        photo=self._photo(image)
        if self._item_photos.get(item) is not photo:
            self.c.itemconfigure(item,image=photo)
            self._item_photos[item]=photo

    def _delete(self,tag):
        for item in self.c.find_withtag(tag):
            self._item_photos.pop(item,None)
            self._shape_specs.pop(item,None)
        self.c.delete(tag)

    def _shape(self,x,y,w,h,r=18,*,tags=(),state='normal',**kwargs):
        """Every curved primitive, including animated states, uses the AA renderer."""
        spec=dict(w=max(1,round(w)),h=max(1,round(h)),radius=r,**kwargs)
        item=self._image(self.renderer.shape(**spec),x,y,tags,state)
        self._shape_specs[item]=spec
        return item

    def _configure_shape(self,item,**changes):
        spec=self._shape_specs[item]
        for dimension in ('w','h'):
            if dimension in changes:
                changes[dimension]=max(1,round(changes[dimension]))
        if all(spec.get(key)==value for key,value in changes.items()):
            return
        spec.update(changes)
        self._replace_image(item,self.renderer.shape(**spec))

    def glass(self,x,y,w,h,r=24,tint='white',tags=()):
        x,y,w,h=round(x),round(y),round(w),round(h)
        return self._image(self.renderer.panel(self._background,x,y,w,h,radius=r,tint=tint),x,y,tags)

    def _register(self,key,box,command,outline=None,base=LINE,accent=CYAN,enabled=True):
        self._regions.append(dict(key=key,box=box,command=command,outline=outline,
            base=base,accent=accent,value=0.,enabled=enabled))

    def button(self,x,y,w,h,label,command,fill=SURFACE,color=INK,tag=None,enabled=True):
        key = tag or f'button_{x}_{y}'
        primary = fill == CYAN
        if primary:
            self._shape(x,y+2,w,h,h/2,fill='#BCD3D3')
            self._shape(x,y,w,h,h/2,fill=CYAN,outline='#21998F')
            self._shape(x+22,y+1,w-44,2,1,fill='#6AC4B4')
        else:
            self.glass(x,y,w,h,r=int(h/2),tint='mint' if fill==LIME else 'white')
        outline = self._shape(x,y,w,h,h/2,outline='#45A99F' if primary else '#FFFFFF',
            width=1,inset=0 if primary else 3)
        self.text(x+w/2,y+h/2,label,12,color if enabled else MUTED,'bold',anchor='center')
        self._register(key,(x,y,x+w,y+h),command,outline,'#45A99F' if primary else '#FFFFFF',enabled=enabled)
        return outline

    def bind_action(self,tag,command,shape=None,base=None,hover=None):
        # Compatibility for any extension adding tagged Canvas actions.
        box = self.c.bbox(tag)
        if box:
            self._register(tag,box,command,shape,base or LINE)

    def pill(self,x,y,label,fill=LIME,color=CYAN,width=120):
        self._shape(x,y,width,25,12,fill=fill)
        self._shape(x+10,y+10,5,5,fill=color,kind='ellipse')
        self.text(x+22,y+5,label,10,color)

    def _icon(self,x,y,kind,color=CYAN):
        return self._image(self.renderer.icon(kind,size=20,color=color),x,y)

    def resize(self,_event):
        if self.resize_timer:
            self.root.after_cancel(self.resize_timer)
        self.resize_timer=self.root.after(65,self.draw)

    def navigate(self,page):
        if self.page == page:
            return
        self.page=page
        self._focus_key=None
        self.draw()
        self._enter_page()

    def draw(self):
        if self.closed:
            return
        self.resize_timer=None
        self._stop_motion()
        old_images=self._item_photos
        self._item_photos={}
        self._shape_specs={}
        self._regions=[]
        self._ripples=[]
        self._meters=[]
        self._switches=[]
        self._specular=[]
        self._hover=None
        self._pressed=None
        self._entry=None
        self.c.delete('all')
        self.c.configure(cursor='')
        w,h=max(self.c.winfo_width(),1000),max(self.c.winfo_height(),720)
        self._background=self.renderer.background(w,h)
        self._image(self._background,0,0)
        del old_images
        self.glass(16,16,168,h-32,28)
        self._icon(40,46,'wave')
        self.text(68,36,'PULSE',25,INK,'bold',family='Bahnschrift')
        self.text(40,76,'无线设备电量',11,MUTED)
        self.c.create_line(40,113,160,113,fill='#CCDADC')
        self.text(40,139,'工作空间',10,MUTED)
        for page,label,y in [('devices','设备概览',175),('settings','偏好设置',234)]:
            chosen=self.page==page
            if chosen:
                self.glass(28,y,144,46,22,tint='mint')
            outline=self._shape(28,y,144,46,22,outline='#A7D0C5' if chosen else None,width=1,inset=3)
            self._icon(43,y+15,page,CYAN if chosen else MUTED)
            self.text(74,y+13,label,13,CYAN if chosen else MUTED,'bold' if chosen else 'normal')
            self._register('nav_'+page,(28,y,172,y+46),lambda p=page:self.navigate(p),outline,
                '#A7D0C5' if chosen else '#E7EFF0')
        self.glass(29,h-174,142,107,20)
        self._icon(43,h-155,'wave',GREEN)
        self.text(68,h-158,'随时连接',12,INK,'bold')
        self.text(43,h-128,'2.4 GHz',20,CYAN,family='Bahnschrift')
        self.text(43,h-96,'后台守候每一格电量',9,MUTED)
        self.text(40,h-42,'PULSE  /  DESKTOP',9,MUTED,family='Consolas')
        x=216
        self.text(x,30,'CONNECTED, EFFORTLESSLY.',10,CYAN,family='Consolas')
        self.text(x,56,'设备概览' if self.page=='devices' else '偏好设置',31,INK,'bold')
        self.text(x,103,'桌面上的每一份能量，一目了然。' if self.page=='devices' else '让 PULSE 按你的节奏运行。',12,MUTED)
        self.button(w-174,52,142,44,'正在同步' if self.busy else '刷新电量',self.refresh,
            fill=CYAN,color='#FFFFFF',tag='refresh',enabled=not self.busy)
        self._spinner=self._image(self.renderer.icon('refresh',size=18,color='#E7FFF8'),w-158,65)
        if self.page=='devices':
            self.draw_devices(x,w,h)
        else:
            self.draw_settings(x,w,h)
        # Animate the content as a group; the sidebar remains an anchored surface.
        for item in self.c.find_all():
            box=self.c.bbox(item)
            if box and box[0]>=210 and box[1]>=130 and box[3]<h-42:
                self.c.addtag_withtag('content',item)
        last=max((r.checked_at for r in self.rows),default=0)
        stamp=time.strftime('%H:%M:%S',time.localtime(last)) if last else '等待首次同步'
        self._shape(x,h-30,5,5,fill=CYAN if self.auto else MUTED,kind='ellipse')
        self.text(x+14,h-36,'正在同步设备…' if self.busy else f'最近查询  {stamp}',10,MUTED)
        self.text(w-32,h-36,'Ctrl + R  刷新    ·    Ctrl + ,  设置',10,MUTED,anchor='ne')
        if self._last_busy and not self.busy:
            self._toast_text=self.notice or '设备状态已更新'
            self._toast_until=time.monotonic()+3.0
        self._last_busy=self.busy
        if self.notice and self.notice != self._notice_seen:
            self._toast_text=self.notice
            self._toast_until=time.monotonic()+3.5
        self._notice_seen=self.notice
        self._draw_toast(w,h)
        self._draw_focus()
        self._ensure_motion()

    def _filtered_rows(self):
        return [r for r in self.rows if self._filter=='all' or r.kind==self._filter]

    def set_filter(self,kind):
        if kind==self._filter:
            return
        self._filter=kind
        self.device_page=0
        self.draw()
        self._enter_page()

    def draw_devices(self,x,w,h):
        c=self.c
        self.glass(x,141,w-x-32,69,22)
        good=sum(r.available and not r.stale for r in self.rows)
        values=[(f'{len(self.rows):02d}','已发现设备'),(f'{good:02d}','电量已同步'),
                (f'{service.receiver_count(self.rows):02d}','已连接接收器')]
        segment=(w-x-32)/3
        for i,(value,label) in enumerate(values):
            sx=x+i*segment+23
            self.text(sx,157,value,30,CYAN if i==1 else INK,'bold',family='Bahnschrift')
            self.text(sx+51,171,label,11,MUTED)
            if i:
                c.create_line(sx-24,160,sx-24,191,fill='#CDDDDF')
        rows=self._filtered_rows()
        pages=max(1,(len(rows)+2)//3)
        self.device_page=min(self.device_page,pages-1)
        visible=rows[self.device_page*3:self.device_page*3+3]
        gap=18
        columns=max(1,len(visible))
        card_w=(w-32-x-gap*(columns-1))/columns
        gallery_x=x
        if columns==1 and visible:
            card_w=min(520,card_w)
            gallery_x=x+(w-32-x-card_w)/2
        card_h=h-330
        for i,row in enumerate(visible):
            self.draw_card(gallery_x+i*(card_w+gap),230,card_w,card_h,row,i)
        if not visible:
            self.glass(x,230,w-x-32,card_h,28)
            self.device_art(x+(w-x-32)/2,336,'mouse',scale=.65)
            self.text(x+(w-x-32)/2,425,'尚未发现电量设备' if not self.rows else '此分类暂无设备',23,INK,'bold',anchor='n')
            self.text(x+(w-x-32)/2,469,'连接无线接收器并开启设备，PULSE 会自动发现。',12,MUTED,anchor='n')
        # Categories stay below the device gallery so device names are immediately visible.
        fy=h-77
        for i,(kind,label) in enumerate([('all','全部'),('mouse','鼠标'),('keyboard','键盘'),('headset','耳机')]):
            self.button(x+i*73,fy,65,30,label,lambda k=kind:self.set_filter(k),
                fill=LIME if self._filter==kind else SURFACE,color=CYAN if self._filter==kind else MUTED,tag='filter_'+kind)
        if pages>1:
            self.button(w-159,fy,33,30,'‹',lambda:self.change_device_page(-1),tag='previous')
            self.text(w-96,fy+15,f'{self.device_page+1} / {pages}',11,MUTED,anchor='center')
            self.button(w-65,fy,33,30,'›',lambda:self.change_device_page(1),tag='next')
        else:
            self.text(w-32,fy+15,f'自动同步 · {self.cfg["interval"]} 秒' if self.auto else '自动同步已暂停',10,MUTED,anchor='e')

    def change_device_page(self,delta):
        pages=max(1,(len(self._filtered_rows())+2)//3)
        self.device_page=(self.device_page+delta)%pages
        self.draw()
        self._enter_page()

    def device_art(self,x,y,kind,accent=CYAN,scale=1):
        size=round(184*scale)
        self._image(self.renderer.device(kind,size=size,accent=accent),x-size/2,y-size/2)

    def draw_card(self,x,y,w,h,row,index):
        c=self.c
        accent=MUTED if row.stale else RED if row.level is not None and row.level<30 else BAND_COLORS.get(row.display_band,CYAN)
        self.glass(x,y,w,h,26)
        # The material already has an antialiased rim. A second Canvas outline
        # would add jagged, misaligned pixels around that finished contour.
        self._register('card_'+row.key,(x,y,x+w,y+h),None,accent=accent)
        shine=self._shape(x+30,y+3,80,2,1,fill='#FFFFFF',state='hidden')
        refract=self._shape(x+30,y+5,58,1,.5,fill='#ADD9D4',state='hidden')
        self._specular.append(dict(box=(x,y,x+w,y+h),shine=shine,refract=refract,pos=x+w/2))
        size=self._fit_text(row.name,w-42,17)
        self.text(x+21,y+19,row.name,size,INK,'bold',width=w-42)
        label={'mouse':'无线鼠标','headset':'无线耳机','keyboard':'无线键盘','gamepad':'游戏手柄'}.get(row.kind,row.category or '无线设备')
        self.text(x+21,y+49,label,10,MUTED)
        self.text(x+w-21,y+49,'2.4G' if row.receiver else 'USB' if 'USB' in row.connection else row.connection,9,MUTED,anchor='ne',family='Consolas')
        art_size=min(w-52,330,max(150,h-260))
        self.device_art(x+w/2,y+80+(h-270)/2,row.kind,accent,scale=art_size/184)
        number=row.display_band or (str(row.level) if row.level is not None else f'{row.voltage:.2f}' if row.voltage is not None else '—')
        ny=y+h-189
        number_size=42 if row.display_band else 58
        self.text(x+22,ny,number,number_size,MUTED if row.stale else accent if row.display_band or (row.level is not None and row.level<30) else INK,
            'bold',family='Microsoft YaHei UI' if row.display_band else 'Bahnschrift')
        if row.level is not None and not row.display_band:
            self.text(x+24+len(number)*31,ny+32,'%',18,MUTED,family='Bahnschrift')
        elif row.voltage is not None and not row.display_band:
            self.text(x+158,ny+32,'V',18,MUTED,family='Bahnschrift')
        elif row.display_band and row.voltage is not None:
            self.text(x+w-21,ny+35,f'{row.voltage:.2f} V',13,MUTED,anchor='ne',family='Bahnschrift')
        note=(f'上次 {time.strftime("%H:%M",time.localtime(row.sampled_at))} · 非当前值' if row.stale and row.sampled_at else
            '上次电量 · 非当前值' if row.stale else
            '缓存电压 · 采样时间未知' if row.protocol=='zowie' and row.voltage is not None else
            '正在充电' if row.charging else '设备原生档位' if row.display_band else
            '剩余电量' if row.level is not None else '设备电压' if row.voltage is not None else '尚未取得电量')
        self.text(x+23,y+h-118,note,10,MUTED)
        bx,by,bw=x+22,y+h-87,w-44
        if row.display_band:
            for i,band in enumerate(('低档','中档','高档')):
                col=accent if band==row.display_band else '#D6E3E5'
                self._shape(bx+i*bw/3,by,bw/3-5,5,2,fill=col)
        else:
            self._shape(bx,by,bw,5,2,fill='#D7E4E5')
            if row.level is not None:
                fill=self._shape(bx,by,max(1,bw*row.level/100),5,2,fill=accent)
                previous=self._previous_levels.get(row.key,row.level)
                if previous!=row.level and not row.stale and not self._reduced_motion:
                    self._meters.append((fill,bx,by,bw,previous,row.level,time.monotonic()))
                self._previous_levels[row.key]=row.level
        c.create_line(x+21,y+h-64,x+w-21,y+h-64,fill='#D5E2E3')
        state='非当前值' if row.stale else '正在充电' if row.charging else '已同步' if row.available else '等待设备'
        dot=accent if row.available else AMBER
        self._shape(x+23,y+h-39,5,5,fill=dot,kind='ellipse')
        self.text(x+35,y+h-45,state,10,MUTED)
        self.button(x+w-92,y+h-51,74,29,'详情  ↗',lambda r=row:self.details(r),color=CYAN,tag='detail_'+row.key)

    draw_compact_card=draw_card

    def toggle(self,x,y,value,callback,tag=None):
        key=tag or f'toggle_{x}_{y}'
        track=self._shape(x,y,46,26,13,fill=CYAN if value else '#BECDD2',outline='#FFFFFF')
        knob=self._shape(x+(23 if value else 3),y+3,20,20,fill='#FFFFFF',outline='#D2E6E1',kind='ellipse',width=.75)
        old=self._switch_values.get(key,value)
        if old!=value and not self._reduced_motion:
            self._switches.append((track,knob,x,y,float(old),float(value),time.monotonic()))
        self._switch_values[key]=value
        outline=self._shape(x-2,y-2,50,30,15,width=1.3)
        self._register(key,(x-3,y-5,x+49,y+31),callback,outline,'#D2E6E1')

    def draw_settings(self,x,w,h):
        right=w-32
        self.glass(x,141,right-x,h-201,28)
        tx=x+27
        self.text(tx,165,'连接与同步',16,INK,'bold')
        self.text(tx,206,'自动刷新电量',13,INK,'bold')
        self.text(tx,231,'收起到托盘后，仍会按设定间隔同步。',11,MUTED)
        self.toggle(right-75,213,self.auto,self.toggle_auto,'auto')
        self.c.create_line(tx,270,right-27,270,fill=LINE)
        self.text(tx,289,'刷新间隔',13,INK,'bold')
        bw=min(96,(right-tx-27-5*8)/6)
        for i,(seconds,label) in enumerate(tc.INTERVAL_CHOICES):
            chosen=self.cfg['interval']==seconds
            self.button(tx+i*(bw+8),322,bw,35,label,lambda s=seconds:self.set_interval(s),
                fill=LIME if chosen else SURFACE,color=CYAN if chosen else MUTED,tag=f'interval_{seconds}')
        self.c.create_line(tx,378,right-27,378,fill=LINE)
        self.text(tx,397,'卓威鼠标型号',13,INK,'bold')
        self.text(tx,423,self.cfg['zowie_model'].replace('auto','未选择 · 仅读电压'),11,MUTED)
        self.button(right-165,400,138,37,'选择型号  ↗',self.choose_model,color=CYAN,tag='model')
        self.c.create_line(tx,463,right-27,463,fill=LINE)
        self.text(tx,481,'开机启动托盘',13,INK,'bold')
        self.text(tx,507,'登录 Windows 后在后台运行。',11,MUTED)
        self.toggle(right-75,486,tc.is_autostart_enabled(),self.toggle_boot,'boot')
        self.c.create_line(tx,544,right-27,544,fill=LINE)
        self.text(tx,562,'减少动态效果',13,INK,'bold')
        self.text(tx,588,'本次运行使用更安静的过渡效果。',11,MUTED)
        self.toggle(right-75,567,self._reduced_motion,self.toggle_motion,'motion')
        self.text(tx,h-99,'关闭窗口收起到托盘，电量同步继续运行。',10,MUTED)
        self.button(right-155,h-109,128,34,'退出 PULSE',self.exit_app,color=RED,tag='exit')

    def toggle_motion(self):
        self._reduced_motion=not self._reduced_motion
        self.draw()
        self.feedback('已减少动态效果' if self._reduced_motion else '动态效果已开启')

    def feedback(self,message):
        self._toast_text=message
        self._toast_until=time.monotonic()+2.8
        self._draw_toast(max(1000,self.c.winfo_width()),max(720,self.c.winfo_height()))
        self._ensure_motion()

    def _draw_toast(self,w,h):
        self._delete('toast')
        if time.monotonic()>=self._toast_until:
            return
        width=min(w-260,max(220,len(self._toast_text)*13+50))
        x=216+(w-248-width)/2
        self._shape(x,h-124,width,40,20,fill='#264E52',outline='#81BEB4',tags='toast')
        self.text(x+width/2,h-104,'✓  '+self._toast_text,12,'#FFFFFF',anchor='center',tags='toast')

    def _hit(self,x,y):
        for region in reversed(self._regions):
            a,b,c,d=region['box']
            if region['enabled'] and a<=x<=c and b<=y<=d:
                return region
        return None

    def _pointer_move(self,event):
        self._pointer=(event.x,event.y)
        region=self._hit(event.x,event.y)
        key=region['key'] if region else None
        if key!=self._hover:
            self._hover=key
            self.c.configure(cursor='hand2' if region and region['command'] else '')
            self._ensure_motion()
        elif any(a<=event.x<=c and b<=event.y<=d for a,b,c,d in (s['box'] for s in self._specular)):
            self._ensure_motion()

    def _pointer_leave(self,_event):
        self._pointer=(-1,-1)
        self._hover=None
        self._pressed=None
        self.c.configure(cursor='')
        self._ensure_motion()

    def _pointer_down(self,event):
        self.c.focus_set()
        self._keyboard_focus=False
        self._draw_focus()
        region=self._hit(event.x,event.y)
        self._pressed=region['key'] if region and region['command'] else None
        if self._pressed:
            self._focus_key=self._pressed
            if not self._reduced_motion:
                x1,y1,x2,y2=region['box']
                item=self._shape(event.x-2,event.y-2,4,4,kind='ellipse',outline='#299F92',width=1.5)
                radius=max(3,min(23,event.x-x1,x2-event.x,event.y-y1,y2-event.y))
                self._ripples.append((item,event.x,event.y,radius,time.monotonic()))
            self._ensure_motion()

    def _pointer_up(self,event):
        region=self._hit(event.x,event.y)
        key=self._pressed
        self._pressed=None
        if key and region and region['key']==key and region['command']:
            region['command']()
        self._ensure_motion()

    def _focus_step(self,delta):
        regions=[r for r in self._regions if r['enabled'] and r['command']]
        if not regions:
            return 'break'
        keys=[r['key'] for r in regions]
        index=keys.index(self._focus_key) if self._focus_key in keys else (-1 if delta>0 else 0)
        self._focus_key=keys[(index+delta)%len(keys)]
        self._keyboard_focus=True
        self._draw_focus()
        return 'break'

    def _activate_focus(self,_event):
        for region in self._regions:
            if region['key']==self._focus_key and region['enabled'] and region['command']:
                region['command']()
                break
        return 'break'

    def _draw_focus(self):
        self._delete('focus')
        if not self._keyboard_focus:
            return
        for region in self._regions:
            if region['key']==self._focus_key:
                x1,y1,x2,y2=region['box']
                self._shape(x1-3,y1-3,x2-x1+6,y2-y1+6,16,outline=CYAN,width=1.5,tags='focus')

    def _enter_page(self):
        if not self._reduced_motion and self.visible and self.root.state() in ('normal','zoomed'):
            self.c.move('content',0,10)
            self._entry=(time.monotonic(),10.)
            self._ensure_motion()

    def _ensure_motion(self):
        if self._animation is None and not self.closed and self.visible and self.root.state() in ('normal','zoomed'):
            self._animation=self.root.after(16,self._tick)

    def _stop_motion(self):
        if self._animation is not None:
            self.root.after_cancel(self._animation)
            self._animation=None

    def _tick(self):
        self._animation=None
        if self.closed or not self.visible or self.root.state() not in ('normal','zoomed'):
            return
        now=time.monotonic()
        active=False
        px,py=self._pointer
        for specular in self._specular:
            x1,y1,x2,y2=specular['box']
            inside=x1<=px<=x2 and y1<=py<=y2 and not self._reduced_motion
            for item in (specular['shine'],specular['refract']):
                self.c.itemconfigure(item,state='normal' if inside else 'hidden')
            if inside:
                target=max(x1+64,min(x2-64,px))
                delta=target-specular['pos']
                specular['pos']+=delta*.22
                cx=specular['pos']
                self.c.coords(specular['shine'],round(cx-40),round(y1+3))
                self.c.coords(specular['refract'],round(cx-29),round(y1+5))
                active |= abs(delta)>.5
        for region in self._regions:
            target=1. if region['key'] in (self._hover,self._pressed) else 0.
            delta=target-region['value']
            region['value']=target if self._reduced_motion or abs(delta)<.015 else region['value']+delta*.24
            active |= abs(delta)>.015
            if region['outline']:
                # Quantized material states are reusable across hover cycles.
                level=round(region['value']*12)/12
                self._configure_shape(region['outline'],outline=mix(region['base'],region['accent'],level*.72),
                    width=round((1+level*(1.2 if region['key']==self._pressed else .6))*4)/4)
        for ripple in self._ripples[:]:
            item,x,y,radius,start=ripple
            p=min(1,(now-start)/.42)
            r=2+(radius-2)*(1-(1-p)**3)
            self.c.coords(item,round(x-r),round(y-r))
            self._configure_shape(item,w=2*r,h=2*r,outline=mix('#299F92','#E9F4F1',p))
            if p>=1:
                self._delete(item)
                self._ripples.remove(ripple)
            else:
                active=True
        if self._entry:
            start,last=self._entry
            p=min(1,(now-start)/.28)
            offset=10*(1-p)**3
            self.c.move('content',0,offset-last)
            self._entry=(start,offset) if p<1 else None
            active |= p<1
        for meter in self._meters[:]:
            item,x,y,width,old,new,start=meter
            p=min(1,(now-start)/.6)
            value=old+(new-old)*(1-(1-p)**3)
            self._configure_shape(item,w=max(1,width*value/100))
            if p>=1:
                self._meters.remove(meter)
            else:
                active=True
        for switch in self._switches[:]:
            track,knob,x,y,old,new,start=switch
            p=min(1,(now-start)/.22)
            value=old+(new-old)*(1-(1-p)**3)
            self.c.coords(knob,round(x+3+20*value),round(y+3))
            self._configure_shape(track,fill=mix('#BECDD2',CYAN,round(value*16)/16))
            if p>=1:
                self._switches.remove(switch)
            else:
                active=True
        if self.busy and not self._reduced_motion:
            angle=round(-(now*240)/10)*10%360
            self._replace_image(self._spinner,self.renderer.shape(18,18,kind='arc',
                start=angle,extent=280,outline='#E7FFF8',width=1.5,inset=1))
            active=True
        if self._toast_until:
            if now>=self._toast_until:
                self._delete('toast')
                self._toast_until=0
            else:
                active=True
        if active:
            self._animation=self.root.after(16 if self._entry or self._ripples else 33,self._tick)

    def _mapped(self,event):
        if event.widget is self.root and not self.closed:
            self.visible=True
            self.draw()
            self._enter_page()

    def _unmapped(self,event):
        if event.widget is self.root:
            self.visible=False
            self._stop_motion()

    def _destroyed(self,event):
        if event.widget is self.root:
            self._stop_motion()
