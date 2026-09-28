# -*- coding: utf-8 -*-
"""A-3 负控/回归：批量下载中「单个作品失败」不得中断整批。

## 缺陷（真实定位，2026-09-28）

`downloader/downloader.py` 的 `run_task()` 原先把 `try:` 放在
**前置准备之后**（原 ~127-136 行）::

    if mgr.get(task.task_id) is None:
        mgr.add(task)
    media = MR.extract_media(aweme)        # ← 可抛（aweme 无法解析媒体）
    task.media_type = media["type"]
    task.quality = quality
    mgr.mark(task.task_id, status=TK.RUNNING, ...)   # ← 可抛
    out_dir = TK.archive_dir(base_dir, ...)          # ← 可抛
    os.makedirs(out_dir, exist_ok=True)              # ← 可抛（OSError）
    try:                                             # ← try 起点太晚
        ...

而 `run_batch()` 用 `list(ex.map(_one, awemes))` 编排：`_one` 抛异常 ⇒
`ThreadPoolExecutor.map` 的 Future 在 `list()` 物化时抛出 ⇒ **整批中断**，
其余作品全部不下、调用方收到异常。

## 修法

把「每个作品的**完整处理**（含 `extract_media` 与全部前置准备）」整体纳入
`try/except`：单作品异常 → 该任务标 `FAILED` + 写 `error` + 继续其余；
调用方不抛异常。既有「取消 → CANCELED」语义不变。

## 判据（负控）

1. 注入 1 个 `extract_media` 必失败的作品 → 整批仍跑完、
   `ok == 总数-1`、`failed == 1`、`run_batch` **不抛异常**；
2. 注入 1 个「前置准备（archive_dir）必失败」的作品 → 同上（证明覆盖的是
   **完整前置准备**，不只是 extract_media）；
3. 还原注入（无失败样本）→ 全绿（ok == total）；
4. 下载阶段失败（旧 try 内）与取消语义**零回归**。

## 离线

全程 monkeypatch `media_request.extract_media` 与 `downloader.download_file`，
**零网络**（URL 用 `dl.invalid`，fake 下载只写 1 字节本地文件）。

运行::

    cd backend
    DY_APP_ROOT=<已存在的临时目录> python -m unittest test_downloader_batch_skip -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

# ── 隔离：先立临时根（必须在 import downloader 之前）─────────────────────
# 与 test_uid_sink_ext 的经验一致：DY_APP_ROOT 必须存在，否则会回落到仓库
# 相对路径 data/ 污染工作树；这里也每条用例用独立 base_dir，互不干扰。
_ROOT = os.environ.get("DY_APP_ROOT", "")
if not _ROOT or not os.path.isdir(_ROOT):
    _ROOT = tempfile.mkdtemp(prefix="dl_batch_skip_test_")
    os.makedirs(_ROOT, exist_ok=True)
    os.environ["DY_APP_ROOT"] = _ROOT
os.environ.setdefault("DY_MEMBER", "")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import downloader.downloader as DL          # noqa: E402
from downloader import media_request as MR  # noqa: E402
from downloader import tasks as TK          # noqa: E402

OK_URL = "https://dl.invalid/ok.mp4"
FAIL_URL = "https://dl.invalid/fail.mp4"
CANCEL_URL = "https://dl.invalid/cancel.mp4"


# ── 离线替身 ────────────────────────────────────────────────────────────

def _fake_extract(aweme):
    """按样本标记返回/抛出 —— 完全离线，不触网。"""
    if aweme.get("_fail_extract"):
        # 模拟「aweme 的 media 无法解析 / 取址失败」
        raise RuntimeError("模拟取址失败：media 无法解析")
    url = FAIL_URL if aweme.get("_fail_download") else (
        CANCEL_URL if aweme.get("_cancel") else OK_URL)
    return {
        "aweme_id": str(aweme.get("aweme_id") or ""),
        "type": "video",
        "videos": {"h264": [url]},
        "images": [],
        "live_photos": [],
        "audio": [],
        "cover": "",
        "duration": 0,
        "desc": aweme.get("desc") or "",
    }


def _fake_download_file(url, dest, *, task=None, mgr=None, on_progress=None):
    """离线替身：按 URL 标记成功/失败/取消，成功只写 1 字节。"""
    if "cancel" in url:
        raise RuntimeError("已取消")
    if "fail" in url:
        raise RuntimeError("模拟下载失败：HTTP 500")
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "wb") as f:
        f.write(b"x")
    return dest


def _aweme(aid, *, fail_extract=False, fail_download=False, cancel=False,
           nickname="作者"):
    return {
        "aweme_id": aid,
        "desc": f"作品{aid}",
        "author": {"nickname": nickname},
        "_fail_extract": fail_extract,
        "_fail_download": fail_download,
        "_cancel": cancel,
    }


class BatchSkipTest(unittest.TestCase):
    def setUp(self):
        # 冻结替身
        self._orig_extract = MR.extract_media
        self._orig_dl = DL.download_file
        self._orig_mgr = TK._manager
        self._orig_archive = TK.archive_dir
        MR.extract_media = _fake_extract
        DL.download_file = _fake_download_file
        # 每个用例独立任务表（TK.manager() 是进程级单例）
        TK._manager = TK.TaskManager()
        # 每个用例独立落盘根
        self.base_dir = tempfile.mkdtemp(prefix="dl_skip_base_", dir=_ROOT)

    def tearDown(self):
        MR.extract_media = self._orig_extract
        DL.download_file = self._orig_dl
        TK.archive_dir = self._orig_archive
        TK._manager = self._orig_mgr

    # ---------- ① 负控：extract_media 抛异常 ⇒ 不得中断整批 ----------
    def test_single_extract_failure_does_not_abort_batch(self):
        awemes = [
            _aweme("ok1"), _aweme("ok2"),
            _aweme("bad", fail_extract=True),
            _aweme("ok3"),
        ]
        # 旧实现：此处 run_batch 会抛异常（RED）；修复后必须正常返回（GREEN）
        res = DL.run_batch(awemes, base_dir=self.base_dir, max_concurrent=3)

        self.assertEqual(res["total"], 4)
        self.assertEqual(res["ok"], 3, "失败 1 个后其余 3 个仍须成功")
        self.assertEqual(res["failed"], 1)
        self.assertEqual(len(res["tasks"]), 4, "四个作品都要有任务记录")

        bad = [t for t in res["tasks"] if t["aweme_id"] == "bad"][0]
        self.assertEqual(bad["status"], TK.FAILED, "失败作品须标 FAILED")
        self.assertTrue(bad["error"], "失败作品须写 error（不得静默）")
        # 且必须真的落进任务表（mark 目标存在）
        self.assertIsNotNone(TK.manager().get(bad["task_id"]))
        self.assertEqual(TK.manager().get(bad["task_id"]).status, TK.FAILED)

    # ---------- ② 负控：前置准备（archive_dir）抛异常 ⇒ 同样跳过 ----------
    def test_prep_stage_failure_does_not_abort_batch(self):
        orig = self._orig_archive

        def _boom_archive(base, *, nickname="", auto_folder=True,
                          folder_tpl="{nickname}"):
            if nickname == "BOOM":
                raise OSError("模拟归档目录准备失败")
            return orig(base, nickname=nickname, auto_folder=auto_folder,
                        folder_tpl=folder_tpl)

        TK.archive_dir = _boom_archive
        awemes = [
            _aweme("n1", nickname="正常作者"),
            _aweme("n2", nickname="BOOM"),
        ]
        res = DL.run_batch(awemes, base_dir=self.base_dir, max_concurrent=2)

        self.assertEqual(res["total"], 2)
        self.assertEqual(res["ok"], 1)
        self.assertEqual(res["failed"], 1)
        bad = [t for t in res["tasks"] if t["aweme_id"] == "n2"][0]
        self.assertEqual(bad["status"], TK.FAILED)
        self.assertIn("模拟归档目录准备失败", bad["error"])

    # ---------- ③ 还原注入：全绿（happy path 零回归）----------
    def test_no_injection_all_success(self):
        awemes = [_aweme("a"), _aweme("b"), _aweme("c")]
        res = DL.run_batch(awemes, base_dir=self.base_dir, max_concurrent=3)
        self.assertEqual(res["total"], 3)
        self.assertEqual(res["ok"], 3)
        self.assertEqual(res["failed"], 0)
        for t in res["tasks"]:
            self.assertEqual(t["status"], TK.COMPLETED)
            self.assertEqual(t["error"], "")
            self.assertTrue(os.path.isfile(os.path.join(
                t["save_path"], f"作者_作品{t['aweme_id']}_{t['aweme_id']}.mp4")),
                "成功作品须真正落盘（离线替身也应写文件）")

    # ---------- ④ 下载阶段失败（旧 try 已覆盖）零回归 ----------
    def test_download_stage_failure_isolated(self):
        awemes = [_aweme("d1"), _aweme("d2", fail_download=True)]
        res = DL.run_batch(awemes, base_dir=self.base_dir, max_concurrent=2)
        self.assertEqual(res["total"], 2)
        self.assertEqual(res["ok"], 1)
        self.assertEqual(res["failed"], 1)
        bad = [t for t in res["tasks"] if t["aweme_id"] == "d2"][0]
        self.assertEqual(bad["status"], TK.FAILED)
        self.assertIn("模拟下载失败", bad["error"])

    # ---------- ⑤ 取消语义零回归：CANCELED 且 error 为空 ----------
    def test_cancel_semantics_preserved(self):
        awemes = [_aweme("c1"), _aweme("c2", cancel=True)]
        res = DL.run_batch(awemes, base_dir=self.base_dir, max_concurrent=2)
        self.assertEqual(res["total"], 2)
        self.assertEqual(res["ok"], 1)
        self.assertEqual(res["failed"], 1)
        c = [t for t in res["tasks"] if t["aweme_id"] == "c2"][0]
        self.assertEqual(c["status"], TK.CANCELED, "已取消须标 CANCELED（非 FAILED）")
        self.assertEqual(c["error"], "", "取消不写 error")

    # ---------- ⑥ run_task 直接调用：单作品失败不抛、返回 task ----------
    def test_run_task_returns_task_on_prep_failure(self):
        t = TK.DownloadTask(task_id="t-direct", aweme_id="x", nickname="作者")
        out = DL.run_task(t, _aweme("x", fail_extract=True),
                          base_dir=self.base_dir)
        self.assertIs(out, t, "run_task 单作品异常必须自行收敛并返回 task")
        self.assertEqual(t.status, TK.FAILED)
        self.assertTrue(t.error)
        self.assertEqual(TK.manager().get("t-direct").status, TK.FAILED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
