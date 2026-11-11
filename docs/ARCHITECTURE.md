# 电量引擎架构

## 数据流

`pulse_app / modern_ui / resident_app → device_service.read_all → PollSession → Adapter → 协议解析 / standard_hid → Reading`

| 模块 | 职责 |
|---|---|
| `readings.py` | 独立的数据模型、接收器计数、CLI 的百分比 / 档位 / 电压显示规则 |
| `device_identity.py` | 物理设备分组、稳定的本地哈希标识、外设分类 |
| `device_discovery.py` | 有序适配器注册表、轮询会话、异常隔离、热插拔清理 |
| `headset_recovery.py` | 迈从耳机缺失电量的定向恢复调度、重试间隔与成功/拔出清理 |
| `device_adapters.py` | 通用、迈从、键盘和 HID++ 结果转换 |
| `standard_hid.py` | 所有入口共用的描述符解析、报告读取、范围校验与缩放 |
| `hid_transport.py` | 按物理设备命名的跨进程查询互斥锁 |
| `hid_win.py` | Windows 枚举、USB 父节点、HID 能力与输入输出、异步取消完成 |
| `receiver_protocols.py` | ASUS 鼠标、VGN MouseEnc、VGN / ATK Weisheng 键盘 |
| `aula_proto.py` | AULA / AJAZZ 接收器 GET_DEVICE_INFO |
| `*_proto.py` | 各协议的请求、解析与设备差异 |

## 状态与并发

每个 `PollSession` 持有自己的卓威缓冲和迈从缓存，支持独立实例测试。一次扫描只读取一次配置；同一会话的扫描互斥，物理设备默认由四个工作线程读取。每个设备失败只影响自身的卡片，输出按稳定顺序排序。

常驻面板在迈从耳机未返回有效电量时，前两次间隔 5 秒重试，持续失败后保持 15 秒间隔，避免耳机长时间关闭后再开机仍需等待数分钟。`PollSession.retry_headsets` 复用最近全量扫描中的接口描述，只查询指定耳机，每次打开新的 HID 句柄并保留原协议校验和超时。全量扫描替换接口快照，拔出或枚举失败清理；恢复结果仅合并对应卡片，不覆盖其他设备。恢复调度和普通全量刷新共用一个 Tk 定时器与忙碌保护，采用单调时钟，恢复查询不推迟全量刷新；暂停自动同步或退出后停止自动查询。

成功读取（包括 0%）后停止额外重试。持续离线时每台耳机约每分钟四次定向查询，等待 I/O 不占用 CPU 忙循环，不额外枚举所有设备；仅查询时间变化而显示状态不变时不重绘或重建托盘图标。时间间隔从查询完成开始计算，不承诺内核异常或其他设备阻塞时的硬实时响应。

卓威继续使用原有公共互斥锁；本进程的卓威任务先排队，避免互相耗尽等待时间。其他协议使用按物理设备命名的锁，慢设备不再占用卓威锁。HID++ 每个槽独立分配查询预算，先读完所有槽的电量，再查询名称，避免前面的休眠槽或名称查询耗尽后续设备机会。

拔出设备清除该物理设备状态；扫描失败清除会话缓存。迈从缓存最多保留十分钟，明确标注非当前值，不继承充电状态，系统时钟回退时不使用未来缓存。卓威固件、型号、接口变化与读取失败继续重置档位缓冲。

## 桌面渲染与交互

`modern_ui.Dashboard` 使用原生 Tk Canvas 组织浅银液态玻璃界面，Pillow 生成并缓存玻璃背景与设备插图。模糊、折射、高光及半透明合成针对应用内背景，不读取窗口后面的桌面，也不表示原生桌面穿透。视觉与交互约定见 [UI_DESIGN.md](UI_DESIGN.md)。

悬停、点击、切页和同步进度由 Tk 主线程更新，设备读取仍通过工作线程和消息队列返回。玻璃材质与插图复用缓存，避免逐帧重新生成；面板隐藏时停止视觉动画，自动查询、设备通知与耳机恢复调度继续按既有设置运行。退出时清理动画回调与后台资源。

源码运行依赖列在 `requirements.txt`：Python 3.10+、自带的 tkinter 和 Pillow。`requirements-build.txt` 引用运行依赖并增加 PyInstaller；便携 EXE 包含运行所需库，用户无需另装 Python 或 Pillow。

## 通用 HID

仅使用描述符声明的电池字段，支持 Feature、Input、Usage Range、非零逻辑下限与有符号值。同一报告 ID 一轮只 GET 一次；一个字段或报告失败后继续尝试其余字段。GET_INPUT_REPORT 无有效电量时，只在声明的电池 Input ID 中短时监听。错误 ID、空值、逻辑范围外值均不截断成 0% 或 100%。调试窗口与常驻面板共用这一实现。

标准电池字段可以回退补充失败的单设备厂商读取。卓威不使用通用百分比回退，HID++ 多槽不把接收器的一个通用字段分配给所有逻辑设备。

## 扩展协议

新增协议应提供明确的 VID/PID、集合 / 报告长度和 ID 筛选、只读请求构造器、严格回复解析器以及异常测试。注册 `Adapter` 即可接入发现、卡片、托盘和 CLI。多槽协议返回多个 `Reading`，共用 `receiver_key`。

识别 USB 身份不等于完成硬件验证。不要从接收器在线推断外设在线，不用电压曲线补造容量，不把原生粗档改写成精确百分比。未确认私有协议的设备保留不可用状态。

Windows 的同步 Feature IOCTL 由系统驱动完成，不能承诺对故障第三方驱动实现硬超时；输入监听和协议重试具有各自的等待预算。测试覆盖协议失败和正常设备，不模拟所有内核驱动故障。

## 验证与打包

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -v
python -m pip install ruff==0.17.1
python -m ruff check .
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm PULSE.spec
python test_resident_lifecycle.py dist/PULSE.exe
python tools/package_release.py
```

`unittest discover` 不启动实机生命周期脚本；该脚本仅显式运行。GitHub Actions 在 Windows Python 3.10 / 3.12 上运行静态检查和硬件无关测试，并构建便携版。实机验收与离线固件复核另行记录，不能由 CI 替代。
