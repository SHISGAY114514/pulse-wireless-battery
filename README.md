# PULSE · 无线设备电量

Windows 桌面电量面板，支持 **漫步者 G5 MAX 电量百分比**和 **卓威 EC2-DW 电压及高中低档**。圆角界面、后台常驻、系统托盘与可选的开机自启动。

基于 [yszyszysz233/2.4G-battery-viewer](https://github.com/yszyszysz233/2.4G-battery-viewer) 修改，保留原作者提交和源码。原项目介绍见 [上游 README](docs/UPSTREAM_README.md)。

> 授权状态：上游未提供许可证，本仓库暂未对整个派生项目添加新的许可证。当前为公开源码的 Fork；使用、修改、分发的授权范围请先阅读 [NOTICE](NOTICE.md)。本次不提供修改版 EXE 的公开下载。

## 当前支持

| 设备 | 接口 | 显示内容 | 验证范围 |
|---|---|---|---|
| 漫步者 / HECATE G5 MAX | `35BB:A00B` 2.4G 接收器 | 设备返回的百分比、充电状态 | 已实机验证纯 2.4G 读取 |
| 漫步者 / HECATE G5 MAX | `35BB:A211` USB 有线 | 设备返回的百分比、充电状态 | 已实机验证，界面区分有线连接 |
| 卓威 ZOWIE EC2-DW | `04A5:800A` 2.4G 接收器 | 接收器缓存电压及高/中/低档 | 接收器固件 `8511`、实机鼠标固件 `2524` |

这不是通用外设电量工具。卓威未取得设备原生百分比，不能把电压或高档当成准确容量或充满信号。其他型号、其他固件仍需验证。原项目的 ATK / DARKRIM 调试逻辑保留在源码中，不属于本面板已验证的默认设备。

## 运行

Windows 10/11、64 位 Python 3.10+，包含 tkinter；开发验证使用 Python 3.12。运行时只使用 Python 标准库与 Windows 自带 API，无第三方运行依赖。

```powershell
git clone https://github.com/SHISGAY114514/pulse-wireless-battery.git
cd pulse-wireless-battery
python pulse_app.py
```

不需要安装厂商应用或运行固件更新器。接收器须连接电脑，设备须开机；G5 MAX 无线读取需切到 2.4G 模式。

## 界面和后台

- 关闭面板仅隐藏窗口，后台继续按设定间隔查询。
- 左键托盘图标恢复同一面板；重复启动也会恢复已有实例。
- 右键托盘可打开面板、刷新、切换开机自启动或彻底退出。
- 在偏好设置中调整自动刷新及间隔，自启动默认关闭。
- 设置保存在 `%LOCALAPPDATA%/PULSE/settings.json`，自启动使用 Windows 启动文件夹快捷方式。
- 鼠标托盘为绿 H / 黄 M / 红 L；耳机为设备返回的电量数字。悬停可见中文说明。
- `Ctrl+R` 刷新，`Ctrl+,` 打开偏好设置。

## 卓威档位与更新时间

下表是应用根据 EC-DW 固件阈值及本机观察采用的保守显示规则，不是设备直接返回的档位字节，也不是完整放电校准结果。

| 首次读取的电压 | 显示档位 |
|---|---|
| ≤3.75 V | 低档 |
| 3.76–3.89 V | 中档 |
| ≥3.90 V | 高档 |

边界加入缓冲，避免标签来回跳动：低→中需 ≥3.77 V，中→低需 ≤3.74 V；中→高需 ≥3.90 V，高→中需 ≤3.88 V。断开或读取失败后清除缓冲并重判。高档不代表 100%。

**软件每 60 秒查询一次，不代表鼠标每 60 秒上报一次。** 电脑读取的是接收器缓存，界面更新时间是查询时间，实际采样时间未知；查询不能强制鼠标重新采样。固件实际无线电压上报周期尚未确定。鼠标版本为零、前后不一致或读取失败时隐藏电压和档位。

更多命令格式、缓存行为和固件分析边界见 [协议说明](docs/PROTOCOLS.md)。

## 命令行

```powershell
python pulse_app.py --once
python pulse_app.py --json
python pulse_app.py --background
python pulse_app.py --show
python pulse_app.py --hide
python pulse_app.py --refresh
python pulse_app.py --status
python pulse_app.py --autostart on
python pulse_app.py --autostart off
python pulse_app.py --quit
```

`my_devices.py` 是兼容入口，`app.py` 保留上游完整 HID 调试界面。常规使用请使用 PULSE 入口。

## 开发验证与打包

不依赖已连接设备的协议、异常、档位及状态测试：

```powershell
python -m unittest -v test_devices
```

有接收器且耳机、鼠标可读时，退出已有 PULSE 后，可做常驻生命周期验收：

```powershell
python test_resident_lifecycle.py
```

它验证后台启动、连续托盘恢复/隐藏、重复启动、隐藏后的实际读取和完全退出。自启动快捷方式测试使用临时目录，不改动真实自启动项。

开发打包使用 PyInstaller，运行程序本身不需要此依赖：

```powershell
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm PULSE.spec
# 构建结果：dist/PULSE.exe
python test_resident_lifecycle.py dist/PULSE.exe
```

## 源码结构

| 文件 | 用途 |
|---|---|
| `pulse_app.py` | GUI / CLI 入口 |
| `modern_ui.py` | 圆角桌面面板 |
| `resident_app.py` | 单实例、后台常驻及原生托盘线程 |
| `device_service.py` | 面板、托盘与命令行共享的设备读取 |
| `edifier_proto.py` | G5 MAX 状态查询 |
| `zowie_proto.py` | EC2-DW 只读电压查询及显示分档 |
| `hid_win.py` | Windows HID 访问 |
| `tray_common.py` | 配置、自启动及 IPC |
| `test_devices.py` | 无硬件协议与状态测试 |

不发布官方固件、更新器、原始抓包、设备清单、个人设置或开发环境依赖。

## 致谢

- [yszyszysz233/2.4G-battery-viewer](https://github.com/yszyszysz233/2.4G-battery-viewer)：原项目与 Windows HID 基础。
- [yleshchuk/zowie-battery](https://github.com/yleshchuk/zowie-battery)：EC-CW 协议研究参考。
- [0Mercy/U2-dw-battery-monitor](https://github.com/0Mercy/U2-dw-battery-monitor)：DW 只读电压协议及缓存研究参考。

本项目为非官方工具，设备名称及商标归各自权利人所有。来源与授权说明见 [NOTICE](NOTICE.md)。
