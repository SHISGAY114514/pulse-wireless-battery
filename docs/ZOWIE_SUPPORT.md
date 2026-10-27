# 卓威无线鼠标：支持范围与固件依据

核对日期：2026-10-21。**尚未覆盖所有近年无线型号。**“离线验证”表示执行了提取固件中的实际 ARM 指令，不表示每款鼠标都已连接实测，也没有从这些鼠标闪存回读固件。

## 型号与连接矩阵

| 型号 | 鼠标固件 | 接收器 | 档位能力与证据 |
|---|---|---|---|
| EC1-DW、EC2-DW、EC3-DW | 2524 / 2526 | 04A5:800A，8511 / 8507 | EC-DW 固件已离线验证；仅 EC2-DW + 2524 + 8511 已实机读取 |
| FK2-DW | 2524 / 2526 | 同上 | FK 固件已离线验证；未实机验证 |
| ZA13-DW | 2524 / 2526 | 同上 | ZA13 固件已离线验证；未实机验证 |
| S2-DW | 2524 / 2526 | 同上 | S2 固件已离线验证；未实机验证 |
| U2-DW | 2524 / 2526 | 同上 | U2DW 固件已离线验证；未实机验证 |
| 上述七款的 Glossy / 白色版 | **仅 2526** | 同上 | 官网型号 `*-DW-WH` 指向相同更新包，工具按家族和鼠标 PID 选择同一镜像；未实机验证 |
| 早期 U2 + ER2-80 4K 接收器 | **2511** | 同上 | U2 固件已离线验证；版本来自镜像中的 `0x2511` 常量；未实机验证 |
| 早期 U2 + 原装 1K 接收器 | 社区报告 2027 / 8027；持续刷新未验证 | 04A5:8008 | 自动面板仅识别；独立诊断仅查询已报告的 B440 / B415，不显示档位，见 [Issue #1 调查](U2_8008_INVESTIGATION.md) |
| FK1-DW、ZA12-DW | 未取得对应镜像 | 04A5:800A | 可尝试已验证接收器的电压查询；**不显示档位** |
| EC1-CW、EC2-CW、EC3-CW | 未取得固件 | 04A5:8006 | 手动选择后尝试公开的 `A5 03` 电压查询；Windows 未实机验证；**不显示档位** |

接收器 8511 已实机读取；8507 的查询分派及回复格式已离线验证。原装小接收器、其他 USB ID、其他固件不属于已验证范围。程序不执行固件升级或重新配对。

## 型号选择

在 **偏好设置 → 卓威鼠标型号 → 选择型号** 中选择实际型号。DW 共用接收器 `04A5:800A`，相同版本号也不能确定配对鼠标家族，因此不冒充自动识别。

新安装默认“未选择（仅读电压）”。已有设置保留；切换型号、接口或固件、断开或读取失败后，清除档位缓冲。未知固件保留可读的缓存电压，隐藏档位。动态发现版本按物理设备分组后分别查询多个接收器，各自维护缓冲；多个卓威接收器需在各自“连接详情 → 设置此接收器的鼠标型号”中保存独立型号，避免把全局默认型号套到不同鼠标上。底层收到无法区分的多组接口时仍拒绝查询。

```powershell
python pulse_app.py --list-zowie-models
python pulse_app.py --zowie-model FK2-DW --once
python pulse_app.py --zowie-model "EC2-DW Glossy" --once
```

`--zowie-model` 会保存选择，并通知已有后台实例重新读取设置。型号设置同刷新间隔一起保存在用户本地配置中。

## 固件如何分档

从官方更新工具提取每个系列的 `.bin`，反汇编确认 ADC 转换、电压编码、启动电量灯选择和充电分支；再用 Unicorn 执行相应函数。每个镜像检查 0–4095 的全部 ADC 输入，在启动、正常运行、充电三种状态下记录 LED 处理函数入口，并检查两种电压修正状态，共 **8192 组算术输入**。

| 固件家族 | ADC 比较边界 | 一格起点 | 两格起点 | 三格起点 |
|---|---|---|---|---|
| EC-DW 2524 / 2526 | 3398 / 3472 / 3590 | ADC 3399 → 3680 mV | ADC 3473 → 3761 mV | ADC 3591 → 3888 mV |
| FK、ZA13、S2、U2DW 2524 / 2526；U2 2511 | 3398 / 3446 / 3510 | ADC 3399 → 3680 mV | ADC 3447 → 3732 mV | ADC 3511 → 3802 mV |

换算链为 `floor(ADC × 0x3CA50 / 0x38000)`，随后除以 10、限幅到 300–500、减去 300 编码；某些状态把 368、369 修正为 370。单位由公式和电池范围推断。接收器电压分辨率只有 0.01 V，同一读数可能对应两侧的 ADC；详情会明确显示“一格 / 两格（边界）”等歧义。

ADC ≤3398 对应低电量提示；正常运行时较高电压通常熄灯。这里推断的是**启动电量灯对应档位**，不是实时灯态。充电、满电还受 GPIO、计数器及内部状态控制，三格不等于充满。

### 应用高中低显示

| 家族 | 低档（首次） | 中档（首次） | 高档（首次） |
|---|---|---|---|
| EC-DW | ≤3.75 V | 3.76–3.89 V | ≥3.90 V |
| FK、ZA13、S2、U2DW、U2 | ≤3.73 V | 3.74–3.80 V | ≥3.81 V |

这是固件阈值之上的**应用保守规则**，不是鼠标返回的档位字段。EC-DW 保留 EC2-DW 在 3.88 V 时为中档的观察与原有显示策略；其他系列在量化边界上暂归较低档。低电量提示与一格都归低档。

缓冲规则：中档下界记作 M、高档下界记作 H；低→中需 M+0.01 V，中→低需 M−0.02 V，中→高需 H，高→中需 H−0.02 V。缓冲只在同一读取上下文中生效。详情同时保留固件灯档与应用规则，避免将保守显示说成硬件的精确状态。

## 可复核材料

- [完整哈希、函数地址、离线结果及官网下载关联](firmware-evidence.json)：11 个不同鼠标镜像，90112 组算术输入，无差异。
- [鼠标离线核验脚本](../tools/verify_zowie_firmware.py)：只接受已记录 SHA-256 的镜像，执行实际指令并核对灯态转折。
- [接收器离线核验脚本](../tools/verify_zowie_receiver.py)：对 8507、8511 各执行 792 组查询，检查版本、电压、鼠标版本回复格式和缓存未被修改。

```powershell
python -m pip install -r requirements-research.txt
python tools/verify_zowie_firmware.py path/to/extracted_mouse.bin
python tools/verify_zowie_receiver.py path/to/extracted_receiver.bin
```

上述依赖仅用于离线研究；运行 PULSE 无需安装。仓库与便携包不分发厂商固件或更新器。离线执行把特殊硬件状态 getter 固定返回 0，并在 LED 处理函数入口停止；不等价于执行完整设备、不验证真实 LED 接线、电池健康或无线采样周期。

## 缺口与继续验证的条件

EC-CW 在当前更新工具的设备表中为 `isSupportUpdate: false`，没有对应固件镜像；官网下载 API 也仅返回说明书等资料。公开项目 [yleshchuk/zowie-battery](https://github.com/yleshchuk/zowie-battery) 提供电压协议线索，但它使用通用 LiPo 曲线估算百分比，不能证明固件 LED/电压映射。本项目独立实现只读电压查询，没有复制其 GPL 代码、曲线或平滑逻辑。

FK1-DW、ZA12-DW 的官网下载页当前没有固件条目，现有 2524 / 2526 更新包没有独立对应镜像。相同外形系列、相同 USB PID、相同版本号和通用说明书都不足以证明内部阈值相同，暂不套用 FK2 / ZA13 规则。

早期 U2 已找到相同的 2511 鼠标镜像：它同时出现在官方 ER2-80 v1.0.13.0 工具与较新的更新包中。2026-10-27 已纳入 Issue #1 的原装 8008 / 8027 社区回复证据，并补充 2511 服务计数的离线验证；仍未取得 2027 / 8027 镜像，也未证明旧版持续无线刷新。协议差异、诊断工具和证据边界见 [初代 U2 调查](U2_8008_INVESTIGATION.md)。

## 主要来源

- [ZOWIE 鼠标目录](https://zowie.benq.com/en-us/mouse.html)
- [EC2-DW 下载页](https://zowie.benq.com/en-us/support/products/mouse/ec2-dw/download.html)；其他已支持型号的下载关联保存在证据 JSON 中。
- [EC2-DW Glossy 下载页](https://zowie.benq.com/en-us/support/products/mouse/ec2-dw-glossy/download.html)（API 型号为 EC2-DW-WH）
- [FK1-DW 下载页](https://zowie.benq.com/en-us/support/products/mouse/fk1-dw/download.html)、[ZA12-DW 下载页](https://zowie.benq.com/en-us/support/products/mouse/za12-dw/download.html)、[EC2-CW 下载页](https://zowie.benq.com/en-us/support/products/mouse/ec2-cw/download.html)
- [ER2-80 4K 接收器](https://zowie.benq.com/en-us/mouse/er2-80.html)、[BenQ 商店的 U2 / DW 兼容说明](https://compre.benq.com.br/zowie-er20-80-4k/p)
- [0Mercy/U2-dw-battery-monitor](https://github.com/0Mercy/U2-dw-battery-monitor)：DW 只读协议研究参考。

固件包来自官网下载链接；2524 的旧包另通过驱动归档取得，未运行。新 2526 工具及旧 ER2-80 v1.0.13.0 工具的 BenQ Authenticode 签名验证通过。研究仅提取和离线执行函数，不向设备刷写任何镜像。
