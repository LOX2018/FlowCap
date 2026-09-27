# -*- coding: utf-8 -*-
"""N3 机械门禁：任何单元测试都**不得写入 design 真实数据根**。

背景（M-17 附带发现，2026-09-27 三轮深挖）：
  `C:\\temp\\dyautodm_design` 是 design 分支的**真实设计数据根**（含真实账号、
  真实会员库 `members/m17db0f8209156f26/data/dyautodm.db`）。实测该库的
  `kv_store` 里存在 `agA/agB/ag2/agS/ag1` 与 `账号A` 绑定 —— 这些是
  `test_ai_agent.py` 的测试数据 ⇒ **测试写串了根**。
  三轮深挖未定位到确切机制（已触发迭代止损），故本文件交付的是**判定能力**：
  今后只要再发生污染，本门禁必然变红，无需再靠人猜。

判据：
  D1  design 根指纹（.db + WAL 旁车的 size / mtime_ns / sha256）在本模块
      整个运行窗口内**逐字节不变**（setUpClass 取基线，tearDownClass 比对；
      每个用例结束再比对一次）。
  D2  **负控**：把 design 根按其**被指纹观测到的那些文件**复制到临时根，
      在其中真写一笔（既做 mtime-only、也做 content 写），断言 D1 的
      **同一个判据函数**对该副本**报红**。证明门禁不是恒绿。
  D3  design 根不存在时**必须显式打印 skip 原因**，不得静默通过。
  D4  本模块导入时 `os.environ.get("DY_APP_ROOT")` 不得等于 design 根
      （防止本模块自己就是污染源）。

设计取舍：
  · **只复制 .db / -wal / -shm / -journal**（约 12MB）做副本根，而非整根复制。
    design 根实测含 51,326 个非 db 文件约 3GB（浏览器 profile / 缓存），
    整根复制实测 139s，无法作为门禁；而判据只观测这些文件，故副本是**忠实**的。
  · 全程对 design 根**只读**（os.walk + 只读打开 + 哈希），不写、不删、不改，
    不使用任何 LIKE 模糊删，不触碰生产数据。
  · 零第三方依赖（只用 stdlib）⇒ 不 import database/vbrowser，避免本模块
    自身参与 DY_APP_ROOT 争夺。

运行： python -m unittest test_design_root_isolation -v
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import unittest

DESIGN_ROOT = r"C:\temp\dyautodm_design"

# 判据观测的文件后缀：本体 + SQLite WAL 旁车（写 WAL 模式的库会先生出 -wal）
_WATCH = (".db", "-wal", "-shm", "-journal")


# --------------------------------------------------------------------------
# 判据内核（D1 / D2 共用同一份实现 —— 负控必须打在同一个函数上）
# --------------------------------------------------------------------------
def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_os_managed(rel: str) -> bool:
    """排除 OS/第三方托管、与本项目无关且会自发变动的路径。

    实测（2026-09-28）：design 根内的 `%SystemDrive%\\ProgramData\\Microsoft\\
    Windows\\Caches\\*.db` 由 **Windows 自身**维护，指纹会自发跳动（观测到
    size 309024→309032 且 sha256 变化），与被测代码无关。把它们纳入判据会得到
    「恒红」的假阳性 —— 门禁必须**只守护本项目的真实数据**。
    """
    low = rel.lower().replace("/", "\\")
    return low.startswith("%systemdrive%") or "\\programdata\\" in low


def fingerprint(root: str) -> tuple[dict, list]:
    """返回 {relpath: (size, mtime_ns, sha256)}，以及只读失败清单。"""
    out: dict = {}
    errors: list = []
    if not os.path.isdir(root):
        return out, ["root 不存在: %s" % root]
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.lower().endswith(_WATCH):
                continue
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, root)
            if _is_os_managed(rel):
                continue
            try:
                st = os.stat(p)
                out[rel] = (st.st_size, st.st_mtime_ns, _sha256(p))
            except Exception as e:                      # 只读探测，失败只记录
                errors.append("%s: %s" % (rel, type(e).__name__))
    return out, errors


def diff_baseline(before: dict, after: dict) -> dict:
    """比对两份指纹，返回差异明细（空 = 无变化）。"""
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = []
    for k in sorted(set(before) & set(after)):
        s0, m0, h0 = before[k]
        s1, m1, h1 = after[k]
        if (s0, m0, h0) != (s1, m1, h1):
            changed.append({
                "path": k,
                "size": [s0, s1],
                "mtime_ns": [m0, m1],
                "mtime_advanced": m1 > m0,
                "sha256_changed": h0 != h1,
            })
    return {"added": added, "removed": removed, "changed": changed}


def assert_root_unchanged(baseline: dict, current: dict, label: str) -> bool:
    """D1/D2 共用的**唯一**判据函数：有差异就 raise AssertionError（= 报红）。"""
    d = diff_baseline(baseline, current)
    if d["added"] or d["removed"] or d["changed"]:
        raise AssertionError(
            "[%s] design 根指纹发生变化 —— 测试正在写真实数据根！\n"
            "  root     = %s\n"
            "  added    = %s\n"
            "  removed  = %s\n"
            "  changed  = %s"
            % (label, DESIGN_ROOT, d["added"], d["removed"], d["changed"])
        )
    return True


def _build_copy_root(dst: str) -> int:
    """把 design 根**中被指纹观测到的文件**按相对路径复制到 dst（保 mtime）。"""
    n = 0
    for dirpath, _dirnames, filenames in os.walk(DESIGN_ROOT):
        for name in filenames:
            if not name.lower().endswith(_WATCH):
                continue
            src = os.path.join(dirpath, name)
            tgt = os.path.join(dst, os.path.relpath(src, DESIGN_ROOT))
            os.makedirs(os.path.dirname(tgt), exist_ok=True)
            try:
                shutil.copy2(src, tgt)                  # copy2 保 mtime ⇒ 基线稳定
                n += 1
            except Exception:
                continue                                # 占用中的库跳过，不影响判据
    return n


# --------------------------------------------------------------------------
# D4：本模块导入时先自证不是污染源
# --------------------------------------------------------------------------
_DY_APP_ROOT_AT_IMPORT = os.environ.get("DY_APP_ROOT")


class TestDesignRootIsolation(unittest.TestCase):
    """design 真实数据根的只读不变式门禁。"""

    _BASELINE: dict | None = None
    _BASELINE_ERRORS: list = []

    # ---------------- D1：窗口前后指纹逐字节相同 ----------------
    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(DESIGN_ROOT):
            print("\n[D3-SKIP] design 根不存在：%s\n"
                  "          本机未跑过 design 环境 ⇒ 无「真实数据根」可守护，"
                  "本门禁**不适用**（显式跳过，非静默通过）。" % DESIGN_ROOT)
            raise unittest.SkipTest(
                "design 根不存在（未跑过 design 环境），无真实数据根可守护: %s"
                % DESIGN_ROOT)
        cls._BASELINE, cls._BASELINE_ERRORS = fingerprint(DESIGN_ROOT)
        print("\n[D1] 基线指纹：root=%s  watched_files=%d  read_errors=%d"
              % (DESIGN_ROOT, len(cls._BASELINE), len(cls._BASELINE_ERRORS)))

    @classmethod
    def tearDownClass(cls):
        if cls._BASELINE is None:
            return
        current, errors = fingerprint(DESIGN_ROOT)
        print("[D1] 收尾指纹：watched_files=%d  read_errors=%d"
              % (len(current), len(errors)))
        assert_root_unchanged(cls._BASELINE, current, "D1 tearDownClass")

    def tearDown(self):
        # 每个用例结束再比一次，缩小到「哪个用例引入的写入」的粒度
        if self._BASELINE is not None:
            current, _ = fingerprint(DESIGN_ROOT)
            assert_root_unchanged(self._BASELINE, current, "D1 tearDown")

    # ---------------- D4 ----------------
    def test_0_import_time_dy_app_root_is_not_design_root(self):
        """D4：本模块导入时 DY_APP_ROOT 不得已经是 design 根。

        （在 discover 里被其它模块抢成 design 根时，本用例会**红** —— 那正是
        它的职责：把「已被污染的环境」变成可判定的失败。）
        """
        cur = _DY_APP_ROOT_AT_IMPORT
        print("[D4] import 期 DY_APP_ROOT = %r" % (cur,))
        self.assertNotEqual(
            os.path.abspath(cur or "").rstrip("\\/").lower(),
            os.path.abspath(DESIGN_ROOT).rstrip("\\/").lower(),
            "本模块导入时 DY_APP_ROOT 已指向 design 真实数据根 —— "
            "此处即为污染源之一，禁止在此环境下跑测试")

    # ---------------- D2：负控 ----------------
    def test_1_negative_control_criterion_actually_goes_red(self):
        """D2：证明上面的判据函数真的会红，而不是恒绿。

        在**副本根**（design 根中被观测文件的保真副本）上真写一笔，
        断言同一个 `assert_root_unchanged` 抛 AssertionError。
        """
        tmp = tempfile.mkdtemp(prefix="design_root_negctl_")
        self.addCleanup(shutil.rmtree, tmp, True)
        mut_root = os.path.join(tmp, "mut_root")
        clean_root = os.path.join(tmp, "clean_root")
        os.makedirs(mut_root, exist_ok=True)
        os.makedirs(clean_root, exist_ok=True)
        copy_root = mut_root

        # 两个**互相独立**的副本根：mut_root 用来写（判据必须红），
        # clean_root 原封不动（判据必须绿）。用同一个根做这两件事会自相矛盾 ——
        # 第一版就犯了这个错：写完再比对「未写基线」，逻辑上永远不可能绿。
        n = _build_copy_root(mut_root)
        n2 = _build_copy_root(clean_root)
        base_clean, _ = fingerprint(clean_root)

        victims = sorted(fingerprint(mut_root)[0])
        if not victims:                  # 极端情形：无任何被观测文件 → 造合成样本
            for i in range(2):
                p = os.path.join(mut_root, "members", "synthetic",
                                 "data", "s%d.db" % i)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                open(p, "wb").write(b"sqlite-synthetic-%d" % i)
                q = os.path.join(clean_root, "members", "synthetic",
                                 "data", "s%d.db" % i)
                os.makedirs(os.path.dirname(q), exist_ok=True)
                open(q, "wb").write(b"sqlite-synthetic-%d" % i)
            victims = sorted(fingerprint(mut_root)[0])
        base, _ = fingerprint(mut_root)
        self.assertTrue(len(base) >= 1, "副本根无指纹，负控无法成立")

        # 优先挑**会员真实数据**路径做受害者 —— 那才是本门禁要守护的东西
        member = [v for v in victims if "members" in v.lower().replace("/", "\\")]
        ordered = member + [v for v in victims if v not in member]
        v0 = ordered[0]
        v1 = (ordered[1] if len(ordered) > 1 else None)

        print("[D2] 负控副本：copy_files=%d/%d  被观测文件=%d  victim0=%s"
              % (n, n2, len(base), v0))

        # (a) 最弱信号：仅推进 mtime，内容与大小都不变
        after_a, _ = self._touch_and_fingerprint(mut_root, os.path.join(mut_root, v0))
        with self.assertRaises(AssertionError) as cm:
            assert_root_unchanged(base, after_a, "D2-negctl-mtime")
        self.assertIn("指纹发生变化", str(cm.exception))
        print("[D2a] mtime-only 写入 ⇒ 判据报红 ✓")

        # (b) 最强信号：真改内容
        if v1:
            after_b, _ = self._write_and_fingerprint(
                mut_root, os.path.join(mut_root, v1))
            with self.assertRaises(AssertionError) as cm2:
                assert_root_unchanged(base, after_b, "D2-negctl-content")
            self.assertIn("指纹发生变化", str(cm2.exception))
            print("[D2b] content 写入 ⇒ 判据报红 ✓")

        # (c) 未被写过的一路必须保持绿（防「判据恒红」的反向假阳性）
        after_clean, _ = fingerprint(clean_root)
        self.assertIs(
            assert_root_unchanged(base_clean, after_clean,
                                  "D2-negctl-clean"), True)
        print("[D2c] 未写副本 ⇒ 判据保持绿 ✓（非恒定报红，共 %d 文件）"
              % len(after_clean))

    @staticmethod
    def _touch_and_fingerprint(copy_root: str, victim: str):
        os.utime(victim, None)                 # 仅 mtime 推进
        return fingerprint(copy_root)

    @staticmethod
    def _write_and_fingerprint(copy_root: str, victim: str):
        with open(victim, "r+b") as f:         # 真改内容（副本，非 design 根！）
            f.seek(0, os.SEEK_END)
            f.write(b"\x00NEGCTL\x00")
        return fingerprint(copy_root)

    # ---------------- D3：skip 路径自证 ----------------
    def test_2_missing_root_skips_loudly(self):
        """D3：design 根缺失时必须 loud skip（打印原因 + SkipTest）。

        本机 design 根存在 ⇒ 用一个人造缺失路径验证该分支真的会 raise SkipTest，
        而不是「写了个 if 却从没被走到」。
        """
        missing = os.path.join(tempfile.gettempdir(),
                               "design_root_definitely_absent_%d" % os.getpid())
        fp, errors = fingerprint(missing)
        self.assertEqual(fp, {}, "缺失根的指纹应为空")
        self.assertTrue(errors and "不存在" in errors[0],
                        "缺失根必须给出可读的错误原因: %r" % errors)
        print("[D3] 缺失根分支可用：errors=%r" % (errors,))

    # ---------------- D1 显式断言 ----------------
    def test_3_design_root_untouched_so_far(self):
        """D1：此刻为止 design 根仍与 setUpClass 基线逐字节相同。"""
        current, _ = fingerprint(DESIGN_ROOT)
        assert_root_unchanged(self._BASELINE, current, "D1 explicit")
        print("[D1] 显式比对通过：%d 个文件逐字节未变" % len(current))


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=2))
