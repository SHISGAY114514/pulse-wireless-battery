# 参与贡献

欢迎提交问题反馈、文档改进、设备适配与 Pull Request。项目基于 [yszyszysz233/2.4G-battery-viewer](https://github.com/yszyszysz233/2.4G-battery-viewer) 开发，来源与协议参考见 [docs/REFERENCES.md](docs/REFERENCES.md)。

## 反馈问题

请使用 [Issue 模板](https://github.com/SHISGAY114514/pulse-wireless-battery/issues/new/choose)，提供：

- Windows、Python 或打包版本，以及设备型号与连接方式。
- 可重复的操作步骤、实际结果和期望结果。
- 对设备读取问题，补充 VID/PID、固件版本及脱敏后的必要报文。

接收器存在不代表设备在线。卓威显示的是接收器电压缓存和估算档位，查询时间不等于采样时间；这些限制见 [协议说明](docs/PROTOCOLS.md)。

请勿直接上传包含用户名、序列号、USB 实例路径、密钥或个人设置的完整日志。不要上传官方更新器、固件文件或未经授权的第三方代码。

## 本地开发

使用 Windows 和包含 tkinter 的 64 位 Python 3.10+。运行源码不需要第三方包：

```powershell
python pulse_app.py
python -m unittest -v test_devices
```

开发打包步骤见 [README](README.md#开发验证与打包)。无硬件测试可独立运行；常驻生命周期测试需要设备可读，不能把缺少硬件造成的失败当成已经验证通过。

## 提交修改

1. Fork 仓库，在独立分支中完成修改。
2. 保持改动聚焦；用户可见行为、设备支持范围或协议变化需要同步说明。
3. 运行与改动相关的验证。协议解析和连接状态变化应覆盖有效报文及失败路径；单纯文档修改核对链接和格式即可。
4. 在 Pull Request 中说明问题、修改后的行为、验证方式及尚未验证的条件。

新增设备请优先提交已确认的只读查询及最小脱敏回复。没有证据时不要把未知字节标成百分比，也不要默认套用其他型号的协议、满电参考或校准曲线。会重置、配对或刷写设备的命令不属于日常读取功能。

## 许可与来源

项目采用 [MIT 许可证](LICENSE)。提交的贡献应是你有权提供的内容，并在 MIT 条款下供本项目使用；保留原作者及第三方必要署名。引用其他项目时注明链接、具体参考内容及是否使用其代码。
