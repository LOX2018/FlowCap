# -*- coding: utf-8 -*-
"""回放沙箱：把 `FLOWCAP_APP_ROOT` 钉到独立临时目录，使回放用例可并发且不碰真机。

设计依据（`flowcap-dev-guards` §三·己 的实测教训）：
  探针/脚本把 `FLOWCAP_APP_ROOT` 放在 `app_root()` 之前，曾导致"读了另一个根"的误判。
  ⇒ 沙箱必须**显式钉根**，并在激活后**回读自证**根没被后续 import 改掉。
"""

import atexit
import os
import shutil
import sys
import tempfile

# 源码树（本文件位于 <repo>/FlowCap/backend/replay/sandbox.py）
_THIS = os.path.abspath(__file__)
_BACKEND = os.path.dirname(os.path.dirname(_THIS))          # <repo>/FlowCap/backend
_SRC_REPO = os.path.dirname(os.path.dirname(_BACKEND))      # <repo>（外层 DYchajian）

#: 真实数据根（本分支环境）。回放层**禁止**指向它。
DESIGN_ROOT = r"C:\temp\flowcap_design"
#: 已废弃的主分支数据根。命中即拒绝（防串环境）。
LEGACY_ROOT = r"C:\temp\flowcap_test"


class DesignRootForbidden(RuntimeError):
    """回放层被指向真实数据根 / 源码树时抛出。"""


def _forbidden_reason(path: str) -> str:
    p = os.path.abspath(path)
    low = p.lower()
    if low == DESIGN_ROOT.lower() or low.startswith(DESIGN_ROOT.lower() + os.sep):
        return f"指向真实数据根 DESIGN_ROOT（{DESIGN_ROOT}）"
    if low == LEGACY_ROOT.lower() or low.startswith(LEGACY_ROOT.lower() + os.sep):
        return f"指向已废弃主分支数据根（{LEGACY_ROOT}）"
    if low == _SRC_REPO.lower() or low.startswith(_SRC_REPO.lower() + os.sep):
        return f"落在源码树内（{_SRC_REPO}），会污染代码库"
    return ""


