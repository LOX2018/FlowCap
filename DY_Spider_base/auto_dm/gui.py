# -*- coding: utf-8 -*-
"""
抖音直播间评论自动私信 · 控制台 GUI
------------------------------------------------------------------
整体按照 douyin-console.html 定稿原型重做：暗色仪表盘风格 + 模块化 Tab 架构。
模块划分（与原型一致）：
  - Overview   概览：顶部状态条 + 关键指标卡 + 最近活动
  - Crawl      采集监控：直播间链接/房间号解析 + 采集配置（弹幕/中控台/真发）
  - Live Monitor 直播监听：实时统计（列表 / 查阅两视图）
  - Messages   私信消息：多账号私信聚合收件箱 + 回复
  - Accounts   账号管理：账号切换/新增/删除/重扫/守护/角色绑定/密钥状态
  - Tasks      任务中心：私信词库 + 发送策略（上限/间隔/延迟抖动）+ 运行控制 + 统计导出
  - Settings   设置：词库/延迟/守护开关等配置 + 基座功能入口

所有核心功能逻辑（私信分发 DispatchCenter、词库随机抽取、延迟抖动、
账号守护 browser_daemon、接收守护 recv_daemon、统计导出 xlsx、基座功能浏览器）
均完整保留，仅 UI 层按原型重构。
日志仍落盘 logs/run_*.log（界面不再内嵌日志文本框，见记忆 32074691）。
"""

import os
import sys
import time
import json
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter.ttk import Notebook
import tkinter.font as tkfont

import loguru
from loguru import logger

import auto_dm.config as C
import urllib.parse
from auto_dm import accounts
from auto_dm import browser_daemon
from auto_dm import run as dm_run
from utils import data_util

A = dm_run.AutoDM
auto_dm_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(auto_dm_root))


# --------------------------------------------------------------------------
# 暗色主题样式（复刻原型的深蓝/青色仪表盘调性）
# --------------------------------------------------------------------------
BG = "#0f1320"
PANEL = "#171c2e"
PANEL2 = "#1d2438"
BORDER = "#2a3350"
TEXT = "#e6ebf5"
MUTED = "#8794b3"
ACCENT = "#36c2cf"      # 青色
ACCENT2 = "#5b8def"     # 蓝
GREEN = "#3ecf8e"
YELLOW = "#f5c451"
RED = "#f2607d"
CHIP = "#222a42"


def setup_style():
    s = ttk.Style()
    try:
        s.theme_use("clam")
    except Exception:
        pass
    s.configure(".", background=BG, foreground=TEXT, font=("Microsoft YaHei", 10))
    s.configure("TFrame", background=BG)
    s.configure("Panel.TFrame", background=PANEL, relief="flat")
    s.configure("TLabel", background=BG, foreground=TEXT)
    s.configure("Muted.TLabel", background=BG, foreground=MUTED)
    s.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Microsoft YaHei", 16, "bold"))
    s.configure("Sub.TLabel", background=BG, foreground=MUTED, font=("Microsoft YaHei", 10))
    s.configure("CardTitle.TLabel", background=PANEL, foreground=MUTED, font=("Microsoft YaHei", 10))
    s.configure("CardVal.TLabel", background=PANEL, foreground=TEXT, font=("Microsoft YaHei", 22, "bold"))
    s.configure("TNotebook", background=BG, borderwidth=0)
    s.configure("TNotebook.Tab", background=PANEL2, foreground=MUTED, padding=[14, 8],
                font=("Microsoft YaHei", 11, "bold"))
    s.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", ACCENT)])
    s.configure("TButton", background=PANEL2, foreground=TEXT, borderwidth=0,
                padding=[12, 6], font=("Microsoft YaHei", 10, "bold"))
    s.map("TButton", background=[("active", "#2b3552"), ("disabled", "#1a2030")],
          foreground=[("disabled", "#56607e")])
    s.configure("Accent.TButton", background=ACCENT, foreground="#06121a")
    s.map("Accent.TButton", background=[("active", "#4fd6e2")])
    s.configure("Green.TButton", background=GREEN, foreground="#06231a")
    s.configure("Danger.TButton", background=RED, foreground="#2a0a14")
    s.configure("TEntry", fieldbackground=PANEL2, foreground=TEXT, borderwidth=1,
                insertcolor=TEXT, padding=[8, 5])
    s.configure("TCombobox", fieldbackground=PANEL2, foreground=TEXT, padding=[6, 4])
    s.configure("TCheckbutton", background=BG, foreground=TEXT)
    s.configure("TSpinbox", fieldbackground=PANEL2, foreground=TEXT, padding=[6, 4])
    s.configure("TLabelFrame", background=PANEL, foreground=MUTED, borderwidth=1,
                relief="groove", padding=[10, 8])
    s.configure("TLabelFrame.Label", background=BG, foreground=ACCENT,
                font=("Microsoft YaHei", 11, "bold"))
    s.configure("Treeview", background=PANEL, foreground=TEXT, fieldbackground=PANEL,
                borderwidth=0, rowheight=26, font=("Microsoft YaHei", 10))
    s.configure("Treeview.Heading", background=PANEL2, foreground=MUTED,
                font=("Microsoft YaHei", 10, "bold"))
    s.map("Treeview", background=[("selected", ACCENT2)], foreground=[("selected", "#06121a")])
    s.configure("TSeparator", background=BORDER)
    s.configure("Horizontal.TScrollbar", background=PANEL2, troughcolor=BG)
    s.configure("Vertical.TScrollbar", background=PANEL2, troughcolor=BG)
    return s


