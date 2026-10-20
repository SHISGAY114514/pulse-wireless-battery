# 键盘与新增外设支持

2026-10-21。适配按设备 ID、HID 集合、报告格式与回复校验确定，不能只凭品牌推断。除明确的本机实测项外，以下均是公开协议适配，未逐型号实测；不宣称覆盖“绝大多数”外设。

| 品牌 / 路径 | 当前接入 | 验证边界 |
|---|---|---|
| MCHOSE 鼠标 | Feature 11/12 的 06 状态查询，短/长报告回退 | **本机 A7 Ultra：有线与 5253:1020 纯 2.4G 已读到电量和充电标记** |
| 标准 HID 键盘 | 按声明的电池 Feature / Input 字段取值；GET_INPUT_REPORT 不支持时短时等待对应电池输入 | 不解析或保存按键记录，不将信号强度当作电量 |
| RK R98 | 识别共享 25A7:FA70 接收器与 258A:00C1 本体；候选固件离线灯分支研究 | **尚未读到自动电量或电压，不能填入九格或 90%**；见[研究报告](RK_R98_RESEARCH.md) |
| ROG | Falchion / Falchion RX、Claymore II、Scope RX TKL Wireless、Scope II 96、Azoth / Extreme / X、Omni 的已列 ID | `12 01` 电源查询；Falchion 与其他型号字段不同，Omni 额外验证重复字段；无电池的 FALCHION ACE 75 HE 排除 |
| Razer 键盘 | BlackWidow V3 Pro、V3 Mini、DeathStalker V2 Pro / TKL、BlackWidow V4 Mini / Tenkeyless HyperSpeed 的已列 ID | 91 字节 Feature，只读 07:80 / 07:84；未实机验证键盘，DeathAdder V3 Pro 鼠标已实测 |
| MCHOSE K99 | 05AC:024F 且 FF02 20 字节接口；K99 V3 3837:3033 的匹配厂商接口 | 13:4A 或 V3 00:55:03；K99 充电标记仍需实机核对，V3 充电状态保持未知 |
| DAREU | 260D:0101 的 TK51G / EK75 协议，260D:0042 接收器槽转发 | 只构造 GetBattery / GetSlots；无线转发为推断适配，尚未实机验证；不代表全品牌兼容 |
| Logitech | 公开 HID++ 1004 / 1000 / 1001 电池特征和 1.0 电池寄存器 | 可展开同接收器多个逻辑设备，物理接收器只计一次；原生粗档直接显示，只有电压则不推算百分比；无本机设备验证 |

完整 PID 白名单在 `keyboard_proto.py`、`mchose_proto.py`、`razer_proto.py` 和 `logitech_proto.py`。未知身份不会扫描私有命令。RK 的共享接收器显示 Compx 名称，以免误认成其他 RK 型号；有线和无线身份也不会靠 PID 猜测合并。

## 迈从休眠

A7 Ultra 休眠时可能回复命令回显及空身份，不能按 0% 解释。有效读数最多保留十分钟；界面、详情和托盘以灰色标注“上次成功读取 / 非当前值”及时间，充电状态不从缓存继承。拔出接收器或缓存到期后移除旧值；下一次有效查询恢复实时显示。移动鼠标后手动刷新可再次尝试。

## 来源与独立实现

- [alexfrih/mchose-linux](https://github.com/alexfrih/mchose-linux)：迈从鼠标状态协议。
- [JKWTCN/BatteryMonitor](https://github.com/JKWTCN/BatteryMonitor)：多品牌身份、字段与设备差异研究。
- [arutkowski00/mchose-hid-bridge](https://github.com/arutkowski00/mchose-hid-bridge)：K99 / K99 V3 报告及校验事实。
- [seerge/g-helper](https://github.com/seerge/g-helper)：ROG 键盘电源查询及型号差异。
- [pwr-Solaar/Solaar](https://github.com/pwr-Solaar/Solaar)：HID++ 电池、名称及设备类型协议事实。
- [OpenRazer](https://github.com/openrazer/openrazer)：雷蛇键盘型号与事务差异。

PULSE 独立实现上述协议，不复制这些项目的运行代码。它们的第三方代码仍受各自许可证约束；测试通过不等于真实硬件兼容认证。
