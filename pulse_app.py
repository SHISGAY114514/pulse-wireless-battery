"""Modern panel entry point; shares readers with the original tray and diagnostics."""
from __future__ import annotations
import argparse
import json
import sys
import device_service
import tray_common as tc
from zowie_profiles import MODEL_CHOICES, PROFILES

def main():
    parser=argparse.ArgumentParser(description="PULSE · 卓威 / 漫步者设备电量")
    parser.add_argument("--once",action="store_true",help="读取一次并退出")
    parser.add_argument("--zowie-model",choices=MODEL_CHOICES,help="保存实际卓威型号；auto 仅读电压")
    parser.add_argument("--list-zowie-models",action="store_true",help="列出型号与验证范围")
    parser.add_argument("--json",action="store_true",help="以 JSON 输出实际读取结果")
    parser.add_argument("--json-file",help="将一次真实读取结果保存为 JSON")
    parser.add_argument("--autostart",choices=["on","off","status"])
    parser.add_argument("--install-links",action="store_true")
    parser.add_argument("--background","--tray",dest="background",action="store_true",help="静默启动后台托盘")
    parser.add_argument("--show",action="store_true",help="打开已有面板")
    parser.add_argument("--hide",action="store_true",help="隐藏面板并保留后台")
    parser.add_argument("--quit",action="store_true",help="彻底退出后台程序")
    parser.add_argument("--status-file",help="将后台状态写入 JSON")
    parser.add_argument("--status",action="store_true",help="查看后台状态")
    parser.add_argument("--refresh",action="store_true",help="让后台刷新电量")
    parser.add_argument("--debug",action="store_true",help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.list_zowie_models:
        if sys.stdout:
            print("auto: 未选择（仅读电压）")
            for model, profile in PROFILES.items():
                print(f"{model}: {profile.evidence}")
        return
    if args.zowie_model:
        cfg=tc.load_config()
        cfg["zowie_model"]=args.zowie_model
        tc.save_config(cfg)
        tc.post_to_tray(tc.MSG_RELOAD)
    if args.status or args.status_file:
        from resident_app import runtime_status
        value=json.dumps(runtime_status(),ensure_ascii=False,indent=2)
        if args.status_file:
            from pathlib import Path
            Path(args.status_file).write_text(value,encoding="utf-8")
        elif sys.stdout:
            print(value)
        return
    if args.hide or args.quit or args.refresh:
        tc.post_to_tray(tc.MSG_EXIT if args.quit else tc.MSG_HIDE if args.hide else tc.MSG_REFRESH)
        return
    if args.autostart:
        if args.autostart=="status":
            if sys.stdout:
                print(json.dumps(tc.autostart_status(),ensure_ascii=False,indent=2))
        else:
            ok=tc.set_autostart(args.autostart=="on")
            tc.post_to_tray(tc.MSG_RELOAD)
            if sys.stdout:
                print("设置成功" if ok else "设置失败")
        return
    if args.install_links:
        print(json.dumps(tc.install_desktop_links(),ensure_ascii=False,indent=2))
        return
    if args.once or args.json or args.json_file:
        rows=device_service.read_all()
        if args.json_file:
            from pathlib import Path
            Path(args.json_file).write_text(json.dumps([r.to_dict() for r in rows],ensure_ascii=False,indent=2),encoding="utf-8")
            return
        if args.json:
            print(json.dumps([r.to_dict() for r in rows],ensure_ascii=False,indent=2))
        else:
            for row in rows:
                value=f"{row.level}%" if row.level is not None else (f"{row.voltage:.2f} V" if row.voltage is not None else "--")
                print(f"{row.name}: {value}  ·  {row.message}  ·  {row.source}")
        return
    from resident_app import run
    run(background=args.background)

if __name__=="__main__":
    main()
