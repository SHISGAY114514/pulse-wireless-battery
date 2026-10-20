# PULSE · 无线设备电量

[![License: MIT](https://img.shields.io/badge/License-MIT-2ea44f.svg)](LICENSE)
![Platform: Windows](https://img.shields.io/badge/Platform-Windows-0078D4.svg)
![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)
[![Upstream](https://img.shields.io/badge/Upstream-2.4G--battery--viewer-5865F2.svg)](https://github.com/yszyszysz233/2.4G-battery-viewer)

一个轻量的 Windows 外设电量面板：**动态发现鼠标、键盘和耳机**，按设备协议显示百分比、电压或原生档位。采用深色电竞界面，支持热插拔、动态托盘和可选的开机自启动。[协议支持范围](docs/DEVICE_SUPPORT.md)与[键盘适配说明](docs/KEYBOARD_SUPPORT.md)明确区分实测、实验性适配与暂不支持的设备。

**原项目：[yszyszysz233/2.4G-battery-viewer](https://github.com/yszyszysz233/2.4G-battery-viewer)**。PULSE 是在该项目基础上开发的适配版本，保留原作者源码、署名和 Git 历史。

[快速开始](#快速开始) · [支持设备](#当前支持) · [项目来源](#项目来源与改动) · [协议说明](docs/PROTOCOLS.md) · [参与贡献](CONTRIBUTING.md) · [更新记录](CHANGELOG.md)

## 项目来源与改动

| 来源 | 对本项目的贡献 |
|---|---|
| [yszyszysz233/2.4G-battery-viewer](https://github.com/yszyszysz233/2.4G-battery-viewer) · 原项目 | Windows HID 访问、原版电量读取与设备调试工具、托盘及系统集成基础、原有 ATK / DARKRIM 协议实现 |
| PULSE · 本仓库 | 新版面板、同进程托盘常驻、单实例与自启动、动态设备发现、G5 MAX / 卓威 / 雷蛇适配、标准 HID 电量读取及相关测试 |
| [0Mercy/U2-dw-battery-monitor](https://github.com/0Mercy/U2-dw-battery-monitor) · 协议研究参考 | DW 接收器只读查询、电压缓存行为与分档研究方法 |
| [yleshchuk/zowie-battery](https://github.com/yleshchuk/zowie-battery) · 协议研究参考 | EC-CW 电量查询的调查线索与型号差异参考 |
| [openrazer/openrazer](https://github.com/openrazer/openrazer) · 协议研究参考 | 雷蛇型号 USB ID、报文结构及电量 / 充电只读查询 |

后三项为研究参考，协议模块独立实现；未复制其运行代码或校准数据。PULSE 分别分析 EC-DW、FK、ZA13、S2、U2DW、早期 U2 固件，区分实机验证和离线验证，详见[卓威支持矩阵与证据](docs/ZOWIE_SUPPORT.md)。完整来源说明、基线提交和授权信息见 [参考项目与来源](docs/REFERENCES.md)及 [NOTICE](NOTICE.md)。[原项目 README](docs/UPSTREAM_README.md)作为原文保留。

## 功能概览

- 深色切角卡片、细线网格、青色与荧光黄点缀；设备插画由原生 Canvas 绘制，无额外图片依赖。
- 一处查看鼠标、键盘和耳机状态，手动刷新或按设定间隔自动查询。
- 按 Windows 物理设备合并 HID 集合，卡片、计数和托盘随设备增删；三张卡片同屏，更多设备分页。
- 关闭面板后继续在后台运行，点击托盘可恢复已有窗口。
- 支持开机自启动、单实例启动和彻底退出。
- 无法读取时显示不可用；迈从休眠的旧值限时保留并标注“非当前值”，拔出后清除。
- 运行时仅使用 Python 标准库和 Windows API。

## 当前支持

| 设备 | 接口 | 显示内容 | 验证范围 |
|---|---|---|---|
| 迈从 A7 Ultra | `5253:1020` 接收器 / `5253:0011` 有线 | 直接百分比、充电状态，休眠缓存明确标注 | 已实机验证有线与纯 2.4G |
| RK R98 | `25A7:FA70` 接收器 / `258A:00C1` 本体 | 已发现，电量未取得 | 官方候选固件已解码，尚无实机电压标定，见[研究报告](docs/RK_R98_RESEARCH.md) |
| ROG / Razer / MCHOSE / DAREU 键盘 | 只尝试已列身份和协议 | 按实际回复显示；无有效回复则不可用 | 公开协议适配，尚未逐型号实测，见[键盘支持矩阵](docs/KEYBOARD_SUPPORT.md) |
| Logitech HID++ | 收录的接收器 / 匹配厂商集合 | 直接百分比、原生粗档或电压，多槽设备分开显示 | 协议适配，无本机设备验证 |
| 雷蛇 DeathAdder V3 Pro | `1532:00B7` 2.4G 接收器 | 电量百分比、充电状态 | 已实机读取；同协议其他已收录 PID 尚未逐型号实测 |
| 标准 HID 电池设备 | 描述符声明的 Feature / Input 电池字段 | 按逻辑范围读取电量百分比 | 通用读取路径，设备仍须实际提供对应字段 |
| 漫步者 / HECATE G5 MAX | `35BB:A00B` 2.4G 接收器 | 设备返回的百分比、充电状态 | 已实机验证纯 2.4G 读取 |
| 漫步者 / HECATE G5 MAX | `35BB:A211` USB 有线 | 设备返回的百分比、充电状态 | 已实机验证，界面区分有线连接 |
| 卓威 ZOWIE EC2-DW | `04A5:800A` 2.4G 接收器 | 接收器缓存电压及高/中/低档 | 接收器固件 `8511`、实机鼠标固件 `2524` |
| EC1/2/3-DW、FK2-DW、ZA13-DW、S2-DW、U2-DW | `04A5:800A`，接收器 `8507 / 8511` | 分系列电压与档位 | 鼠标固件 `2524 / 2526` 已离线验证；其余型号未实机验证 |
| 上述七款 Glossy 版 | 同上 | 分系列电压与档位 | 仅 `2526`，官网下载共用镜像，离线验证 |
| 早期 U2 + ER2-80 4K 接收器 | 同上 | U2 电压与档位 | 仅 `2511` 鼠标固件，离线验证 |
| FK1-DW、ZA12-DW | `04A5:800A` | 仅读取电压，不显示档位 | 缺少对应型号固件 |
| EC1/2/3-CW | `04A5:8006` | 实验性电压查询，不显示档位 | Windows 未实机验证，缺少固件映射 |

**尚未覆盖所有私有电量协议，也未覆盖所有近年卓威无线型号。** U2 原装 `8008` 接收器暂不读取；未知固件不显示档位。接收器 `8507` 已离线核对，只有 `8511` 在本机实测。卓威未取得已验证的原生百分比，不能把高档当成准确容量或充满信号。原项目的部分厂商适配器作为实验性路径接入，具体见[协议支持说明](docs/DEVICE_SUPPORT.md)。

**首次使用请到“偏好设置 → 卓威鼠标型号”选择实际型号。** DW 共用接收器 USB ID，无法据此自动识别；新安装默认仅读电压。完整型号、版本、阈值和缺口见[支持矩阵](docs/ZOWIE_SUPPORT.md)。

## 快速开始

Windows 10/11、64 位 Python 3.10+，包含 tkinter；开发验证使用 Python 3.12。运行时只使用 Python 标准库与 Windows 自带 API，无第三方运行依赖。

```powershell
git clone https://github.com/SHISGAY114514/pulse-wireless-battery.git
cd pulse-wireless-battery
python pulse_app.py
```

不需要安装厂商应用或运行固件更新器。接收器须连接电脑，设备须开机；G5 MAX 无线读取需切到 2.4G 模式。

当前仓库提供源码和打包配置，尚未发布 GitHub Release 安装包。需要独立 EXE 时，可按下方步骤自行打包。

## 界面和后台

- 关闭面板仅隐藏窗口，后台继续按设定间隔查询。
- 左键托盘图标恢复同一面板；重复启动也会恢复已有实例。
- 右键托盘可打开面板、刷新、切换开机自启动或彻底退出。
- 在偏好设置中调整自动刷新及间隔，自启动默认关闭。
- 设置保存在 `%LOCALAPPDATA%/PULSE/settings.json`，自启动使用 Windows 启动文件夹快捷方式。
- 卓威档位托盘为绿 H / 黄 M / 红 L；支持百分比的鼠标、耳机及其他外设显示电量数字。悬停可见中文说明。
- `Ctrl+R` 刷新，`Ctrl+,` 打开偏好设置。

## 卓威档位与更新时间

下表是 EC-DW 的保守显示规则，依据固件阈值及本机观察；不是设备直接返回的档位字节，也不是完整放电校准结果。

| 首次读取的电压 | 显示档位 |
|---|---|
| ≤3.75 V | 低档 |
| 3.76–3.89 V | 中档 |
| ≥3.90 V | 高档 |

边界加入缓冲，避免标签来回跳动：低→中需 ≥3.77 V，中→低需 ≤3.74 V；中→高需 ≥3.90 V，高→中需 ≤3.88 V。断开或读取失败后清除缓冲并重判。高档不代表 100%。

FK、ZA13、S2、U2DW 和早期 U2 固件采用不同阈值，首次显示为 ≤3.73 V 低、3.74–3.80 V 中、≥3.81 V 高。应用按所选型号和已核对固件启用规则；详情会显示量化边界的灯档歧义和验证范围。[固件证据与复核方法](docs/ZOWIE_SUPPORT.md)包含 11 个镜像哈希及离线执行脚本。

**软件每 60 秒查询一次，不代表鼠标每 60 秒上报一次。** 电脑读取的是接收器缓存，界面更新时间是查询时间，实际采样时间未知；查询不能强制鼠标重新采样。固件实际无线电压上报周期尚未确定。鼠标版本为零、前后不一致或读取失败时隐藏电压和档位。

更多命令格式、缓存行为和固件分析边界见 [协议说明](docs/PROTOCOLS.md)。

## 命令行

```powershell
python pulse_app.py --once
python pulse_app.py --list-zowie-models
python pulse_app.py --zowie-model EC2-DW --once
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
python -m unittest -v test_devices test_zowie_models test_discovery test_peripheral_protocols
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
| `modern_ui.py` | 深色电竞桌面面板及设置、详情弹窗 |
| `device_discovery.py` | 物理设备发现、去重及电量适配器分派 |
| `razer_proto.py` | 雷蛇电量与充电状态只读协议 |
| `mchose_proto.py` | 迈从鼠标状态查询与接口白名单 |
| `keyboard_proto.py` | 已知无线键盘协议与 RK 研究边界 |
| `logitech_proto.py` | HID++ 只读电池特征及多槽设备 |
| `zowie_profiles.py` | 按型号、固件区分的电量灯映射及验证范围 |
| `resident_app.py` | 单实例、后台常驻及原生托盘线程 |
| `device_service.py` | 面板、托盘与命令行共享的设备读取 |
| `edifier_proto.py` | G5 MAX 状态查询 |
| `zowie_proto.py` | EC2-DW 只读电压查询及显示分档 |
| `hid_win.py` | Windows HID 访问 |
| `tray_common.py` | 配置、自启动及 IPC |
| `test_devices.py` | 无硬件协议与状态测试 |

不发布官方固件、更新器、原始抓包、设备清单、个人设置或开发环境依赖。

## 参与贡献与反馈

欢迎通过 [Issues](https://github.com/SHISGAY114514/pulse-wireless-battery/issues/new/choose) 反馈问题、提出功能建议或提交新设备的支持请求。设备型号、连接方式、VID/PID 与固件版本有助于复现；日志请先移除用户名、序列号和设备实例路径。

贡献流程及验证要求见 [CONTRIBUTING.md](CONTRIBUTING.md)，已知协议限制见 [docs/PROTOCOLS.md](docs/PROTOCOLS.md)。

## 许可证与致谢

本项目统一采用 [MIT 许可证](LICENSE)，允许修改、商用及再分发，须保留版权和许可声明。原作者与维护者署名、授权记录及第三方说明见 [NOTICE](NOTICE.md)。

- [yszyszysz233/2.4G-battery-viewer](https://github.com/yszyszysz233/2.4G-battery-viewer)：原项目与 Windows HID 基础。
- [yleshchuk/zowie-battery](https://github.com/yleshchuk/zowie-battery)：EC-CW 协议研究参考。
- [0Mercy/U2-dw-battery-monitor](https://github.com/0Mercy/U2-dw-battery-monitor)：DW 只读电压协议及缓存研究参考。
- [openrazer/openrazer](https://github.com/openrazer/openrazer)：雷蛇报文及电量查询协议参考。

本项目为非官方工具，设备名称及商标归各自权利人所有。来源与授权说明见 [NOTICE](NOTICE.md)。
