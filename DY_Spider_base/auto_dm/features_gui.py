# coding=utf-8
"""基座功能浏览器（独立窗口）：把 ccv-cat/Douyin_Spider 基座的全部 API 做成可点击调用。

从主 GUI「基座功能」按钮打开。每个分组一个区块，输入必要参数点按钮即调用
auto_dm.features 封装，结果以可读文本显示在下方结果框。

涉及"写操作"（发评论/直播点赞/发弹幕/收藏/点赞作品/建会话发私信）均带确认，
避免误触。所有调用需要已登录的 auth（由主 GUI 当前账号提供）。
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

from auto_dm.features import (
    user_info, user_all_works, user_favorite,
    work_info, work_all_comments, publish_comment,
    search_user, search_work, search_live,
    follower_list, following_list,
    live_info, live_all_production, live_digg, live_send_msg,
    digg_work, collect_work, collect_list,
    notice_list, feed, conversation_list,
)


def _get_auth():
    """取当前已登录 auth（复用 accounts 选中账号的登录态）。"""
    try:
        from auto_dm.auth_helper import get_current_auth
        auth, _ = get_current_auth()
        if auth is None:
            messagebox.showerror("未登录", "请先在「账号管理」完成登录/选择账号。")
        return auth
    except Exception as e:
        messagebox.showerror("错误", f"获取账号失败: {e}")
        return None


class FeatureWindow:
    def __init__(self, root):
        self.root = root
        self.root.title("基座功能浏览器 - Douyin_Spider 全功能")
        self.root.geometry("900x680")

        # 左侧分组按钮区
        left = ttk.Frame(root, width=220)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=6)
        ttk.Label(left, text="功能分组", font=("Microsoft YaHei", 11, "bold")).pack(pady=4)
        groups = [
            ("用户 / 作品", self._tab_user),
            ("评论", self._tab_comment),
            ("搜索", self._tab_search),
            ("粉丝 / 关注", self._tab_relation),
            ("直播互动", self._tab_live),
            ("点赞 / 收藏", self._tab_interact),
            ("消息 / 推荐", self._tab_msg),
            ("私信会话", self._tab_dm),
        ]
        for name, cmd in groups:
            ttk.Button(left, text=name, command=cmd, width=20).pack(pady=3, fill=tk.X)

        # 右侧：参数输入 + 结果
        right = ttk.Frame(root)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.param_frame = ttk.LabelFrame(right, text="参数", padding=8)
        self.param_frame.pack(fill=tk.X, pady=(0, 6))
        self.result = scrolledtext.ScrolledText(right, state=tk.NORMAL)
        self.result.pack(fill=tk.BOTH, expand=True)
        self.result.configure(font=("Consolas", 10))
        ttk.Button(right, text="清空结果", command=lambda: self.result.delete("1.0", tk.END)).pack(anchor=tk.E, pady=4)

        self._show_hint()

    # ---------- 工具 ----------
    def _show_hint(self):
        self._clear_params()
        ttk.Label(self.param_frame, text="← 选择左侧功能分组，填写参数后点执行").pack()

    def _clear_params(self):
        for w in self.param_frame.winfo_children():
            w.destroy()

    def _entry(self, row, label, key, default=""):
        """在 param_frame 加一行输入框，返回 (StringVar,)。"""
        ttk.Label(self.param_frame, text=label).grid(row=row, column=0, sticky=tk.W, pady=2)
        var = tk.StringVar(value=default)
        ttk.Entry(self.param_frame, textvariable=var, width=50).grid(
            row=row, column=1, sticky=tk.W, padx=4, pady=2)
        return var

    def _run(self, fn, *vars, confirm=None):
        if confirm:
            if not messagebox.askyesno("确认", confirm):
                return
        auth = _get_auth()
        if auth is None:
            return
        args = [v.get().strip() for v in vars]
        self.result.insert(tk.END, f"\n>>> {fn.__name__}{args}\n")
        res = fn(auth, *args)
        self._print_result(res)

    def _print_result(self, res):
        import json as _json
        if isinstance(res, dict) and res.get("ok"):
            try:
                text = _json.dumps(res.get("data"), ensure_ascii=False, indent=2, default=str)
            except Exception:
                text = str(res.get("data"))
            self.result.insert(tk.END, text + "\n")
        elif isinstance(res, dict) and not res.get("ok"):
            self.result.insert(tk.END, f"[失败] {res.get('error')}\n")
        else:
            self.result.insert(tk.END, f"{res}\n")
        self.result.see(tk.END)

    # ---------- 分组 ----------
    def _tab_user(self):
        self._clear_params()
        self.param_frame.configure(text="用户 / 作品")
        u = self._entry(0, "用户主页链接/URL:", "u")
        ttk.Button(self.param_frame, text="查询用户资料",
                   command=lambda: self._run(user_info, u)).grid(row=0, column=2, padx=4)
        w = self._entry(1, "用户主页链接:", "w")
        ttk.Button(self.param_frame, text="抓取全部作品",
                   command=lambda: self._run(user_all_works, w)).grid(row=1, column=2, padx=4)
        fv = self._entry(2, "用户 sec_id:", "fv")
        ttk.Button(self.param_frame, text="收藏列表",
                   command=lambda: self._run(user_favorite, fv)).grid(row=2, column=2, padx=4)

    def _tab_comment(self):
        self._clear_params()
        self.param_frame.configure(text="评论")
        url = self._entry(0, "作品链接:", "url")
        ttk.Button(self.param_frame, text="抓全部评论",
                   command=lambda: self._run(work_all_comments, url)).grid(row=0, column=2, padx=4)
        aweme = self._entry(1, "作品 aweme_id:", "aweme")
        content = self._entry(2, "评论内容:", "content")
        reply = self._entry(3, "回复评论ID(空=发新评论):", "reply")
        ttk.Button(self.param_frame, text="发布评论",
                   command=lambda: self._run(publish_comment, aweme, content, reply,
                                             confirm="确认发布该评论？")).grid(row=1, column=3, padx=4)

    def _tab_search(self):
        self._clear_params()
        self.param_frame.configure(text="搜索")
        q = self._entry(0, "关键词:", "q")
        ttk.Button(self.param_frame, text="搜用户",
                   command=lambda: self._run(search_user, q)).grid(row=0, column=2, padx=4)
        ttk.Button(self.param_frame, text="搜作品",
                   command=lambda: self._run(search_work, q)).grid(row=0, column=3, padx=4)
        ttk.Button(self.param_frame, text="搜直播",
                   command=lambda: self._run(search_live, q)).grid(row=0, column=4, padx=4)

    def _tab_relation(self):
        self._clear_params()
        self.param_frame.configure(text="粉丝 / 关注")
        uid = self._entry(0, "用户数字 uid:", "uid")
        sec = self._entry(1, "用户 sec_id:", "sec")
        ttk.Button(self.param_frame, text="粉丝列表",
                   command=lambda: self._run(follower_list, uid, sec)).grid(row=0, column=2, padx=4)
        ttk.Button(self.param_frame, text="关注列表",
                   command=lambda: self._run(following_list, uid, sec)).grid(row=1, column=2, padx=4)

    def _tab_live(self):
        self._clear_params()
        self.param_frame.configure(text="直播互动")
        lid = self._entry(0, "直播间号 live_id:", "lid")
        ttk.Button(self.param_frame, text="直播间信息",
                   command=lambda: self._run(live_info, lid)).grid(row=0, column=2, padx=4)
        lurl = self._entry(1, "直播间链接:", "lurl")
        ttk.Button(self.param_frame, text="带货列表",
                   command=lambda: self._run(live_all_production, lurl)).grid(row=1, column=2, padx=4)
        rid = self._entry(2, "房间 room_id:", "rid")
        ttk.Button(self.param_frame, text="直播点赞",
                   command=lambda: self._run(live_digg, rid,
                                             confirm="确认给该直播间点赞？")).grid(row=2, column=2, padx=4)
        rmsg = self._entry(3, "弹幕内容:", "rmsg")
        ttk.Button(self.param_frame, text="发直播弹幕",
                   command=lambda: self._run(live_send_msg, rid, rmsg,
                                             confirm="确认发送该弹幕？")).grid(row=3, column=2, padx=4)

    def _tab_interact(self):
        self._clear_params()
        self.param_frame.configure(text="点赞 / 收藏")
        aweme = self._entry(0, "作品 aweme_id:", "aweme")
        ttk.Button(self.param_frame, text="点赞作品",
                   command=lambda: self._run(digg_work, aweme,
                                             confirm="确认点赞该作品？")).grid(row=0, column=2, padx=4)
        ttk.Button(self.param_frame, text="收藏作品",
                   command=lambda: self._run(collect_work, aweme,
                                             confirm="确认收藏该作品？")).grid(row=0, column=3, padx=4)
        ttk.Button(self.param_frame, text="我的收藏夹",
                   command=lambda: self._run(collect_list)).grid(row=1, column=2, padx=4)

    def _tab_msg(self):
        self._clear_params()
        self.param_frame.configure(text="消息 / 推荐")
        ttk.Button(self.param_frame, text="消息通知列表",
                   command=lambda: self._run(notice_list)).grid(row=0, column=0, padx=4)
        ttk.Button(self.param_frame, text="推荐流",
                   command=lambda: self._run(feed)).grid(row=0, column=1, padx=4)

    def _tab_dm(self):
        self._clear_params()
        self.param_frame.configure(text="私信会话")
        uid = self._entry(0, "对方数字 uid:", "uid")
        short = self._entry(1, "会话 short_id:", "short")
        ttk.Button(self.param_frame, text="会话列表",
                   command=lambda: self._run(conversation_list, uid, short)).grid(row=0, column=2, padx=4)


def open_features():
    win = tk.Toplevel()
    FeatureWindow(win)
    return win


if __name__ == "__main__":
    root = tk.Tk()
    FeatureWindow(root)
    root.mainloop()
