# 来源与授权状态

PULSE 基于 [yszyszysz233/2.4G-battery-viewer](https://github.com/yszyszysz233/2.4G-battery-viewer) 修改，保留上游 Git 提交、原作者署名和原有源码。原版说明保存在 [docs/UPSTREAM_README.md](docs/UPSTREAM_README.md)。

本仓库维护者为 [SHISGAY114514](https://github.com/SHISGAY114514)。新增工作包括圆角桌面面板、同进程后台常驻与托盘恢复、自启动设置、漫步者 G5 MAX 状态读取、卓威 EC2-DW 电压与档位显示及相关测试。

截至整理发布时，上游仓库没有提供 `LICENSE` 文件，也未在其 README 中指定许可证。本仓库暂未对整个派生项目添加新的许可证；源码公开不应被理解为原作者已授予任意再许可或商业使用权。保留各原作者的权利，进一步再分发或再许可应先确认适用授权。

协议研究参考：

- [yleshchuk/zowie-battery](https://github.com/yleshchuk/zowie-battery)：EC-CW 系列的协议说明；该项目声明 GPL-3.0-or-later。PULSE 没有复制其运行代码或通用电压曲线。
- [0Mercy/U2-dw-battery-monitor](https://github.com/0Mercy/U2-dw-battery-monitor)：DW 系列只读电压命令、缓存行为等研究。PULSE 的协议模块独立实现，没有复制其运行代码、校准文件或百分比算法；采用相同的已公开互斥量名称以协调共同使用接口的工具。

卓威、ZOWIE、BenQ、漫步者、Edifier、HECATE 等名称和商标归各自权利人所有。本项目为非官方工具。
