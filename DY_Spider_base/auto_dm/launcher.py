# -*- coding: utf-8 -*-
"""
统一启动入口（封装版）
------------------------------------------------------------------
取消"python -m"脚本启动制，改为单一可执行文件按参数分流：
    dyautodm.exe                 -> 默认启动 WebView 前端控制台（1:1 复刻原型 HTML）
    dyautodm.exe --mode web      -> WebView 前端控制台（1:1 复刻原型 HTML）
    dyautodm.exe --mode daemon   -> 浏览器常驻守护（凭证保活）
    dyautodm.exe --mode recv     -> 私信接收守护

注：GUI（tkinter）内容已去除，默认进入 WebView 模式。
打包后（PyInstaller）sys.executable 即 exe 自身，守护由 WebView 内部
经本入口带 --mode 拉起无窗口子进程，从而做到"双击即运行、无需任何脚本"。
"""
import sys
import os

# 确保项目根目录在 sys.path（开发态直接 python auto_dm/launcher.py 也能跑）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = "web"
    # 解析 --mode（放在最前，避免与子命令参数冲突）
    if "--mode" in argv:
        i = argv.index("--mode")
        if i + 1 < len(argv):
            mode = argv[i + 1]
            del argv[i:i + 2]

    if mode == "daemon":
        from auto_dm import browser_daemon
        # browser_daemon.main 内部用 argparse 读取 --account 等，直接透传
        sys.argv = [sys.argv[0]] + argv
        browser_daemon.main()
    elif mode == "recv":
        from auto_dm import recv_daemon
        sys.argv = [sys.argv[0]] + argv
        recv_daemon.main()
    elif mode == "web":
        from auto_dm import web_bridge
        web_bridge.launch_web()
    else:
        # 未知的 mode（原 gui 已去除）统一回退到 web
        from auto_dm import web_bridge
        web_bridge.launch_web()


if __name__ == "__main__":
    main()
