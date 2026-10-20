# 电量引擎架构

## 数据流

`pulse_app / modern_ui / resident_app → device_service.read_all → PollSession → Adapter → 协议解析 / standard_hid → Reading`

| 模块 | 职责 |
|---|---|
| `readings.py` | 独立的数据模型、接收器计数、CLI 的百分比 / 档位 / 电压显示规则 |
| `device_identity.py` | 物理设备分组、稳定的本地哈希标识、外设分类 |
| `device_discovery.py` | 有序适配器注册表、轮询会话、异常隔离、热插拔清理 |
| `device_adapters.py` | 通用、迈从、键盘和 HID++ 结果转换 |
| `standard_hid.py` | 所有入口共用的描述符解析、报告读取、范围校验与缩放 |
| `hid_transport.py` | 按物理设备命名的跨进程查询互斥锁 |
| `hid_win.py` | Windows 枚举、USB 父节点、HID 能力与输入输出、异步取消完成 |
| `receiver_protocols.py` | ASUS 鼠标、VGN MouseEnc、VGN / ATK Weisheng 键盘 |
| `aula_proto.py` | AULA / AJAZZ 接收器 GET_DEVICE_INFO |
| `*_proto.py` | 各协议的请求、解析与设备差异 |

## 状态与并发

每个 `PollSession` 持有自己的卓威缓冲和迈从缓存，支持独立实例测试。一次扫描只读取一次配置；同一会话的扫描互斥，物理设备默认由四个工作线程读取。每个设备失败只影响自身的卡片，输出按稳定顺序排序。

卓威继续使用原有公共互斥锁；本进程的卓威任务先排队，避免互相耗尽等待时间。其他协议使用按物理设备命名的锁，慢设备不再占用卓威锁。HID++ 每个槽独立分配查询预算，先读完所有槽的电量，再查询名称，避免前面的休眠槽或名称查询耗尽后续设备机会。

拔出设备清除该物理设备状态；扫描失败清除会话缓存。迈从缓存最多保留十分钟，明确标注非当前值，不继承充电状态，系统时钟回退时不使用未来缓存。卓威固件、型号、接口变化与读取失败继续重置档位缓冲。

## 通用 HID

仅使用描述符声明的电池字段，支持 Feature、Input、Usage Range、非零逻辑下限与有符号值。同一报告 ID 一轮只 GET 一次；一个字段或报告失败后继续尝试其余字段。GET_INPUT_REPORT 无有效电量时，只在声明的电池 Input ID 中短时监听。错误 ID、空值、逻辑范围外值均不截断成 0% 或 100%。调试窗口与常驻面板共用这一实现。

标准电池字段可以回退补充失败的单设备厂商读取。卓威不使用通用百分比回退，HID++ 多槽不把接收器的一个通用字段分配给所有逻辑设备。

## 扩展协议

新增协议应提供明确的 VID/PID、集合 / 报告长度和 ID 筛选、只读请求构造器、严格回复解析器以及异常测试。注册 `Adapter` 即可接入发现、卡片、托盘和 CLI。多槽协议返回多个 `Reading`，共用 `receiver_key`。

识别 USB 身份不等于完成硬件验证。不要从接收器在线推断外设在线，不用电压曲线补造容量，不把原生粗档改写成精确百分比。未确认私有协议的设备保留不可用状态。

Windows 的同步 Feature IOCTL 由系统驱动完成，不能承诺对故障第三方驱动实现硬超时；输入监听和协议重试具有各自的等待预算。测试覆盖协议失败和正常设备，不模拟所有内核驱动故障。

## 验证与打包

```powershell
python -m unittest discover -v
python -m pip install ruff==0.17.1
python -m ruff check .
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm PULSE.spec
python test_resident_lifecycle.py dist/PULSE.exe
python tools/package_release.py
```

`unittest discover` 不启动实机生命周期脚本；该脚本仅显式运行。GitHub Actions 在 Windows Python 3.10 / 3.12 上运行静态检查和硬件无关测试，并构建便携版。实机验收与离线固件复核另行记录，不能由 CI 替代。
