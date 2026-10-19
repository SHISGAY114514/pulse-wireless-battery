# 来源与授权状态

PULSE 基于 [yszyszysz233/2.4G-battery-viewer](https://github.com/yszyszysz233/2.4G-battery-viewer) 修改，保留上游 Git 提交、原作者署名和原有源码。原版说明保存在 [docs/UPSTREAM_README.md](docs/UPSTREAM_README.md)。

本仓库维护者为 [SHISGAY114514](https://github.com/SHISGAY114514)。新增工作包括圆角桌面面板、同进程后台常驻与托盘恢复、自启动设置、漫步者 G5 MAX 状态读取、卓威 EC2-DW 电压与档位显示及相关测试。

## PULSE 新增贡献：MIT

维护者将本次原创新增贡献按 [MIT 许可证](LICENSE) 授权，允许使用、复制、修改、商用及再分发，须保留版权和许可声明，且不提供担保。

MIT 授权的完整新增文件或独立重写文件：

- `device_service.py`、`edifier_proto.py`、`zowie_proto.py`
- `modern_ui.py`、`resident_app.py`、`pulse_app.py`、当前版本的 `my_devices.py`
- `make_icon.py`、`pulse.ico`
- `test_devices.py`、`test_resident_lifecycle.py`
- `PULSE.spec`、`requirements-build.txt`
- 当前 `README.md`、`NOTICE.md`、`docs/PROTOCOLS.md`

维护者对上游文件新增的原创修改部分也按 MIT 授权；这不改变这些文件中原有代码的授权状态。

## 上游原有内容：授权待确认

截至 2026-10-20 核查，上游仓库没有提供 `LICENSE` 文件，也未在其 README 中指定许可证。我们尚无原作者对整个项目采用 MIT 的授权依据。

来自上游且不能由本仓库替原作者重新授权的内容包括：`app.py`、`battery.py`、`bt_battery.py`、`darkrim_proto.py`、`hid_win.py`、`tray_app.py`、`tray_common.py`、`win_icon.py`、`win_link.py`、`win_shell.py`、`PROMPT.md`、`docs/UPSTREAM_README.md`，以及 Git 历史中的其他上游内容。

根目录的 `LICENSE` **仅覆盖 PULSE 原创贡献，不表示整个组合项目已取得 MIT 授权**。上游权利和署名继续保留。整套项目的进一步再分发或再许可，仍需先确认上游适用授权。

协议研究参考：

- [yleshchuk/zowie-battery](https://github.com/yleshchuk/zowie-battery)：EC-CW 系列的协议说明；该项目声明 GPL-3.0-or-later。PULSE 没有复制其运行代码或通用电压曲线。
- [0Mercy/U2-dw-battery-monitor](https://github.com/0Mercy/U2-dw-battery-monitor)：DW 系列只读电压命令、缓存行为等研究。PULSE 的协议模块独立实现，没有复制其运行代码、校准文件或百分比算法；采用相同的已公开互斥量名称以协调共同使用接口的工具。

卓威、ZOWIE、BenQ、漫步者、Edifier、HECATE 等名称和商标归各自权利人所有。本项目为非官方工具。
