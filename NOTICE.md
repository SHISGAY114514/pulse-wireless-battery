# 来源与授权状态

PULSE 基于 [yszyszysz233/2.4G-battery-viewer](https://github.com/yszyszysz233/2.4G-battery-viewer) 修改，保留上游 Git 提交、原作者署名和原有源码。原版说明保存在 [docs/UPSTREAM_README.md](docs/UPSTREAM_README.md)。

本仓库维护者为 [SHISGAY114514](https://github.com/SHISGAY114514)。新增工作包括圆角桌面面板、同进程后台常驻与托盘恢复、自启动设置、漫步者 G5 MAX 状态读取、卓威 EC2-DW 电压与档位显示及相关测试。

## 项目许可证：MIT

本仓库版本的项目源码和随附文档统一按根目录的 [MIT 许可证](LICENSE) 发布，允许使用、复制、修改、合并、发布、分发、再许可及商用，须保留版权和许可声明，且不提供担保。

授权依据记录：2026-10-20，维护者 SHISGAY114514 明确确认，已取得原项目作者 yszyszysz233 的授权，可以将原有代码按 MIT 开源。本仓库依据该确认，将先前仅覆盖 PULSE 新增贡献的 MIT 条款扩展为本仓库项目的统一许可证。

原作者 yszyszysz233、维护者 SHISGAY114514 及 PULSE 贡献者的署名保留在 `LICENSE`、本说明及 Git 历史中。依赖项和第三方材料若有各自许可证，仍适用其原有条款。

协议研究参考：

- [yleshchuk/zowie-battery](https://github.com/yleshchuk/zowie-battery)：EC-CW 系列的协议说明；该项目声明 GPL-3.0-or-later。PULSE 没有复制其运行代码或通用电压曲线。
- [0Mercy/U2-dw-battery-monitor](https://github.com/0Mercy/U2-dw-battery-monitor)：DW 系列只读电压命令、缓存行为等研究。PULSE 的协议模块独立实现，没有复制其运行代码、校准文件或百分比算法；采用相同的已公开互斥量名称以协调共同使用接口的工具。

卓威、ZOWIE、BenQ、漫步者、Edifier、HECATE 等名称和商标归各自权利人所有。本项目为非官方工具。