# 让原生 Scrollbar 也暗色（ttk scrollbar 在部分平台不跟 theme）
def dark_scrollbar(parent):
    return tk.Scrollbar(parent, bg=PANEL2, troughcolor=BG, bd=0,
                        activebackground=ACCENT2, width=10)


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def pct_color(val):
    if val >= 80:
        return RED
    if val >= 50:
        return YELLOW
    return GREEN


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("抖音直播间自动私信 · 控制台")
        self.geometry("1180x760")
        self.configure(bg=BG)
        self.minsize(1000, 680)
        try:
            self.iconbitmap("")  # 占位，避免默认图标
        except Exception:
            pass

        self.adm = None
        self.dm_rows = []          # [(BooleanVar, Entry)] 词库行
        self.stats_mode = "list"
        self.dm_conv_nb = None
        self.dm_conv_tabs = {}     # conv_id -> frame
        self.stats_cols_list = ["发言人", "评论内容", "私信情况", "私信文案", "私信时间", "发言时间"]
        self.stats_cols_view = ["发言人", "发言次数", "评论内容", "私信情况", "私信文案", "私信时间", "发言时间"]
        self._dm_cache = {}        # account -> {conv_id: {...}}
        self._refresh_after_id = None
        self._last_status_text = ""
        self._status_spin = 0

        self._build_topbar()
        self._build_notebook()
        self._apply_dark_widgets()
        self._load_config_to_ui()

        # 退出处理
        self.protocol("WM_DELETE_WINDOW", self._on_exit)
        # 启动轮询
        self._poll()

    # ------------------------------------------------------------------ #
    # 顶部状态条
    # ------------------------------------------------------------------ #
    def _build_topbar(self):
        bar = tk.Frame(self, bg=PANEL, height=56)
        bar.pack(side="top", fill="x")
        bar.pack_propagate(False)

        # logo / 标题
        tk.Label(bar, text="◆ 抖音自动私信控制台", bg=PANEL, fg=ACCENT,
                 font=("Microsoft YaHei", 14, "bold")).pack(side="left", padx=18)

        # 状态指示
        self.status_dot = tk.Canvas(bar, width=12, height=12, bg=PANEL, highlightthickness=0)
        self.status_dot.pack(side="left", padx=(0, 6))
        self.status_dot.create_oval(1, 1, 11, 11, fill=RED, tags="dot")
        self.status_label = tk.Label(bar, text="未启动", bg=PANEL, fg=MUTED,
                                     font=("Microsoft YaHei", 11, "bold"))
        self.status_label.pack(side="left")

        # 关键指标（右侧）
        self.top_sent = tk.Label(bar, text="已发送 0", bg=PANEL, fg=TEXT,
                                 font=("Microsoft YaHei", 11, "bold"))
        self.top_sent.pack(side="right", padx=18)
        self.top_limit = tk.Label(bar, text="上限 0", bg=PANEL, fg=MUTED,
                                  font=("Microsoft YaHei", 11))
        self.top_limit.pack(side="right", padx=4)
        tk.Label(bar, text="|", bg=PANEL, fg=BORDER).pack(side="right", padx=4)
        self.top_queue = tk.Label(bar, text="待发 0", bg=PANEL, fg=MUTED,
                                  font=("Microsoft YaHei", 11))
        self.top_queue.pack(side="right", padx=4)

    def _set_dot(self, color):
        self.status_dot.itemconfig("dot", fill=color)

    # ------------------------------------------------------------------ #
    # Notebook 七个模块
    # ------------------------------------------------------------------ #
    def _build_notebook(self):
        nb = Notebook(self)
        nb.pack(side="top", fill="both", expand=True, padx=10, pady=8)
        self.notebook = nb

        self.overview_frame = self._tab(nb, "概览")
        self.crawl_frame = self._tab(nb, "采集监控")
        self.live_frame = self._tab(nb, "直播监听")
        self.inbox_frame = self._tab(nb, "私信消息")
        self.account_frame = self._tab(nb, "账号管理")
        self.task_frame = self._tab(nb, "任务中心")
        self.tool_frame = self._tab(nb, "设置")

        self._build_overview(self.overview_frame)
        self._build_crawl(self.crawl_frame)
        self._build_live(self.live_frame)
        self._build_inbox(self.inbox_frame)
        self._build_account(self.account_frame)
        self._build_task(self.task_frame)
        self._build_tool(self.tool_frame)

    def _tab(self, nb, title):
        f = tk.Frame(nb, bg=BG)
        nb.add(f, text=title)
        return f

    # ------------------------------------------------------------------ #
    # 卡片 / 通用组件
    # ------------------------------------------------------------------ #
    def _card(self, parent, w=200, h=92):
        c = tk.Frame(parent, bg=PANEL, bd=1, relief="solid", highlightbackground=BORDER)
        c.configure(width=w, height=h)
        c.pack_propagate(False)
        return c

    def _labeled_entry(self, parent, label, var=None, width=22, **kw):
        f = tk.Frame(parent, bg=BG)
        tk.Label(f, text=label, bg=BG, fg=MUTED, font=("Microsoft YaHei", 9)).pack(anchor="w", pady=(0, 3))
        e = ttk.Entry(f, textvariable=var, width=width, **kw)
        e.pack(fill="x")
        return f, e

    def _apply_dark_widgets(self):
        # 给所有 Text/Canvas 等原生控件补暗色
        self.option_add("*Text.background", PANEL2)
        self.option_add("*Text.foreground", TEXT)
        self.option_add("*Text.insertBackground", TEXT)
        self.option_add("*Text.selectBackground", ACCENT2)
        self.option_add("*Text.borderWidth", 0)
        self.option_add("*Canvas.background", BG)

    # ================================================================== #
    # Overview 概览
    # ================================================================== #
    def _build_overview(self, parent):
        parent.configure(bg=BG)
        # 指标卡行
        cards = tk.Frame(parent, bg=BG)
        cards.pack(fill="x", padx=14, pady=14)

        self.ov_card = {}
        specs = [
            ("已发送", "0", "私信消息"),
            ("发送上限", "0", "本次任务"),
            ("待发送", "0", "延迟队列"),
            ("捕获评论", "0", "本次监听"),
            ("账号", "—", "当前登录"),
            ("守护进程", "—", "浏览器保活"),
        ]
        for i, (title, val, sub) in enumerate(specs):
            c = self._card(cards, w=170, h=96)
            c.grid(row=0, column=i, padx=6, sticky="ew")
            cards.columnconfigure(i, weight=1)
            tk.Label(c, text=title, bg=PANEL, fg=MUTED, font=("Microsoft YaHei", 10)).pack(anchor="w", padx=12, pady=(10, 0))
            v = tk.Label(c, text=val, bg=PANEL, fg=TEXT, font=("Microsoft YaHei", 24, "bold"))
            v.pack(anchor="w", padx=12, pady=(2, 0))
            tk.Label(c, text=sub, bg=PANEL, fg=MUTED, font=("Microsoft YaHei", 9)).pack(anchor="w", padx=12)
            self.ov_card[title] = v

        # 当前状态摘要 + 快捷操作
        mid = tk.Frame(parent, bg=BG)
        mid.pack(fill="x", padx=14, pady=(4, 0))

        info = tk.LabelFrame(mid, text="运行状态", bg=PANEL, fg=ACCENT)
        info.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=6)
        self.ov_status = tk.Text(info, height=6, bg=PANEL2, fg=TEXT, relief="flat",
                                 font=("Microsoft YaHei", 10), wrap="word", state="disabled")
        self.ov_status.pack(fill="both", expand=True, padx=8, pady=8)

        quick = tk.LabelFrame(mid, text="快捷操作", bg=PANEL, fg=ACCENT, width=240)
        quick.pack(side="right", fill="y", padx=(8, 0), pady=6)
        quick.pack_propagate(False)
        ttk.Button(quick, text="▶ 启动任务", style="Accent.TButton", command=self.on_start).pack(fill="x", padx=10, pady=6)
        ttk.Button(quick, text="⏸ 暂停 / ▶ 继续", command=self.on_pause_toggle).pack(fill="x", padx=10, pady=6)
        ttk.Button(quick, text="■ 停止", style="Danger.TButton", command=self.on_stop).pack(fill="x", padx=10, pady=6)
        ttk.Button(quick, text="🌐 基座功能", command=self.on_open_features).pack(fill="x", padx=10, pady=6)
        ttk.Button(quick, text="🚀 启动守护", command=self.on_daemon_toggle).pack(fill="x", padx=10, pady=6)

        # 最近活动
        act = tk.LabelFrame(parent, text="最近活动 / 捕获记录", bg=PANEL, fg=ACCENT)
        act.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.ov_recent = ttk.Treeview(act, columns=self.stats_cols_list, show="headings", height=8)
        for col in self.stats_cols_list:
            self.ov_recent.heading(col, text=col)
            self.ov_recent.column(col, width=170 if col in ("评论内容", "私信文案") else 100)
        self.ov_recent.pack(fill="both", expand=True, padx=8, pady=8)

    # ================================================================== #
    # Crawl 采集监控
    # ================================================================== #
    def _build_crawl(self, parent):
        parent.configure(bg=BG)
        # 直播间链接
        lf = tk.LabelFrame(parent, text="直播间", bg=PANEL, fg=ACCENT)
        lf.pack(fill="x", padx=14, pady=12)
        row = tk.Frame(lf, bg=PANEL)
        row.pack(fill="x", padx=10, pady=10)
        tk.Label(row, text="直播间链接", bg=PANEL, fg=MUTED).pack(side="left")
        self.live_url = tk.StringVar(value=C.LIVE_URL or "")
        e = ttk.Entry(row, textvariable=self.live_url, width=60)
        e.pack(side="left", padx=8, fill="x", expand=True)
        ttk.Button(row, text="解析房间号", command=self.on_resolve).pack(side="left")
        self.resolve_label = tk.Label(row, text=f"房间号：{C.LIVE_ID or '（未解析）'}", bg=PANEL, fg=ACCENT)
        self.resolve_label.pack(side="left", padx=10)

        # 采集配置
        cflf = tk.LabelFrame(parent, text="采集配置", bg=PANEL, fg=ACCENT)
        cflf.pack(fill="x", padx=14, pady=(0, 12))
        cf = tk.Frame(cflf, bg=PANEL)
        cf.pack(fill="x", padx=10, pady=10)

        self.enable_danmaku = tk.BooleanVar(value=C.ENABLE_DANMAKU)
        self.enable_console = tk.BooleanVar(value=C.ENABLE_CONSOLE)
        self.enable_send = tk.BooleanVar(value=C.ENABLE_SEND)
        ttk.Checkbutton(cf, text="启用弹幕监听", variable=self.enable_danmaku).grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Checkbutton(cf, text="启用中控台采集", variable=self.enable_console).grid(row=0, column=1, sticky="w", padx=8, pady=4)
        ttk.Checkbutton(cf, text="启用真实发送私信", variable=self.enable_send).grid(row=0, column=2, sticky="w", padx=8, pady=4)

        tk.Label(cf, text="采集说明：弹幕监听经直播间 WebSocket 捕获评论/弹幕昵称；中控台采集经浏览器 web 探针捕获评论区发言人。两者去重合并，避免同一人重复私信（见 core.py 跨来源去重）。",
                 bg=PANEL, fg=MUTED, wraplength=900, justify="left", font=("Microsoft YaHei", 9)).grid(
            row=1, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 0))

        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=14, pady=4)
        hint = tk.Label(parent, text="提示：配置完成后切换到「任务中心」可设置私信词库与发送策略，再点「启动任务」开始运行。",
                        bg=BG, fg=MUTED, font=("Microsoft YaHei", 9))
        hint.pack(anchor="w", padx=16, pady=4)

    # ================================================================== #
    # Live Monitor 直播监听（实时统计）
    # ================================================================== #
    def _build_live(self, parent):
        parent.configure(bg=BG)
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", padx=14, pady=10)
        self.btn_stats_mode = ttk.Button(bar, text="进入查阅模式", command=self._toggle_stats_mode)
        self.btn_stats_mode.pack(side="left")
        ttk.Button(bar, text="清空显示", command=self._clear_stats_view).pack(side="left", padx=6)
        self.stats_var = tk.StringVar(value="记录 0 · 已发送 0")
        tk.Label(bar, textvariable=self.stats_var, bg=BG, fg=MUTED).pack(side="right")

        self.stats_lf = tk.LabelFrame(parent, text="实时统计", bg=PANEL, fg=ACCENT)
        self.stats_lf.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self._build_stats_tree()

    def _build_stats_tree(self):
        for w in self.stats_lf.winfo_children():
            w.destroy()
        cols = self.stats_cols_view if self.stats_mode == "view" else self.stats_cols_list
        self.stats_tree = ttk.Treeview(self.stats_lf, columns=cols, show="headings", height=18)
        for col in cols:
            self.stats_tree.heading(col, text=col)
            if col in ("评论内容", "私信文案"):
                self.stats_tree.column(col, width=280)
            elif col == "发言人":
                self.stats_tree.column(col, width=140)
            else:
                self.stats_tree.column(col, width=110)
        self.stats_tree.pack(fill="both", expand=True, padx=8, pady=8)

    def _toggle_stats_mode(self):
        self.stats_mode = "view" if self.stats_mode == "list" else "list"
        self.btn_stats_mode.configure(text="进入查阅模式" if self.stats_mode == "list" else "返回列表模式")
        self._build_stats_tree()

    def _clear_stats_view(self):
        if hasattr(self, "stats_tree"):
            for it in self.stats_tree.get_children():
                self.stats_tree.delete(it)

    # ================================================================== #
    # Messages 私信消息（接收聚合 + 回复）
    # ================================================================== #
    def _build_inbox(self, parent):
        parent.configure(bg=BG)
        # 守护控制条
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", padx=14, pady=10)
        self.btn_recv_daemon = ttk.Button(bar, text="▶ 启动接收守护", command=self.on_recv_daemon_toggle)
        self.btn_recv_daemon.pack(side="left")
        self.recv_status_var = tk.StringVar(value="接收守护：未运行")
        tk.Label(bar, textvariable=self.recv_status_var, bg=BG, fg=MUTED).pack(side="left", padx=10)

        # 左：账户列表  右：会话 Tabs
        body = tk.Frame(parent, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        left = tk.LabelFrame(body, text="账号", bg=PANEL, fg=ACCENT, width=200)
        left.pack(side="left", fill="y", padx=(0, 8))
        left.pack_propagate(False)
        self.dm_account_tree = ttk.Treeview(left, columns=["acc", "unread"], show="headings", height=20)
        self.dm_account_tree.heading("acc", text="账号")
        self.dm_account_tree.column("acc", width=120)
        self.dm_account_tree.heading("unread", text="未读")
        self.dm_account_tree.column("unread", width=50)
        self.dm_account_tree.pack(fill="both", expand=True, padx=6, pady=6)
        self.dm_account_tree.bind("<<TreeviewSelect>>", self._on_dm_account_select)

        right = tk.Frame(body, bg=BG)
        right.pack(side="right", fill="both", expand=True)
        self.dm_conv_nb = ttk.Notebook(right)
        self.dm_conv_nb.pack(fill="both", expand=True)
        self.dm_conv_tabs = {}

        replybar = tk.Frame(right, bg=BG)
        replybar.pack(fill="x", pady=(6, 0))
        self.dm_reply = tk.Text(replybar, height=3, bg=PANEL2, fg=TEXT, relief="flat",
                                font=("Microsoft YaHei", 10), wrap="word", insertbackground=TEXT)
        self.dm_reply.pack(side="left", fill="x", expand=True)
        ttk.Button(replybar, text="发送回复", command=self.on_dm_send).pack(side="left", padx=6)

        # 默认会话占位
        self._dm_add_conv_tab("（选择左侧账号查看会话）", None)

    # ================================================================== #
    # Accounts 账号管理
    # ================================================================== #
    def _build_account(self, parent):
        parent.configure(bg=BG)
        # 账号列表
        lf = tk.LabelFrame(parent, text="账号列表", bg=PANEL, fg=ACCENT)
        lf.pack(fill="x", padx=14, pady=12)
        row = tk.Frame(lf, bg=PANEL)
        row.pack(fill="x", padx=10, pady=8)
        self.account_var = tk.StringVar(value=accounts.current_name() or "")
        self.account_combo = ttk.Combobox(row, textvariable=self.account_var, state="readonly", width=18)
        self.account_combo.pack(side="left", padx=6)
        self.account_combo.bind("<<ComboboxSelected>>", lambda e: self.on_account_select())
        ttk.Button(row, text="切换当前", command=self.on_account_select).pack(side="left", padx=4)
        ttk.Button(row, text="新增账号", command=self.on_account_add).pack(side="left", padx=4)
        ttk.Button(row, text="删除账号", command=self.on_account_delete).pack(side="left", padx=4)
        ttk.Button(row, text="重新扫码", command=self.on_account_rescan).pack(side="left", padx=4)
        ttk.Button(row, text="启动守护", command=self.on_daemon_toggle).pack(side="left", padx=4)

        # 角色绑定
        rlf = tk.LabelFrame(parent, text="角色绑定", bg=PANEL, fg=ACCENT)
        rlf.pack(fill="x", padx=14, pady=(0, 12))
        rrow = tk.Frame(rlf, bg=PANEL)
        rrow.pack(fill="x", padx=10, pady=8)
        tk.Label(rrow, text="监测账号（听弹幕）", bg=PANEL, fg=MUTED).pack(side="left")
        self.monitor_var = tk.StringVar(value=accounts.monitor_name() or "")
        self.monitor_combo = ttk.Combobox(rrow, textvariable=self.monitor_var, state="readonly", width=16)
        self.monitor_combo.pack(side="left", padx=6)
        ttk.Button(rrow, text="设为监测", command=self.on_set_monitor).pack(side="left", padx=4)

        tk.Label(rrow, text="发送账号（发私信）", bg=PANEL, fg=MUTED).pack(side="left", padx=(18, 0))
        self.sender_var = tk.StringVar(value=accounts.sender_name() or "")
        self.sender_combo = ttk.Combobox(rrow, textvariable=self.sender_var, state="readonly", width=16)
        self.sender_combo.pack(side="left", padx=6)
        ttk.Button(rrow, text="设为发送", command=self.on_set_sender).pack(side="left", padx=4)

        # 密钥状态
        klf = tk.LabelFrame(parent, text="密钥 / 凭证状态", bg=PANEL, fg=ACCENT)
        klf.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.account_status_tree = ttk.Treeview(klf, columns=["账号", "凭证状态", "web_protect", "keys", "ticket", "ts_sign"], show="headings", height=8)
        for col in ["账号", "凭证状态", "web_protect", "keys", "ticket", "ts_sign"]:
            self.account_status_tree.heading(col, text=col)
            self.account_status_tree.column(col, width=140 if col == "账号" else 130)
        self.account_status_tree.pack(fill="both", expand=True, padx=8, pady=8)

        self._refresh_account_combos()

    # ================================================================== #
    # Tasks 任务中心
    # ================================================================== #
    def _build_task(self, parent):
        parent.configure(bg=BG)
        # 私信词库
        wlf = tk.LabelFrame(parent, text="私信词库（每行一条，发送时随机抽取；勾选框控制是否启用）", bg=PANEL, fg=ACCENT)
        wlf.pack(fill="x", padx=14, pady=12)
        tool = tk.Frame(wlf, bg=PANEL)
        tool.pack(fill="x", padx=8, pady=(4, 6))
        ttk.Button(tool, text="全选", command=self._dm_select_all).pack(side="left", padx=3)
        ttk.Button(tool, text="全不选", command=self._dm_select_none).pack(side="left", padx=3)
        ttk.Button(tool, text="添加一条", command=lambda: self._dm_add_row(True, "")).pack(side="left", padx=3)
        ttk.Button(tool, text="删除选中", command=self._dm_del_checked).pack(side="left", padx=3)

        self.dm_list_frame = tk.Frame(wlf, bg=PANEL)
        self.dm_list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # 发送策略
        slf = tk.LabelFrame(parent, text="发送策略", bg=PANEL, fg=ACCENT)
        slf.pack(fill="x", padx=14, pady=(0, 12))

        r1 = tk.Frame(slf, bg=PANEL)
        r1.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(r1, text="发送上限", bg=PANEL, fg=MUTED).pack(side="left")
        self.max_target = tk.IntVar(value=C.MAX_TARGET)
        ttk.Spinbox(r1, from_=1, to=9999, textvariable=self.max_target, width=8,
                    command=self.on_max_target_change).pack(side="left", padx=6)
        tk.Label(r1, text="发送间隔(秒)", bg=PANEL, fg=MUTED).pack(side="left", padx=(18, 0))
        self.interval = tk.IntVar(value=C.SEND_INTERVAL)
        ttk.Spinbox(r1, from_=1, to=3600, textvariable=self.interval, width=8).pack(side="left", padx=6)

        r2 = tk.Frame(slf, bg=PANEL)
        r2.pack(fill="x", padx=10, pady=(2, 10))
        tk.Label(r2, text="延迟抖动(秒)", bg=PANEL, fg=MUTED).pack(side="left")
        self.delay = tk.StringVar(value=self._delay_repr())
        ttk.Entry(r2, textvariable=self.delay, width=14).pack(side="left", padx=6)
        tk.Label(r2, text="格式：50,120=随机区间；60=固定延迟", bg=PANEL, fg=MUTED,
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=6)

        # 运行控制
        ctrl = tk.Frame(parent, bg=BG)
        ctrl.pack(fill="x", padx=14, pady=(0, 8))
        self.btn_start = ttk.Button(ctrl, text="▶ 启动任务", style="Accent.TButton", command=self.on_start)
        self.btn_start.pack(side="left", padx=6)
        self.btn_pause = ttk.Button(ctrl, text="⏸ 暂停", command=self.on_pause_toggle, state="disabled")
        self.btn_pause.pack(side="left", padx=6)
        self.btn_stop = ttk.Button(ctrl, text="■ 停止", style="Danger.TButton", command=self.on_stop)
        self.btn_stop.pack(side="left", padx=6)
        ttk.Button(ctrl, text="💾 保存配置", command=self.on_save).pack(side="left", padx=6)
        self.btn_save_stats = ttk.Button(ctrl, text="💾 保存统计", command=self._save_stats_manual)
        self.btn_save_stats.pack(side="left", padx=6)
        self.save_ind = tk.Label(ctrl, text="", bg=BG, fg=GREEN)
        self.save_ind.pack(side="left", padx=10)

    # ================================================================== #
    # Settings 设置
    # ================================================================== #
    def _build_tool(self, parent):
        parent.configure(bg=BG)
        lf = tk.LabelFrame(parent, text="模块开关与配置", bg=PANEL, fg=ACCENT)
        lf.pack(fill="x", padx=14, pady=12)
        row = tk.Frame(lf, bg=PANEL)
        row.pack(fill="x", padx=10, pady=8)
        ttk.Checkbutton(row, text="启用弹幕监听", variable=self.enable_danmaku).grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Checkbutton(row, text="启用中控台采集", variable=self.enable_console).grid(row=0, column=1, sticky="w", padx=8, pady=4)
        ttk.Checkbutton(row, text="启用真实发送", variable=self.enable_send).grid(row=0, column=2, sticky="w", padx=8, pady=4)
        ttk.Checkbutton(row, text="启动即强制重新扫码", variable=tk.BooleanVar(value=getattr(C, "FORCE_RESCAN_ON_START", True))).grid(row=1, column=0, sticky="w", padx=8, pady=4)

        blf = tk.LabelFrame(parent, text="功能入口", bg=PANEL, fg=ACCENT)
        blf.pack(fill="x", padx=14, pady=(0, 12))
        brow = tk.Frame(blf, bg=PANEL)
        brow.pack(fill="x", padx=10, pady=8)
        ttk.Button(brow, text="🌐 打开基座功能浏览器", command=self.on_open_features).pack(side="left", padx=6)
        ttk.Button(brow, text=" 🔗 链接解析跳到采集页", command=lambda: self.notebook.select(self.crawl_frame)).pack(side="left", padx=6)
        ttk.Button(brow, text="📂 打开日志目录", command=self._open_logs).pack(side="left", padx=6)
        ttk.Button(brow, text="🚀 启动浏览器守护", command=self.on_daemon_toggle).pack(side="left", padx=6)
        ttk.Button(brow, text="📥 启动接收守护", command=self.on_recv_daemon_toggle).pack(side="left", padx=6)

        wlf = tk.LabelFrame(parent, text="工作流建议", bg=PANEL, fg=ACCENT)
        wlf.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        tk.Label(wlf,
                 text=("推荐顺序：\n"
                       "1. 账号管理 → 选择/新增账号并扫码（或用「启动守护」常驻保活凭证）\n"
                       "2. 采集监控 → 填入直播间链接并「解析房间号」\n"
                       "3. 任务中心 → 编辑私信词库、设定发送上限/间隔/延迟抖动\n"
                       "4. 点「启动任务」；运行中可暂停/调速/保存统计\n"
                       "5. 私信消息 → 查看/回复收到的私信\n"
                       "所有运行日志落盘于 logs/run_*.log，可事后回看。"),
                 bg=PANEL, fg=MUTED, justify="left", font=("Microsoft YaHei", 10),
                 wraplength=900, anchor="nw").pack(anchor="nw", padx=12, pady=12)

    # ================================================================== #
    # 词库行管理
    # ================================================================== #
    def _dm_clear_rows(self):
        for var, ent in self.dm_rows:
            ent.master.destroy()
        self.dm_rows = []

    def _dm_add_row(self, enabled=True, text=""):
        f = tk.Frame(self.dm_list_frame, bg=PANEL)
        f.pack(fill="x", pady=2)
        var = tk.BooleanVar(value=enabled)
        cb = ttk.Checkbutton(f, variable=var, width=3)
        cb.pack(side="left")
        ent = ttk.Entry(f, width=80)
        ent.insert(0, text)
        ent.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.dm_rows.append((var, ent))

    def _dm_select_all(self):
        for var, _ in self.dm_rows:
            var.set(True)

    def _dm_select_none(self):
        for var, _ in self.dm_rows:
            var.set(False)

    def _dm_del_checked(self):
        new_rows = []
        for var, ent in self.dm_rows:
            if var.get():
                ent.master.destroy()
            else:
                new_rows.append((var, ent))
        self.dm_rows = new_rows
        if not self.dm_rows:
            self._dm_add_row(True, "")

    # ================================================================== #
    # 配置：UI <-> config
    # ================================================================== #
    def _delay_repr(self):
        d = getattr(C, "SEND_DELAY_SEC", 60)
        if isinstance(d, (tuple, list)) and len(d) == 2:
            return f"{d[0]},{d[1]}"
        return str(d)

    def _parse_delay(self, raw):
        raw = (raw or "").strip()
        if not raw:
            return 0
        for sep in (",", "-", "~"):
            if sep in raw:
                try:
                    lo, hi = raw.split(sep)
                    lo, hi = float(lo), float(hi)
                    if lo > hi:
                        lo, hi = hi, lo
                    return (int(lo), int(hi))
                except Exception:
                    return 0
        try:
            return max(0.0, float(raw))
        except Exception:
            return 0

    def _load_config_to_ui(self):
        # 直播间
        if hasattr(self, "live_url"):
            self.live_url.set(C.LIVE_URL or "")
        if hasattr(self, "resolve_label"):
            self.resolve_label.configure(text=f"房间号：{C.LIVE_ID or '（未解析）'}")
        # 开关
        if hasattr(self, "enable_danmaku"):
            self.enable_danmaku.set(C.ENABLE_DANMAKU)
        if hasattr(self, "enable_console"):
            self.enable_console.set(C.ENABLE_CONSOLE)
        if hasattr(self, "enable_send"):
            self.enable_send.set(C.ENABLE_SEND)
        # 词库
        if hasattr(self, "dm_list_frame"):
            self._dm_clear_rows()
            pool = getattr(C, "DM_MESSAGE_POOL", [C.DM_MESSAGE]) if hasattr(C, "DM_MESSAGE_POOL") else [C.DM_MESSAGE]
            enabled = getattr(C, "DM_MESSAGE_ENABLED", None)
            if enabled is None:
                enabled = [True] * len(pool)
            for i, msg in enumerate(pool):
                en = enabled[i] if i < len(enabled) else True
                self._dm_add_row(en, msg)
        # 策略
        if hasattr(self, "max_target"):
            self.max_target.set(C.MAX_TARGET)
        if hasattr(self, "interval"):
            self.interval.set(C.SEND_INTERVAL)
        if hasattr(self, "delay"):
            self.delay.set(self._delay_repr())

    def _ui_to_config(self):
        # 采集开关
        C.ENABLE_DANMAKU = self.enable_danmaku.get()
        C.ENABLE_CONSOLE = self.enable_console.get()
        C.ENABLE_SEND = self.enable_send.get()
        # 直播间
        C.LIVE_URL = self.live_url.get().strip() if hasattr(self, "live_url") else C.LIVE_URL
        # 词库
        pool, enabled = [], []
        for var, ent in self.dm_rows:
            t = ent.get().strip()
            if t:
                pool.append(t)
                enabled.append(bool(var.get()))
        if pool:
            C.DM_MESSAGE_POOL = pool
            C.DM_MESSAGE_ENABLED = enabled
            C.DM_MESSAGE = pool[0]
        elif hasattr(C, "DM_MESSAGE"):
            C.DM_MESSAGE_POOL = [C.DM_MESSAGE]
            C.DM_MESSAGE_ENABLED = [True]
        # 策略
        C.MAX_TARGET = int(self.max_target.get())
        C.SEND_INTERVAL = int(self.interval.get())
        C.SEND_DELAY_SEC = self._parse_delay(self.delay.get() if hasattr(self, "delay") else "0")
        # 账号
        if hasattr(self, "account_var"):
            sel = self.account_var.get()
            if sel:
                accounts.set_current(sel)

    def _write_config_file(self):
        """把当前配置写回 config.py，保证命令行模式也能生效。"""
        mapping = {
            "LIVE_URL": repr(C.LIVE_URL),
            "ENABLE_DANMAKU": repr(C.ENABLE_DANMAKU),
            "ENABLE_CONSOLE": repr(C.ENABLE_CONSOLE),
            "ENABLE_SEND": repr(C.ENABLE_SEND),
            "MAX_TARGET": repr(C.MAX_TARGET),
            "SEND_INTERVAL": repr(C.SEND_INTERVAL),
            "SEND_DELAY_SEC": repr(C.SEND_DELAY_SEC),
            "DM_MESSAGE": repr(C.DM_MESSAGE),
            "DM_MESSAGE_POOL": repr(C.DM_MESSAGE_POOL),
            "DM_MESSAGE_ENABLED": repr(C.DM_MESSAGE_ENABLED),
            "FORCE_RESCAN_ON_START": repr(getattr(C, "FORCE_RESCAN_ON_START", True)),
        }
        path = os.path.join(auto_dm_root, "auto_dm", "config.py")
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            return
        out = []
        seen = set()
        for line in lines:
            matched = False
            for k, v in mapping.items():
                if line.strip().startswith(k + " =") or line.strip().startswith(k + "="):
                    out.append(f"{k} = {v}\n")
                    seen.add(k)
                    matched = True
                    break
            if not matched:
                out.append(line)
        for k in mapping:
            if k not in seen:
                out.append(f"\n{k} = {mapping[k]}\n")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(out)
        except Exception as e:
            logger.warning(f"写回 config.py 失败：{e}")

    # ================================================================== #
    # 运行控制
    # ================================================================== #
    def on_start(self):
        self._ui_to_config()
        running = self.adm and self.adm.is_running()
        if not running:
            self.adm = A()
        try:
            self.adm.start()
        except Exception as e:
            messagebox.showerror("启动失败", str(e))
            return
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.btn_pause.configure(state="normal")
        self.btn_pause.configure(text="⏸ 暂停")
        logger.info(
            f"已启动：监测={accounts.monitor_name()} 发送={accounts.sender_name()} "
            f"上限={C.MAX_TARGET} 间隔={C.SEND_INTERVAL}s 延迟抖动={C.SEND_DELAY_SEC} "
            f"词库条数={len(getattr(C,'DM_MESSAGE_POOL',[]))} 弹幕={C.ENABLE_DANMAKU} 中控台={C.ENABLE_CONSOLE} 真发={C.ENABLE_SEND}")

    def on_stop(self):
        if self.adm:
            self.adm.stop()
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.btn_pause.configure(state="disabled")
        self.btn_pause.configure(text="⏸ 暂停")

    def on_pause_toggle(self):
        if not self.adm:
            return
        if self.adm.dispatch and getattr(self.adm.dispatch, "paused", False):
            self.adm.resume()
            self.btn_pause.configure(text="⏸ 暂停")
        else:
            self.adm.pause()
            self.btn_pause.configure(text="▶ 继续")

    def on_save(self):
        self._ui_to_config()
        self._write_config_file()
        self.save_ind.configure(text="✓ 已保存")
        self.after(2000, lambda: self.save_ind.configure(text=""))

    def on_max_target_change(self):
        try:
            n = int(self.max_target.get())
        except Exception:
            return
        C.MAX_TARGET = n
        if self.adm and self.adm.is_running():
            self.adm.set_max_target(n)

    def on_resolve(self):
        url = self.live_url.get().strip()
        if not url:
            messagebox.showwarning("提示", "请先填入直播间链接")
            return
        try:
            live_id = self._resolve_to_live_id(url)
        except Exception as e:
            messagebox.showerror("解析失败", str(e))
            return
        if live_id:
            C.LIVE_ID = live_id
            self.resolve_label.configure(text=f"房间号：{live_id}")
            logger.info(f"解析房间号成功：{live_id}")
        else:
            messagebox.showerror("解析失败", "未能从链接中提取房间号")

    def _resolve_to_live_id(self, url):
        """从直播间链接解析 room_id（复用 link_resolve 的页面抓取）。"""
        from auto_dm.link_resolve import resolve_live_id
        return resolve_live_id(url)

    # ================================================================== #
    # 账号管理
    # ================================================================== #
    def _refresh_account_combos(self):
        names = accounts.list_accounts()
        cur = accounts.current_name()
        self.account_combo.configure(values=names)
        if cur:
            self.account_var.set(cur)
        self.monitor_combo.configure(values=names)
        self.sender_combo.configure(values=names)
        self.monitor_var.set(accounts.monitor_name() or "")
        self.sender_var.set(accounts.sender_name() or "")

    def on_account_select(self):
        name = self.account_var.get()
        if name:
            accounts.set_current(name)
            logger.info(f"切换当前账号：{name}")

    def on_account_add(self):
        import tkinter.simpledialog as sd
        name = sd.askstring("新增账号", "请输入账号名称：")
        if not name:
            return
        accounts.add_account(name)
        accounts.set_current(name)
        self._refresh_account_combos()
        logger.info(f"新增账号：{name}（请点击「重新扫码」完成登录）")

    def on_account_delete(self):
        name = self.account_var.get()
        if not name:
            return
        if messagebox.askyesno("确认删除", f"将删除账号「{name}」及其全部凭证，确定？"):
            accounts.remove_account(name)
            self._refresh_account_combos()
            logger.info(f"已删除账号：{name}")

    def on_account_rescan(self):
        name = self.account_var.get() or accounts.current_name()
        if not name:
            return
        if self.adm and self.adm.is_running():
            messagebox.showwarning("提示", "请先停止任务，再重新扫码")
            return
        try:
            self.adm = self.adm or A()
            self.adm.rescan_and_rebuild(name)
            messagebox.showinfo("完成", f"账号「{name}」已重新扫码并重建会话")
        except Exception as e:
            messagebox.showerror("扫码失败", str(e))

    def on_set_monitor(self):
        name = self.monitor_var.get()
        if name:
            accounts.set_monitor(name)
            logger.info(f"监测账号：{name}")

    def on_set_sender(self):
        name = self.sender_var.get()
        if name:
            accounts.set_sender(name)
            logger.info(f"发送账号：{name}")

    # ================================================================== #
    # 浏览器守护 / 接收守护
    # ================================================================== #
    def on_daemon_toggle(self):
        if browser_daemon.is_daemon_running():
            try:
                browser_daemon._request_quit()
            except Exception as e:
                logger.warning(f"停止守护失败：{e}")
        else:
            self._start_daemon()

    def _start_daemon(self):
        acc = accounts.current_name() or "主"
        # 封装版：用自身可执行文件（打包后=exe 自身）带 --mode 拉起，取消 python -m 脚本启动
        cmd = [sys.executable, "--mode", "daemon", "--account", acc]
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            logger.info("浏览器守护已启动（常驻保活凭证）")
        except Exception as e:
            logger.error(f"启动守护失败：{e}")

    def _recv_daemon_running(self):
        st = self._recv_request("/status")
        return st is not None

    def on_recv_daemon_toggle(self):
        if self._recv_daemon_running():
            try:
                self._recv_request("/quit", method="POST")
            except Exception as e:
                logger.warning(f"停止接收守护失败：{e}")
        else:
            self._start_recv_daemon()

    def _start_recv_daemon(self):
        # 封装版：用自身可执行文件带 --mode 拉起
        cmd = [sys.executable, "--mode", "recv"]
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            logger.info("接收守护已启动")
        except Exception as e:
            logger.error(f"启动接收守护失败：{e}")

    # ================================================================== #
    # 私信聚合 / 回复
    # ================================================================== #
    def _dm_add_conv_tab(self, title, conv_id):
        if conv_id and conv_id in self.dm_conv_tabs:
            return
        frame = tk.Frame(self.dm_conv_nb, bg=BG)
        txt = tk.Text(frame, bg=PANEL2, fg=TEXT, relief="flat", wrap="word",
                      font=("Microsoft YaHei", 10), state="disabled", insertbackground=TEXT)
        txt.pack(fill="both", expand=True, padx=6, pady=6)
        self.dm_conv_nb.add(frame, text=title)
        if conv_id:
            self.dm_conv_tabs[conv_id] = {"frame": frame, "text": txt, "conv_id": conv_id}
        else:
            self.dm_conv_tabs["__placeholder__"] = {"frame": frame, "text": txt, "conv_id": None}

    def _on_dm_account_select(self, ev):
        sel = self.dm_account_tree.selection()
        if not sel:
            return
        acc = self.dm_account_tree.item(sel[0], "values")[0]
        self._load_dm_convs(acc)

    def _recv_request(self, path, method="GET", data=None):
        import urllib.request
        host = getattr(C, "RECV_DAEMON_HOST", "127.0.0.1")
        port = getattr(C, "RECV_DAEMON_PORT", 9912)
        url = f"http://{host}:{port}{path}"
        try:
            req = urllib.request.Request(url, method=method,
                                         data=json.dumps(data).encode() if data else None,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            return None

    def _load_dm_convs(self, acc):
        resp = self._recv_request(f"/conversations?account={urllib.parse.quote(acc)}")
        data = (resp or {}).get("conversations") if resp else None
        if data is None:
            data = []
        # 清理旧 tab
        for cid, tab in list(self.dm_conv_tabs.items()):
            if cid != "__placeholder__":
                try:
                    self.dm_conv_nb.forget(tab["frame"])
                except Exception:
                    pass
        self.dm_conv_tabs = {k: v for k, v in self.dm_conv_tabs.items() if k == "__placeholder__"}
        if not data:
            return
        for conv in data:
            cid = conv.get("conversation_id")
            title = conv.get("peer_name") or conv.get("peer_id") or (cid or "")[:8]
            self._dm_add_conv_tab(title, cid)
            txt = self.dm_conv_tabs.get(cid, {}).get("text")
            if txt:
                txt.configure(state="normal")
                txt.delete("1.0", "end")
                for m in conv.get("messages", []):
                    role = "对方" if m.get("role") == "them" else "我"
                    txt.insert("end", f"[{role}] {m.get('text','')}\n")
                txt.configure(state="disabled")

    def on_dm_send(self):
        sel = self.dm_account_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先在左侧选择账号与会话")
            return
        acc = self.dm_account_tree.item(sel[0], "values")[0]
        cur = self.dm_conv_nb.select()
        if not cur:
            return
        conv_id = None
        for cid, t in self.dm_conv_tabs.items():
            if cid == "__placeholder__":
                continue
            try:
                if str(t["frame"]) == str(self.dm_conv_nb.nametowidget(cur)):
                    conv_id = cid
                    break
            except Exception:
                pass
        content = self.dm_reply.get("1.0", "end").strip()
        if not content or not conv_id:
            return
        try:
            resp = self._recv_request("/send", method="POST",
                                      data={"account": acc, "conv_id": conv_id, "text": content})
            if resp and resp.get("ok"):
                self.dm_reply.delete("1.0", "end")
                logger.info(f"已回复 {acc} / {conv_id}: {content[:20]}")
            else:
                messagebox.showerror("发送失败", (resp or {}).get("error", "未知错误"))
        except Exception as e:
            messagebox.showerror("发送失败", str(e))

    def _open_logs(self):
        path = os.path.join(auto_dm_root, "logs")
        os.makedirs(path, exist_ok=True)
        try:
            os.startfile(path)
        except Exception:
            pass

    def on_open_features(self):
        try:
            from auto_dm import features_gui
            features_gui.open_features(self)
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    # ================================================================== #
    # 统计导出 xlsx
    # ================================================================== #
    STATS_EXPORT_DIR = os.path.join(auto_dm_root, "stats_export")

    def _stats_xlsx_path(self, live_id=None):
        lid = live_id or getattr(C, "LIVE_ID", None) or "unknown"
        return os.path.join(self.STATS_EXPORT_DIR, f"stats_{lid}.xlsx")

    def _export_stats_xlsx(self, live_id=None, auto=False):
        if not self.adm or not getattr(self.adm, "dispatch", None):
            return (False, "尚未运行，无统计数据")
        records = getattr(self.adm.dispatch, "records", [])
        if not records:
            return (False, "暂无记录")
        from collections import OrderedDict
        import openpyxl
        data_util.check_and_create_path(self.STATS_EXPORT_DIR)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "明细"
        cols = ["发言人", "评论内容", "私信情况", "私信文案", "私信时间", "发言时间"]
        ws.append(cols)
        for r in records:
            ws.append([r.get("nickname", ""), r.get("comment", ""), r.get("status", ""),
                       r.get("content", ""), r.get("send_ts", ""), r.get("capture_ts", "")])
        wv = wb.create_sheet("按发言人")
        vcols = ["发言人", "发言次数", "评论内容", "私信情况", "私信文案", "私信时间", "发言时间"]
        wv.append(vcols)
        agg = OrderedDict()
        for r in records:
            agg.setdefault(r.get("nickname", ""), []).append(r)
        for name, rs in agg.items():
            wv.append([
                name, len(rs),
                "\n".join(x.get("comment", "") for x in rs),
                "\n".join(x.get("status", "") for x in rs),
                "\n".join(x.get("content", "") for x in rs),
                "\n".join(x.get("send_ts", "") for x in rs),
                "\n".join(x.get("capture_ts", "") for x in rs),
            ])
        path = self._stats_xlsx_path(live_id)
        wb.save(path)
        return (True, path)

    def _save_stats_manual(self):
        ok, msg = self._export_stats_xlsx()
        if ok:
            messagebox.showinfo("已保存", f"统计已导出：\n{msg}")
        else:
            messagebox.showinfo("提示", msg)

    def _auto_save_stats_on_exit(self):
        try:
            ok, msg = self._export_stats_xlsx(auto=True)
            if ok:
                logger.info(f"退出时已自动保存统计：{msg}")
        except Exception as e:
            logger.warning(f"退出自动保存统计失败：{e}")

    # ================================================================== #
    # 轮询刷新
    # ================================================================== #
    def _poll(self):
        try:
            self._refresh_status()
            self._refresh_stats()
            if self.notebook.index(self.notebook.select()) == self.notebook.index(self.inbox_frame):
                self._refresh_dm()
            if self.notebook.index(self.notebook.select()) == self.notebook.index(self.account_frame):
                self._refresh_account_status()
        except Exception as e:
            logger.debug(f"轮询异常：{e}")
        self._refresh_after_id = self.after(2000, self._poll)

    def _refresh_status(self):
        running = bool(self.adm and self.adm.is_running())
        paused = bool(self.adm and self.adm.dispatch and getattr(self.adm.dispatch, "paused", False))
        sent = self.adm.sent_count() if self.adm else 0
        limit = self.adm.max_target() if self.adm else C.MAX_TARGET
        queue = self.adm.dispatch.queue_size() if (self.adm and self.adm.dispatch) else 0
        daemon = browser_daemon.is_daemon_running()

        if paused:
            label, color = "已暂停", YELLOW
        elif running:
            label, color = "运行中", GREEN
        else:
            label, color = "未启动", RED
        self.status_label.configure(text=label)
        self._set_dot(color)

        self.top_sent.configure(text=f"已发送 {sent}")
        self.top_limit.configure(text=f"上限 {limit}")
        self.top_queue.configure(text=f"待发 {queue}")
        if hasattr(self, "ov_card"):
            self.ov_card["已发送"].configure(text=str(sent))
            self.ov_card["发送上限"].configure(text=str(limit))
            self.ov_card["待发送"].configure(text=str(queue))
            self.ov_card["账号"].configure(text=accounts.current_name() or "—")
            self.ov_card["守护进程"].configure(text="运行" if daemon else "停止",
                                               fg=GREEN if daemon else RED)

        # overview 状态摘要
        if hasattr(self, "ov_status"):
            self.ov_status.configure(state="normal")
            self.ov_status.delete("1.0", "end")
            lines = [
                f"运行状态：{label}",
                f"当前账号：{accounts.current_name() or '—'}",
                f"监测账号：{accounts.monitor_name() or '—'}  发送账号：{accounts.sender_name() or '—'}",
                f"直播间房间号：{getattr(C,'LIVE_ID',None) or '（未解析）'}",
                f"已发送：{sent} / 上限：{limit}  待发：{queue}",
                f"浏览器守护：{'运行' if daemon else '未运行'}  接收守护：{'运行' if self._recv_daemon_running() else '未运行'}",
            ]
            self.ov_status.insert("end", "\n".join(lines))
            self.ov_status.configure(state="disabled")

    def _refresh_stats(self):
        if not (self.adm and getattr(self.adm, "dispatch", None)):
            return
        records = self.adm.dispatch.records
        sent = sum(1 for r in records if r.get("status", "").startswith(("已发送", "发送成功")))
        self.stats_var.set(f"记录 {len(records)} · 已发送 {sent}")
        if hasattr(self, "ov_card"):
            self.ov_card["捕获评论"].configure(text=str(len(records)))
        # 填充树
        tree = getattr(self, "stats_tree", None)
        if not tree:
            return
        for it in tree.get_children():
            tree.delete(it)
        if self.stats_mode == "view":
            from collections import OrderedDict
            agg = OrderedDict()
            for r in records:
                agg.setdefault(r.get("nickname", ""), []).append(r)
            max_lines = 1
            for name, rs in agg.items():
                comments = "\n".join(x.get("comment", "") for x in rs) or ""
                statuses = "\n".join(x.get("status", "") for x in rs) or ""
                contents = "\n".join(x.get("content", "") for x in rs) or ""
                times = "\n".join(x.get("send_ts", "") for x in rs) or ""
                caps = "\n".join(x.get("capture_ts", "") for x in rs) or ""
                max_lines = max(max_lines, len(rs))
                tree.insert("", "end", values=[name, len(rs), comments, statuses, contents, times, caps])
            try:
                tree.configure(rowheight=max(22, min(max_lines, 12) * 18))
            except Exception:
                pass
        else:
            for r in records:
                tree.insert("", "end", values=[
                    r.get("nickname", ""), r.get("comment", ""), r.get("status", ""),
                    r.get("content", ""), r.get("send_ts", ""), r.get("capture_ts", "")])

    def _refresh_dm(self):
        try:
            accs = accounts.list_accounts()
            tree = self.dm_account_tree
            cur = {tree.item(i, "values")[0] for i in tree.get_children()}
            for a in accs:
                if a not in cur:
                    tree.insert("", "end", values=[a, 0])
            running = self._recv_daemon_running()
            self.recv_status_var.set("接收守护：运行" if running else "接收守护：未运行")
            self.btn_recv_daemon.configure(
                text="■ 停止接收守护" if running else "▶ 启动接收守护")
            if not running:
                return
            # 更新未读数
            for i in tree.get_children():
                a = tree.item(i, "values")[0]
                unread = 0
                try:
                    resp = self._recv_request(f"/conversations?account={urllib.parse.quote(a)}")
                    convs = (resp or {}).get("conversations") or []
                    unread = sum(c.get("unread", 0) for c in convs)
                except Exception:
                    pass
                tree.set(i, "unread", unread)
        except Exception as e:
            logger.debug(f"刷新私信异常：{e}")

    def _refresh_account_status(self):
        tree = getattr(self, "account_status_tree", None)
        if not tree:
            return
        for it in tree.get_children():
            tree.delete(it)
        for name in accounts.list_accounts():
            try:
                st = accounts.account_status(name, timeout=5)
            except Exception as e:
                st = {"error": str(e)}
            tree.insert("", "end", values=[
                name,
                st.get("credential", "未知"),
                st.get("web_protect", "—"),
                st.get("keys", "—"),
                st.get("ticket", "—"),
                st.get("ts_sign", "—"),
            ])

    # ================================================================== #
    # 退出
    # ================================================================== #
    def _on_exit(self):
        try:
            self._auto_save_stats_on_exit()
        except Exception:
            pass
        try:
            if self._refresh_after_id:
                self.after_cancel(self._refresh_after_id)
        except Exception:
            pass
        accounts.clear_credentials()  # 守护在跑则内部跳过
        try:
            self.destroy()
        except Exception:
            pass


def main():
    setup_style()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