class Sandbox:
    """一个回放沙箱 = 一个独立临时根 + 钉住的环境变量。

    用法::

        with Sandbox("capture_parse") as sb:
            p = sb.materialize({"init.bin": raw})
            convs = cc.parse_init_protobuf(open(p["init.bin"], "rb").read(), my_uid)
    """

    def __init__(self, name: str = "replay", keep: bool = False):
        self.name = name or "replay"
        self.keep = keep
        self._root = None
        self._saved = {}
        self._created = []

    # ---------- 根管理 ----------
    @property
    def root(self) -> str:
        if self._root is None:
            raise RuntimeError("沙箱尚未激活（先 with Sandbox(...) 或 .activate()）")
        return self._root

    def _make_root(self) -> str:
        # 每个沙箱独立目录 ⇒ 多个回放用例可并发且互不污染
        return tempfile.mkdtemp(prefix=f"dybc_replay_{self.name}_")

    # ---------- P0-5：钉根必须对「已固化连接」也生效 ----------
    def _reset_db_bindings(self) -> None:
        """把已固化的 DB 连接/会员上下文重置到当前 `FLOWCAP_APP_ROOT`。

        ## 为什么必需（2026-09-23 缺陷实证）

        `database.py:84-85` 命中已建全局 `_conn` 会**早退** ——
        只改 `FLOWCAP_APP_ROOT` 而进程里已有连接时，`get_db()` 仍返回**旧根的库**。
        `unittest discover` 把全部模块导入同一进程，首个建连的模块会把根固化；
        之后的沙箱 `activate()` 便形同虚设（实测 `IS SANDBOX ROOT? False`）。

        故激活沙箱时必须：
          ① 关闭进程级 `_conn`（`database.reset_connection()`）；
          ② 清空会员上下文（`member_ctx` 内存态 + 盘上 `.session.json` 回退），
             否则 `_db_path()` 会经 `member_ctx.db_path()` 绕过 `FLOWCAP_APP_ROOT`
             指向**真实会员库**；
          ③ **回读自证**：真跑一次 `get_db()`，确认连的就是沙箱根内的库。
        """
        # ① 会员上下文：内存态 + 环境变量（盘上 .session.json 在新根下不存在，
        #    故清完这两项后 _resolve_member_id() 必然返回空 ⇒ 回落 app_root=DYSANDBOX）
        try:
            from services import member_ctx as _mctx
            _mctx.clear_current()
        except Exception:  # noqa: BLE001
            pass
        # ② 关闭全局连接
        try:
            import database as _db
            _db.reset_connection()
        except Exception:  # noqa: BLE001
            pass
        # ③ 回读自证：真建连并核对库文件落在沙箱根内
        try:
            import database as _db
            path = _db.get_db().execute(
                "PRAGMA database_list").fetchone()[2]
            want = os.path.normcase(os.path.abspath(self._root))
            got = os.path.normcase(os.path.abspath(path or ""))
            if not got.startswith(want):
                raise RuntimeError(
                    f"钉根失败（数据库连接未复位）：沙箱根={self._root}，"
                    f"get_db() 实际指向={path}")
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"沙箱钉根自证失败: {type(e).__name__}: {e}") from e

    def activate(self) -> "Sandbox":
        if self._root is not None:
            return self
        root = self._make_root()
        reason = _forbidden_reason(root)
        if reason:  # 理论不可达（temp 必不在禁列），保留为机械门禁
            shutil.rmtree(root, ignore_errors=True)
            raise DesignRootForbidden(reason)

        # 保存将被改动的环境变量（含会员上下文，退出时原样还原）
        self._saved = {k: os.environ.get(k)
                       for k in ("FLOWCAP_APP_ROOT", "DY_MEMBER", "DY_MEMBER_KEY")}
        os.environ["FLOWCAP_APP_ROOT"] = root
        self._root = root
        os.makedirs(os.path.join(root, "logs"), exist_ok=True)
        os.makedirs(os.path.join(root, "data"), exist_ok=True)

        # 回读自证：钉根后必须真的读回我们设的值
        got = os.path.abspath(os.environ["FLOWCAP_APP_ROOT"])
        if got.lower() != os.path.abspath(root).lower():
            raise RuntimeError(f"钉根失败：期望 {root}，回读得到 {got}")

        if _BACKEND not in sys.path:
            sys.path.insert(0, _BACKEND)

        # 会员上下文必须随沙箱隔离（否则 db_path() 指向真实会员库绕过 FLOWCAP_APP_ROOT）
        for k in ("DY_MEMBER", "DY_MEMBER_KEY"):
            os.environ.pop(k, None)

        # P0-5：强制复位已固化的 DB 连接 + 回读自证（对已建连接同样生效）
        self._reset_db_bindings()

        if not self.keep:
            atexit.register(self.cleanup)
        return self

    def cleanup(self) -> None:
        # 先复位 DB 绑定，避免连接继续持有沙箱库文件的句柄（Windows 上阻碍删除）
        try:
            import database as _db
            _db.reset_connection()
        except Exception:  # noqa: BLE001
            pass
        for k, v in (self._saved or {}).items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if self._root and not self.keep:
            shutil.rmtree(self._root, ignore_errors=True)
        self._root = None

    # ---------- 样本落盘 ----------
    def materialize(self, fixtures: dict) -> dict:
        """把 {相对路径: bytes} 写进沙箱根，返回 {相对路径: 绝对路径}。

        只允许写在本沙箱根内（拒绝绝对路径与 `..` 逃逸）。
        """
        out = {}
        for rel, blob in fixtures.items():
            if os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/"):
                raise ValueError(f"非法的相对路径：{rel!r}")
            dst = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(blob)
            out[rel] = dst
            self._created.append(dst)
        return out

    # ---------- 上下文管理 ----------
    def __enter__(self) -> "Sandbox":
        return self.activate()

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.cleanup()
        return False


_ACTIVE: Sandbox | None = None


def activate(name: str = "replay", keep: bool = False) -> Sandbox:
    """进程级单活跃沙箱（重复调用返回同一个，不嵌套）。"""
    global _ACTIVE
    if _ACTIVE is None or _ACTIVE.root is None:
        _ACTIVE = Sandbox(name=name, keep=keep).activate()
    return _ACTIVE


def active_root() -> str:
    """当前沙箱根（未激活则自动激活）。"""
    return activate().root
