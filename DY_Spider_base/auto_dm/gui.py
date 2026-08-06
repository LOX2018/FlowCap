# coding=utf-8
"""图形化管理界面（tkinter，Python 标准库，无需额外依赖）。

启动：
  python -m auto_dm.gui
或在资源管理器双击 启动GUI.bat

功能：
  - 配置直播间号 / 私信内容 / 发送上限 / 发送间隔
  - 开关：弹幕来源 / 中控台采集 / 真发私信（调试时关掉）
  - 启动 / 停止 自动私信
  - 实时日志窗口
  - 状态栏：登录态、已发 X/N、当前监听房间
  - 【账号管理】标签页：多账号切换、新增/删除账号、密钥状态监控与一键重扫
"""

import sys
import os
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, simpledialog
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auto_dm import config as C
from auto_dm.run import AutoDM
from auto_dm import accounts


# ---------- 日志重定向到文本框 ----------
class TextSink:
    def __init__(self, text_widget):
        self._w = text_widget

    def write(self, line):
        try:
            self._w.insert(tk.END, line)
            self._w.see(tk.END)
        except Exception:
            pass


logger.remove()
logger.add(sys.stderr, level="INFO")

# 日志落盘：每次启动写入本地文件（logs/run_YYYYMMDD_HHMMSS.log），
# 即使关闭 GUI / 未运行时也能回看本次运行的完整日志。
_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
try:
    os.makedirs(_LOG_DIR, exist_ok=True)
    from datetime import datetime
    _log_file = os.path.join(_LOG_DIR, f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    logger.add(_log_file, level="DEBUG", encoding="utf-8",
               format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
               enqueue=True, retention="30 days")
except Exception:
    _log_file = None

# 自定义 sink：把每条日志推到 GUI 文本框
_gui_sink = None


def _install_sink(text_widget):
    global _gui_sink
    _gui_sink = TextSink(text_widget)
    logger.add(lambda msg: _gui_sink.write(msg), level="DEBUG")


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("抖音直播间自动私信 - 管理面板")
        self.root.geometry("820x680")

        self.adm = None

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        self.main_frame = ttk.Frame(self.notebook)
        self.account_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.main_frame, text="私信任务")
        self.notebook.add(self.account_frame, text="账号管理")

        self._build_main_widgets(self.main_frame)
        self._build_account_widgets(self.account_frame)
        self._load_config_to_ui()
        self._refresh_status()
        self._refresh_accounts()

    # ---------- 主面板 UI 构建 ----------
    def _build_main_widgets(self, parent):
        f = ttk.Frame(parent, padding=10)
        f.pack(fill=tk.X)

        # 直播间链接（支持直播页/分享短链/用户主页，自动解析房间号）
        ttk.Label(f, text="直播间链接:").grid(row=0, column=0, sticky=tk.W)
        self.live_url = ttk.Entry(f, width=55)
        self.live_url.grid(row=0, column=1, columnspan=2, sticky=tk.W, padx=4)
        self.btn_resolve = ttk.Button(f, text="解析房间号", command=self.on_resolve)
        self.btn_resolve.grid(row=0, column=3, sticky=tk.W, padx=4)
        ttk.Label(f, text="（粘贴直播页/分享短链/用户主页链接，自动识别）").grid(
            row=0, column=4, sticky=tk.W)

        # 私信内容
        ttk.Label(f, text="私信内容:").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.dm_msg = ttk.Entry(f, width=60)
        self.dm_msg.grid(row=1, column=1, columnspan=2, sticky=tk.W, padx=4, pady=4)

        # 上限 + 间隔
        ttk.Label(f, text="发送上限:").grid(row=2, column=0, sticky=tk.W)
        self.max_target = ttk.Spinbox(f, from_=1, to=9999, width=10)
        self.max_target.grid(row=2, column=1, sticky=tk.W, padx=4)
        ttk.Label(f, text="间隔(秒):").grid(row=2, column=2, sticky=tk.W)
        self.interval = ttk.Spinbox(f, from_=1, to=600, increment=0.5, width=10)
        self.interval.grid(row=2, column=3, sticky=tk.W, padx=4)

        # 开关
        self.en_live = tk.BooleanVar()
        self.en_probe = tk.BooleanVar()
        self.en_send = tk.BooleanVar()
        ttk.Checkbutton(f, text="启用弹幕来源", variable=self.en_live).grid(
            row=3, column=0, sticky=tk.W, pady=4)
        ttk.Checkbutton(f, text="启用中控台采集", variable=self.en_probe).grid(
            row=3, column=1, sticky=tk.W, pady=4)
        ttk.Checkbutton(f, text="真发私信(关=仅采集调试)", variable=self.en_send).grid(
            row=3, column=2, columnspan=2, sticky=tk.W, pady=4)

        # 按钮
        bf = ttk.Frame(parent, padding=(10, 0))
        bf.pack(fill=tk.X)
        self.btn_start = ttk.Button(bf, text="▶ 启动", command=self.on_start)
        self.btn_start.pack(side=tk.LEFT, padx=4)
        self.btn_stop = ttk.Button(bf, text="■ 停止", command=self.on_stop, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=4)
        self.btn_save = ttk.Button(bf, text="保存配置", command=self.on_save)
        self.btn_save.pack(side=tk.LEFT, padx=4)

        # 状态栏
        self.status_var = tk.StringVar(value="状态: 未启动")
        ttk.Label(parent, textvariable=self.status_var, foreground="blue").pack(
            anchor=tk.W, padx=12, pady=(4, 0))

        # 日志框
        ttk.Label(parent, text="运行日志:").pack(anchor=tk.W, padx=12, pady=(6, 0))
        self.log = scrolledtext.ScrolledText(parent, height=20, state=tk.NORMAL)
        self.log.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)
        self.log.configure(font=("Consolas", 10))
        _install_sink(self.log)

    # ---------- 账号管理 UI 构建 ----------
    def _build_account_widgets(self, parent):
        f = ttk.Frame(parent, padding=10)
        f.pack(fill=tk.X)

        # 当前账号（默认选择，新建/切换用）
        ttk.Label(f, text="当前账号:").grid(row=0, column=0, sticky=tk.W)
        self.account_var = tk.StringVar()
        self.account_combo = ttk.Combobox(
            f, textvariable=self.account_var, state="readonly", width=22)
        self.account_combo.grid(row=0, column=1, sticky=tk.W, padx=4)
        self.account_combo.bind("<<ComboboxSelected>>", self.on_account_select)

        self.btn_new = ttk.Button(f, text="新增账号", command=self.on_account_new)
        self.btn_new.grid(row=0, column=2, sticky=tk.W, padx=4)
        self.btn_del = ttk.Button(f, text="删除账号", command=self.on_account_delete)
        self.btn_del.grid(row=0, column=3, sticky=tk.W, padx=4)
        self.btn_rescan = ttk.Button(f, text="重新扫码", command=self.on_account_rescan)
        self.btn_rescan.grid(row=0, column=4, sticky=tk.W, padx=4)
        self.btn_refresh = ttk.Button(f, text="刷新状态", command=self._refresh_accounts)
        self.btn_refresh.grid(row=0, column=5, sticky=tk.W, padx=4)

        # 监测账号 / 发送账号分离绑定
        rb = ttk.LabelFrame(parent, text="账号角色绑定（确认监测与发送分别用哪个账号）", padding=8)
        rb.pack(fill=tk.X, padx=12, pady=(8, 0))
        ttk.Label(rb, text="监测账号（直播间监听/弹幕，需管理器权限看完整昵称）:").grid(
            row=0, column=0, sticky=tk.W, pady=2)
        self.monitor_var = tk.StringVar()
        self.monitor_combo = ttk.Combobox(
            rb, textvariable=self.monitor_var, state="readonly", width=22)
        self.monitor_combo.grid(row=0, column=1, sticky=tk.W, padx=4)
        self.monitor_combo.bind("<<ComboboxSelected>>", self.on_monitor_select)
        ttk.Label(rb, text="发送账号（私信发送，需私信权限）:").grid(
            row=1, column=0, sticky=tk.W, pady=2)
        self.sender_var = tk.StringVar()
        self.sender_combo = ttk.Combobox(
            rb, textvariable=self.sender_var, state="readonly", width=22)
        self.sender_combo.grid(row=1, column=1, sticky=tk.W, padx=4)
        self.sender_combo.bind("<<ComboboxSelected>>", self.on_sender_select)
        self.bind_hint = tk.StringVar(value="当前：监测= — / 发送= —")
        ttk.Label(rb, textvariable=self.bind_hint, foreground="green").grid(
            row=2, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))

        # 状态监控区
        ttk.Label(parent, text="密钥状态监控:").pack(anchor=tk.W, padx=12, pady=(8, 0))
        cols = ("账号", "状态", "ticket", "private_key", "cookie", "uid")
        self.acct_tree = ttk.Treeview(parent, columns=cols, show="headings", height=10)
        for c in cols:
            self.acct_tree.heading(c, text=c)
        widths = {"账号": 130, "状态": 180, "ticket": 70, "private_key": 90,
                  "cookie": 70, "uid": 110}
        for c in cols:
            self.acct_tree.column(c, width=widths.get(c, 90), anchor=tk.CENTER)
        self.acct_tree.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)

        self.acct_hint = tk.StringVar(value="说明：状态分 有效 / 失效 / 未扫码 / 未配置。"
                                              "选中账号后点“重新扫码”可在该账号下打开浏览器登录。")
        ttk.Label(parent, textvariable=self.acct_hint, foreground="gray").pack(
            anchor=tk.W, padx=12, pady=(0, 6))

    # ---------- 配置加载 ----------
    def _load_config_to_ui(self):
        self.live_url.insert(0, C.LIVE_ID)
        self.dm_msg.insert(0, C.DM_MESSAGE)
        self.max_target.set(str(C.MAX_TARGET))
        self.interval.set(str(C.SEND_INTERVAL))
        self.en_live.set(bool(C.ENABLE_LIVE_CHAT))
        self.en_probe.set(bool(C.ENABLE_WEB_PROBE))
        self.en_send.set(bool(C.ENABLE_SEND))

    def _build_auth_for_resolve(self):
        """构造一个带 cookie 的 DouyinAuth 供链接解析（reflow 主引擎需登录态）。

        使用当前选中账号的 .env（若无 cookie 则返回 None，reflow 退化为无登录态）。
        """
        try:
            from builder.auth import DouyinAuth
            from dotenv import load_dotenv
            env_path = accounts.current_env_path()
            if os.path.exists(env_path):
                load_dotenv(env_path, override=True)
            cookies = os.getenv("DY_COOKIES", "") or ""
            if not cookies:
                return None
            auth = DouyinAuth()
            auth.perepare_auth(cookies, "", "")
            return auth
        except Exception as e:
            logger.warning(f"[resolve] 构造解析用 auth 失败，转无登录态: {e}")
            return None

    def _resolve_to_live_id(self):
        """把输入框内容解析成 live_id，返回字符串；失败弹窗并抛异常。"""
        raw = self.live_url.get().strip()
        if not raw:
            raise ValueError("请先粘贴直播间链接 / 分享短链 / 用户主页")
        from auto_dm.link_resolve import resolve_live_id
        auth = self._build_auth_for_resolve()
        live_id, source = resolve_live_id(raw, user_data_dir="pw_profile_dm", auth=auth)
        return live_id

    def _ui_to_config(self):
        live_id = self._resolve_to_live_id()
        C.LIVE_ID = live_id
        C.DM_MESSAGE = self.dm_msg.get().strip()
        try:
            C.MAX_TARGET = int(float(self.max_target.get()))
        except Exception:
            C.MAX_TARGET = 20
        try:
            C.SEND_INTERVAL = float(self.interval.get())
        except Exception:
            C.SEND_INTERVAL = 5.0
        C.ENABLE_LIVE_CHAT = self.en_live.get()
        C.ENABLE_WEB_PROBE = self.en_probe.get()
        C.ENABLE_SEND = self.en_send.get()

    def on_resolve(self):
        try:
            live_id = self._resolve_to_live_id()
            self.live_url.delete(0, tk.END)
            self.live_url.insert(0, live_id)
            logger.info(f"[解析] 直播间号已识别: {live_id}")
        except Exception as e:
            logger.error(f"[解析] 失败: {e}")
            tk.messagebox.showerror("解析失败", str(e))

    # ---------- 事件 ----------
    def on_start(self):
        try:
            self._ui_to_config()
        except Exception as e:
            tk.messagebox.showerror("无法启动", str(e))
            return
        # 用监测账号 + 发送账号分别启动（账号分离）
        m_env = accounts.monitor_env_path()
        s_env = accounts.sender_env_path()
        logger.info("=== 启动自动私信 ===")
        logger.info(f"监测账号={accounts.monitor_name()} 发送账号={accounts.sender_name()} "
                    f"直播间={C.LIVE_ID} 内容={C.DM_MESSAGE!r} "
                    f"上限={C.MAX_TARGET} 间隔={C.SEND_INTERVAL}s "
                    f"弹幕={C.ENABLE_LIVE_CHAT} 中控台={C.ENABLE_WEB_PROBE} 真发={C.ENABLE_SEND}")
        self.adm = AutoDM(monitor_env_path=m_env, sender_env_path=s_env)
        self.adm.start()
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self._refresh_status()

    def on_stop(self):
        if self.adm:
            self.adm.stop()
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self._refresh_status()

    def on_save(self):
        self._ui_to_config()
        # 写回 config.py 文件，方便命令行模式也生效
        self._write_config_file()
        logger.info("配置已保存到 auto_dm/config.py")

    def _write_config_file(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.py")
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        mapping = {
            "LIVE_ID": repr(C.LIVE_ID),
            "DM_MESSAGE": repr(C.DM_MESSAGE),
            "MAX_TARGET": str(C.MAX_TARGET),
            "SEND_INTERVAL": str(C.SEND_INTERVAL),
            "ENABLE_LIVE_CHAT": str(bool(C.ENABLE_LIVE_CHAT)),
            "ENABLE_WEB_PROBE": str(bool(C.ENABLE_WEB_PROBE)),
            "ENABLE_SEND": str(bool(C.ENABLE_SEND)),
        }
        for i, line in enumerate(lines):
            for key, val in mapping.items():
                if line.strip().startswith(key + " ="):
                    lines[i] = f"{key} = {val}\n"
                    break
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)

    # ---------- 账号管理事件 ----------
    def _refresh_accounts(self):
        try:
            names = [n for n, _ in accounts.list_accounts()]
            self.account_combo["values"] = names
            cur = accounts.current_name()
            if cur in names:
                self.account_var.set(cur)
            elif names:
                self.account_var.set(names[0])

            # 角色下拉（监测/发送）
            self.monitor_combo["values"] = names
            self.sender_combo["values"] = names
            mname = accounts.monitor_name()
            sname = accounts.sender_name()
            if mname in names:
                self.monitor_var.set(mname)
            elif names:
                self.monitor_var.set(names[0])
            if sname in names:
                self.sender_var.set(sname)
            elif names:
                self.sender_var.set(names[0])
            self.bind_hint.set(f"当前：监测= {accounts.monitor_name()} / 发送= {accounts.sender_name()}")

            self._refresh_account_status()
        except Exception as e:
            logger.warning(f"[账号] 刷新列表失败: {e}")

    def on_monitor_select(self, event=None):
        name = self.monitor_var.get()
        try:
            accounts.set_monitor(name)
            self.bind_hint.set(f"当前：监测= {accounts.monitor_name()} / 发送= {accounts.sender_name()}")
            logger.info(f"[账号] 监测账号已设为: {name}（需管理器权限以查看完整昵称）")
        except Exception as e:
            logger.error(f"[账号] 设置监测账号失败: {e}")

    def on_sender_select(self, event=None):
        name = self.sender_var.get()
        try:
            accounts.set_sender(name)
            self.bind_hint.set(f"当前：监测= {accounts.monitor_name()} / 发送= {accounts.sender_name()}")
            logger.info(f"[账号] 发送账号已设为: {name}")
        except Exception as e:
            logger.error(f"[账号] 设置发送账号失败: {e}")

    def _refresh_account_status(self):
        try:
            self.acct_tree.delete(*self.acct_tree.get_children())
            for name, _env in accounts.list_accounts():
                st = accounts.account_status(name)
                self.acct_tree.insert("", tk.END, values=(
                    name,
                    st["label"],
                    "✓" if st.get("has_ticket") else "✗",
                    "✓" if st.get("has_private_key") else "✗",
                    "✓" if st.get("has_cookie") else "✗",
                    st.get("uid", "-"),
                ))
        except Exception as e:
            logger.warning(f"[账号] 刷新状态失败: {e}")
        # 定时轮询（仅当账号页可见时，降低开销）
        if self.notebook.index(self.notebook.select()) == 1:
            self.root.after(8000, self._refresh_account_status)

    def on_account_select(self, event=None):
        name = self.account_var.get()
        try:
            accounts.set_current(name)
            logger.info(f"[账号] 已切换到: {name}")
        except Exception as e:
            logger.error(f"[账号] 切换失败: {e}")
        self._refresh_account_status()

    def on_account_new(self):
        name = simpledialog.askstring("新增账号", "请输入账号名称（如 账号A）:")
        if not name:
            return
        try:
            accounts.add_account(name)
            # 新账号默认同时作为监测与发送账号，刷新后由用户按需改
            accounts.set_monitor(name)
            accounts.set_sender(name)
            logger.info(f"[账号] 已新增: {name}，请点“重新扫码”完成登录（默认设为监测+发送账号）")
        except Exception as e:
            messagebox.showerror("新增失败", str(e))
            return
        self._refresh_accounts()
        self.account_var.set(name)
        accounts.set_current(name)

    def on_account_delete(self):
        name = self.account_var.get()
        if name == accounts.current_name() and self.adm and self.adm.is_running():
            messagebox.showwarning("无法删除", "请先停止当前运行任务再删除该账号。")
            return
        if not messagebox.askyesno("删除确认", f"确定删除账号“{name}”及其凭证？此操作不可恢复。"):
            return
        try:
            accounts.remove_account(name)
            logger.info(f"[账号] 已删除: {name}")
        except Exception as e:
            messagebox.showerror("删除失败", str(e))
            return
        self._refresh_accounts()

    def on_account_rescan(self):
        name = self.account_var.get()
        # 运行中必须先把监听停下来，否则旧 WS 会话残留会导致昵称仍加密、私信仍失败
        if self.adm and self.adm.is_running():
            self.adm.stop()
            logger.info("[账号] 已先停止当前任务，准备重新扫码并重建会话…")
        try:
            accounts.set_current(name)
            logger.info(f"[账号] 为“{name}”强制重新打开浏览器扫码（忽略现有凭证）…")
            # 调 AutoDM.rescan_and_rebuild：扫码 + 第一时间重建监听/刷新签名，
            # 解决“重扫后昵称仍加密、私信仍 INVALID_REQUEST”的卡死问题
            auth = self.adm.rescan_and_rebuild(name)
            if auth and getattr(auth, "cookie", None):
                logger.info(f"[账号] “{name}”扫码成功，凭证已写入并重建会话")
                messagebox.showinfo("扫码成功", f"账号“{name}”登录凭证已就绪，监听已用新会话重建。")
            else:
                logger.error(f"[账号] “{name}”扫码未完成或失败，请重试。")
                messagebox.showwarning("扫码未完成", f"账号“{name}”未拿到有效凭证，请重试。")
        except Exception as e:
            logger.error(f"[账号] 重新扫码异常: {e}")
            messagebox.showerror("扫码异常", str(e))
        self._refresh_account_status()

    def _refresh_status(self):
        if self.adm and self.adm.is_running():
            self.status_var.set(
                f"状态: 运行中 | 已发 {self.adm.sent_count()}/{self.adm.max_target()} | {self.adm.status}")
        elif self.adm:
            self.status_var.set(f"状态: 已停止 | 已发 {self.adm.sent_count()}/{self.adm.max_target()}")
        else:
            self.status_var.set("状态: 未启动")
        self.root.after(500, self._refresh_status)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
