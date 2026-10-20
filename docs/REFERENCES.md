# 参考项目与来源

## 原项目：2.4G-battery-viewer

- 作者：[yszyszysz233](https://github.com/yszyszysz233)
- 仓库：[yszyszysz233/2.4G-battery-viewer](https://github.com/yszyszysz233/2.4G-battery-viewer)
- 本工作副本的初始基线：[f0e983e88900592237c7df9ae6b80b9dbeb3020b](https://github.com/yszyszysz233/2.4G-battery-viewer/tree/f0e983e88900592237c7df9ae6b80b9dbeb3020b)
- 基线的项目介绍：[原文归档](UPSTREAM_README.md)

PULSE 基于该项目修改，并在 GitHub 上保留 Fork 关系和上游提交。原项目提供的基础包括：

| 基础内容 | 对应文件 |
|---|---|
| Windows HID 枚举、能力解析和报告读写 | `hid_win.py` |
| 电量读取策略、原有设备协议与调试界面 | `battery.py`、`bt_battery.py`、`darkrim_proto.py`、`app.py` |
| 原生托盘、图标生成及 Windows 系统集成 | `tray_app.py`、`tray_common.py`、`win_icon.py`、`win_link.py`、`win_shell.py` |
| 原版设备面板与适配调查方法 | 基线中的 `my_devices.py`、`PROMPT.md`、README |

PULSE 对部分基础文件作了修改。当前的 `my_devices.py` 为新入口；原版内容可在基线提交中查阅。新版面板、常驻协调及设备适配等新增部分见 `modern_ui.py`、`resident_app.py`、`device_service.py`、`edifier_proto.py`、`zowie_proto.py` 和相关测试。

原项目最初适配 ATK / DARKRIM。本项目面板扩展 G5 MAX、卓威多系列无线鼠标、雷蛇及标准 HID 电池字段，不能把上游设备验证范围与本项目新增适配混为一谈。

## 协议研究参考：OpenRazer

- 仓库：[openrazer/openrazer](https://github.com/openrazer/openrazer)
- 项目声明的许可：GPL-2.0-or-later。
- 参考文件：[鼠标型号与查询](https://github.com/openrazer/openrazer/blob/master/driver/razermouse_driver.c)、[报文结构](https://github.com/openrazer/openrazer/blob/master/driver/razercommon.h)、[电量命令](https://github.com/openrazer/openrazer/blob/master/driver/razerchromacommon.c)。

参考内容为 VID/PID、90 字节报文布局、事务 ID、XOR 校验和，以及 `07:80 / 07:84` 电量和充电查询等协议事实。PULSE 的 Python 模块 `razer_proto.py` 独立实现，未复制 OpenRazer 的 GPL 运行代码；不包含其内核驱动或设置写入功能。已在本机 DeathAdder V3 Pro `1532:00B7` 接收器验证；其他 PID 的适配边界见[动态设备支持说明](DEVICE_SUPPORT.md)。

## 协议研究参考：U2-dw-battery-monitor

- 作者：[0Mercy](https://github.com/0Mercy)
- 仓库：[0Mercy/U2-dw-battery-monitor](https://github.com/0Mercy/U2-dw-battery-monitor)
- 参考内容：[DW 协议说明](https://github.com/0Mercy/U2-dw-battery-monitor/blob/main/diagnostics/PROTOCOL.md)、[电量判断方法](https://github.com/0Mercy/U2-dw-battery-monitor/blob/main/diagnostics/BATTERY_PERCENTAGE.md)

该项目为 DW 接收器的 `08 B4 40 / 25 / 15` 只读查询、电压缓存行为和电量灯分析提供了直接参考。PULSE 的读取模块独立实现，并在 EC2-DW 上验证。

其 U2-DW 电量灯 ADC 阈值为 `3398 / 3446 / 3510`，本项目分析的 EC-DW 阈值为 `3398 / 3472 / 3590`。PULSE 没有复制其运行代码、百分比模型或满电校准数据。

## 协议研究参考：zowie-battery

- 作者：[yleshchuk](https://github.com/yleshchuk)
- 仓库：[yleshchuk/zowie-battery](https://github.com/yleshchuk/zowie-battery)
- 项目声明的许可：GPL-3.0-or-later。

该项目验证的是 EC-CW 系列及相应接收器，使用 `A5 03` 查询。它为调查卓威电量接口和识别型号差异提供了线索。PULSE 没有复制其运行代码或通用锂电池估算曲线；EC-CW 查询不能直接套用到 EC-DW。

## 官方资料与独立验证

- [ZOWIE 官方电量灯使用说明](https://zowie.benq.com/en-us/support/faq/product/application/zowiegear-faq-kn-00019.html)：灯的使用场景和官方显示说明。
- 本项目另外通过本机 HID 读取、开关机对照，以及 EC-DW、FK、ZA13、S2、U2DW、U2 共 11 个固件镜像的实际 ARM 指令离线执行核对适配逻辑；官网下载的 WH 型号关联与镜像哈希见[支持矩阵](ZOWIE_SUPPORT.md)及 [firmware-evidence.json](firmware-evidence.json)。只有本机 EC2-DW 完成实机读取验证。

上述仓库的设备结果不自动构成本项目的兼容性承诺。未经处理的设备记录、固件与抓包不随仓库发布。

## 署名与许可证

新增迈从鼠标、多品牌键盘和 HID++ 的参考仓库与适配边界见[键盘及新增外设说明](KEYBOARD_SUPPORT.md)；RK 官方候选来源、修改 TEA 线索、哈希和独立复核方法见[RK R98 研究报告](RK_R98_RESEARCH.md)。上述来源属于协议事实或研究线索，不自动证明本项目在所有型号上实测通过。

感谢原项目作者及协议研究项目维护者。PULSE 保留原作者提交和署名，当前仓库统一采用 [MIT](../LICENSE)，授权依据及第三方说明见 [NOTICE](../NOTICE.md)。协议参考项目的代码仍遵循各自的许可证。


## v1.0.0 通用引擎与新增协议

ASUS 鼠标、VGN MouseEnc / Weisheng、AULA / AJAZZ 的具体参考提交、请求及字段记录见 [新增协议说明](PROTOCOLS.md#v100-新增协议路径)。本仓库为独立 Python 实现，不包含参考仓库的 C++ / C# 代码。所有 PID 和字段属于协议适配线索；硬件结果仅按本次实际验收记录声明。
