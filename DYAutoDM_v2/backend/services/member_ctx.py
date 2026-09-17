# coding=utf-8
"""会员运行时上下文 —— 当前会员状态 + 数据空间路径切换 + .env 加密封装。

三个职责：
1. 会话管理：登录成功后的 master_key 只在进程内存（绝不写盘）；
   token -> (member_id, master_key, ts)，TTL 过期需重新登录。
2. 路径切换：提供 member_aware 的 db/accounts/data 路径解析 ——
   database.py / accounts.py 在 import 时固化了 app_root() 路径，
   本模块提供「登录后重建这些路径」的机制（DY_MEMBER 环境变量 + 路径函数改造）。
3. .env 加密封装：会员空间内账号 .env 整文件 Fernet 加密存储，
   读写经 read_env_file / write_env_file / dotenv 解密视图，明文不落盘。

铁律：本模块零网络请求、零浏览器操作。
"""
from __future__ import annotations

import io
import os
import threading
import time

from loguru import logger

from services import member_store

# ---------------------------------------------------------------------------
# 会话管理（进程内）
# ---------------------------------------------------------------------------

_SESSION_TTL = 12 * 3600  # 12h 后需重新登录
_LOCK = threading.RLock()
# token -> {"member_id", "username", "master_key", "ts"}
_sessions: dict[str, dict] = {}


def _now() -> float:
    return time.time()


def _persist_path() -> str:
    """会话持久化文件（存当前会员空间；不存在则用 app_root 下的默认位置）。"""
    try:
        import vbrowser
        root = vbrowser.app_root()
    except Exception:
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
    return os.path.join(root, "members", ".session.json")


def _persist_save(token: str, member_id: str, username: str,
                  master_key: str) -> None:
    """把当前会话落盘，backend 重启后可自动恢复（免重复登录）。

    本地单机软件：token 与 master_key 仅存本机会员空间，文件权限依赖
    用户账户隔离；这是「重启即失效」体验问题的根治方案。
    """
    try:
        p = _persist_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        import json as _json
        with open(p, "w", encoding="utf-8") as f:
            _json.dump({"token": token, "member_id": member_id,
                        "username": username, "master_key": master_key,
                        "ts": _now()}, f)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[member] 会话持久化失败（不影响本次登录）: {e}")


def _persist_load() -> dict | None:
    try:
        p = _persist_path()
        if not os.path.isfile(p):
            return None
        import json as _json
        with open(p, encoding="utf-8") as f:
            d = _json.load(f)
        if not d.get("token") or not d.get("member_id"):
            return None
        if _now() - float(d.get("ts") or 0) > _SESSION_TTL:
            return None
        return d
    except Exception:
        return None


def restore_persisted_session() -> bool:
    """backend 启动时调用：恢复上次登录会话（避免每次重启都要重新登录）。"""
    d = _persist_load()
    if not d:
        return False
    with _LOCK:
        _sessions[d["token"]] = {
            "member_id": d["member_id"],
            "username": d["username"],
            "master_key": d["master_key"],
            "ts": _now(),
        }
    set_current(d["member_id"], d["username"], d["master_key"], d["token"])
    logger.info(f"[member] 已恢复上次登录会话: {d['username']}")
    return True


def create_session(member_id: str, username: str, master_key: str) -> str:
    import secrets as _secrets
    token = _secrets.token_hex(32)
    _persist_save(token, member_id, username, master_key)
    with _LOCK:
        # 清理过期会话
        for t in [t for t, s in _sessions.items() if _now() - s["ts"] > _SESSION_TTL]:
            _sessions.pop(t, None)
        _sessions[token] = {
            "member_id": member_id,
            "username": username,
            "master_key": master_key,
            "ts": _now(),
        }
    return token


def get_session(token: str) -> dict | None:
    if not token:
        return None
    with _LOCK:
        s = _sessions.get(token)
        if s is None:
            return None
        if _now() - s["ts"] > _SESSION_TTL:
            _sessions.pop(token, None)
            return None
        s["ts"] = _now()  # 滑动续期
        return dict(s)


def destroy_session(token: str) -> None:
    with _LOCK:
        _sessions.pop(token, None)
    # 2026-09-17 修补（OCR 审查 HIGH —— 陈旧 token 注销会误删有效会话）：
    # 原实现**无条件**删除落盘的会话文件。若旧标签页用陈旧 token 登出
    # （发生在另一次新登录之后），会把当前有效会员的持久化会话一起清掉 →
    # 重启后 `restore_persisted_session()` 失败，用户被迫重新登录。
    # 现仅当落盘 token 与本次销毁的 token 一致（或文件已过期/无 token）时才删。
    try:
        p = _persist_path()
        if not os.path.isfile(p):
            return
        d = _persist_load() or {}
        if d.get("token") == token or not d.get("token"):
            os.remove(p)
        else:
            logger.info("[member] 注销的是陈旧 token，保留当前有效持久化会话")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 当前会员上下文（backend 进程单用户运行时）
# ---------------------------------------------------------------------------

_current: dict | None = None  # {"member_id","username","master_key","token"}


def set_current(member_id: str, username: str, master_key: str, token: str) -> None:
    """设置（单会话模式下替换）当前会员，并把环境变量注入给后续 spawn 的子进程。"""
    global _current
    with _LOCK:
        _current = {
            "member_id": member_id,
            "username": username,
            "master_key": master_key,
            "token": token,
        }
    # 子进程（recv_daemon / browser_daemon）经环境变量继承会员空间与主密钥。
    # 注意：这只存在于本进程环境块，不落任何盘上文件。
    os.environ["DY_MEMBER"] = member_id
    os.environ["DY_MEMBER_KEY"] = master_key
    logger.info(f"[member-ctx] 当前会员: {username} ({member_id})")


def clear_current() -> None:
    global _current
    with _LOCK:
        _current = None
    os.environ.pop("DY_MEMBER", None)
    os.environ.pop("DY_MEMBER_KEY", None)
    logger.info("[member-ctx] 已登出")


def current() -> dict | None:
    with _LOCK:
        return dict(_current) if _current else None


def current_member_id() -> str | None:
    c = current()
    return c["member_id"] if c else None


def master_key() -> str | None:
    """当前主密钥：优先内存会话 → 环境变量（子进程）→ 盘上会话文件（独立进程）。

    2026-09-10 补第三层回退（账号管理校验失效事故）：
      独立 Python 进程 / 未走 set_current() 的调用方既没有内存会话、
      也没有 DY_MEMBER_KEY 环境变量，master_key() 返回 None →
      is_member_env 判据② 失效 → .env.enc 读不出来。
      盘上 members/.session.json 是登录时写入的（含 master_key），
      且只在用户登出/换会员时改写，作为最后回退可让独立进程也能解密凭证。
      **注意**：这只是「读取兜底」，不改变登录态语义，也不写回任何文件。
    """
    c = current()
    if c:
        return c["master_key"]
    env_key = os.environ.get("DY_MEMBER_KEY")
    if env_key:
        return env_key
    return _session_file_key()


# 盘上会话文件里主密钥的进程内缓存（避免每次读盘；文件 mtime 变化自动失效）
_SESSION_KEY_CACHE: dict = {}


def _session_file_key() -> str | None:
    """从 <app_root>/members/.session.json 读主密钥（最后回退，带 mtime 缓存）。"""
    global _SESSION_KEY_CACHE
    try:
        from vbrowser import app_root
        sf = os.path.join(app_root(), "members", ".session.json")
    except Exception:
        return None
    if not os.path.exists(sf):
        return None
    try:
        mtime = os.path.getmtime(sf)
        if _SESSION_KEY_CACHE.get("mtime") == mtime:
            return _SESSION_KEY_CACHE.get("key")
        import json as _json
        with open(sf, "r", encoding="utf-8") as f:
            d = _json.load(f)
        k = d.get("master_key") or None
        _SESSION_KEY_CACHE = {"mtime": mtime, "key": k}
        return k
    except Exception:
        return None


def _session_file_member_id() -> str | None:
    """从 <app_root>/members/.session.json 读 member_id（最后回退）。"""
    try:
        from vbrowser import app_root
        sf = os.path.join(app_root(), "members", ".session.json")
    except Exception:
        return None
    if not os.path.exists(sf):
        return None
    try:
        import json as _json
        with open(sf, "r", encoding="utf-8") as f:
            d = _json.load(f)
        return d.get("member_id") or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 会员感知路径解析（单会员运行时的「应用根」= 会员数据空间）
# ---------------------------------------------------------------------------

def _resolve_member_id() -> str:
    """解析当前 member_id 的**唯一入口**（2026-09-17 抽取）。

    优先序：进程内 current → 环境变量 DY_MEMBER → 盘上会话文件。
    会话文件回退是 2026-09-10 增加的（让独立进程也能解析到会员空间，
    避免账号路径落到非会员空间后 .env.enc 读不出来）。

    ## 为什么要抽成单一函数

    OCR 审查 HIGH：`accounts_root()` 原先**先**用 `member_space_root()` 的结果
    做空值守卫、**再**用另一套等价但独立的表达式重新解析 member_id。
    两处一旦出现任一回退来源缺失（或 `current_member_id()` 在两次调用之间
    被 `set_current()` 改动），守卫取到的目录根与最终拼接用的 id 就会**指向
    不同会员** —— 这正是历史上「账号管理校验失效 / .env.enc 读不出来」的
    事故面：路径归属判断错误会让 `is_member_env()` 落到错误分支。
    抽成单一函数后，**同一请求内 id 只解析一次**，杜绝该分叉。
    """
    mid = current_member_id() or os.environ.get("DY_MEMBER") or ""
    if not mid:
        mid = _session_file_member_id() or ""
    return mid


def member_space_root() -> str | None:
    """当前会员的数据空间根目录；未登录返回 None（调用方回退 app_root()）。

    2026-09-10：member_id 增加「盘上会话文件」回退（与 master_key 同理），
    让独立进程也能解析到会员空间，避免账号路径落到非会员空间后
    .env.enc 读不出来（账号管理校验失效事故）。
    """
    mid = _resolve_member_id()
    if not mid:
        return None
    return member_store.member_dir(mid)


def accounts_root() -> str | None:
    """当前会员的账号目录 <space>/auto_dm/accounts；未登录 None。

    2026-09-17 修补（OCR 审查 HIGH）：改为**复用同一个 mid**，不再二次解析。
    原实现 `member_space_root()` 做守卫 + 另一套表达式重新解析 id，两者
    缺任一回退来源时会指向不同会员（详见 `_resolve_member_id` 说明）。
    """
    mid = _resolve_member_id()
    if not mid:
        return None
    return member_store.member_accounts_dir(mid)


def db_path() -> str | None:
    """当前会员的数据库路径；未登录 None。

    2026-09-17 修补（OCR 审查 HIGH）：补上「盘上会话文件」回退，与
    `member_space_root()` / `accounts_root()` 保持一致。
    原实现只有 `current_member_id() or DY_MEMBER`，**未走 set_current 的
    独立进程**会拿到 None → 上游回落 app_root 的全局库，
    导致任务/会话数据读的是**全局库**而非会员库（数据串库）。
    """
    mid = _resolve_member_id()
    if not mid:
        return None
    return member_store.member_db_path(mid)


# ---------------------------------------------------------------------------
# .env 加密封装（会员空间内账号凭证整文件加密）
# ---------------------------------------------------------------------------

_ENC_SUFFIX = ".enc"
_SIGN_KEYS = ("DY_COOKIES", "DY_TICKET", "DY_TS_SIGN", "DY_CLIENT_CERT",
              "DY_PRIVATE_KEY", "DY_WEB_PROTECT", "DY_KEYS")


def is_member_env(env_path: str) -> bool:
    """该 .env 是否走会员加密封装（决定读写是否走加密视图）。

    2026-09-10 修复（账号管理校验失效事故）：
      原实现**只靠路径前缀**判断 env_path 是否位于 member_space_root() 内，
      但 member_space_root() 依赖 current()（进程内内存会话）——子进程、
      独立 Python 进程、以及未登录/会话态丢失的场景一律返回 None，
      于是 is_member_env 恒 False → parse_env_dict 走明文分支 →
      实际存在的 <env_path>.enc 被无视 → credentials_complete 判
      「账号 .env 不存在」→ wp 引擎 fail → 无谓重捕获循环。
      （凭证本身是好的：指纹浏览器可发私信、探活 uid 正确。）

    现改为**双判据**（任一成立即视为会员加密凭证）：
      ① 路径位于当前会员数据空间内（原语义，保留）；
      ② 存在 <env_path>.enc 且主密钥可用——**以文件事实为准**，
         不再依赖路径归属与进程会话态。

    判据 ② 是根治：无论路径是否错配、会话态是否注入，
    只要磁盘上确有该账号的加密凭证且密钥在手，就按会员加密凭证读。
    """
    if not env_path:
        return False
    # ① 原路径归属判据（会员空间内）
    root = member_space_root()
    if root:
        try:
            p = os.path.abspath(env_path)
            r = os.path.abspath(root)
            if p.startswith(r + os.sep) or p == r:
                return True
        except Exception:
            pass
    # ② 文件事实判据：存在 .enc 加密凭证 + 主密钥可用 → 按加密凭证处理
    try:
        if os.path.exists(_enc_path(env_path)) and master_key():
            return True
    except Exception:
        pass
    return False


def _enc_path(env_path: str) -> str:
    return env_path + _ENC_SUFFIX


def _encrypt_env_text(plain: str) -> str:
    key = master_key()
    if not key:
        raise RuntimeError("会员主密钥不可用，无法加密凭证")
    return member_store.encrypt_text(key, plain)


def _decrypt_env_file(env_path: str) -> str | None:
    """读加密 .env 并解密为明文文本；无加密文件返回 None。"""
    ep = _enc_path(env_path)
    if not os.path.exists(ep):
        return None
    key = master_key()
    if not key:
        raise RuntimeError("会员主密钥不可用（未登录？），无法读取加密凭证")
    with open(ep, "r", encoding="ascii") as f:
        token = f.read().strip()
    return member_store.decrypt_text(key, token)


def write_env_file(env_path: str, updates: dict, merge: bool = True) -> str:
    """写入 .env（会员空间内自动加密；外部路径走原 dotenv 语义）。

    updates: {KEY: value}；merge=True 时保留原文件其余键。
    返回实际落盘路径（会员空间内是 <env_path>.enc）。
    """
    if not is_member_env(env_path):
        # 非会员空间（默认账号/调试态）：保持原 dotenv 行为
        from dotenv import set_key
        os.makedirs(os.path.dirname(env_path), exist_ok=True)
        for k, v in updates.items():
            set_key(env_path, k, v, quote_mode="never")
        return os.path.abspath(env_path)

    os.makedirs(os.path.dirname(env_path), exist_ok=True)
    current_vals: dict[str, str] = {}
    if merge:
        try:
            current_vals = parse_env_dict(env_path)
        except Exception:
            current_vals = {}
    current_vals.update(updates)
    # 过滤空值键（与 save_credential 的 set_values 语义一致）
    kv_lines = [f"{k}={v}" for k, v in current_vals.items() if v]
    plain = "\n".join(kv_lines) + "\n"
    enc = _encrypt_env_text(plain)
    ep = _enc_path(env_path)
    tmp = ep + ".tmp"
    with open(tmp, "w", encoding="ascii") as f:
        f.write(enc)
    os.replace(tmp, ep)
    # 若存在历史明文 .env（旧版本迁移前残留），立即删除
    try:
        if os.path.exists(env_path):
            os.remove(env_path)
    except Exception:
        pass
    return os.path.abspath(ep)


def parse_env_dict(env_path: str) -> dict:
    """读取 .env 为 {KEY: value} 字典（会员空间内自动解密；外部原样 dotenv）。

    会员空间内：优先读 <path>.enc；不存在则读明文 <path>（旧数据迁移场景）。
    """
    if not is_member_env(env_path):
        from dotenv import dotenv_values
        if not os.path.exists(env_path):
            return {}
        return {k: v for k, v in dotenv_values(env_path).items() if v is not None}

    plain = _decrypt_env_file(env_path)
    if plain is None:
        # 无加密文件但有明文文件：按明文读（供一次性迁移加密）
        if os.path.exists(env_path):
            from dotenv import dotenv_values
            return {k: v for k, v in dotenv_values(env_path).items() if v is not None}
        return {}
    out = {}
    for line in plain.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v
    return out


def parse_env_text(env_path: str) -> str | None:
    """读取 .env 全文（会员空间内解密；外部读原文件）。不存在返回 None。"""
    if not is_member_env(env_path):
        if os.path.exists(env_path):
            with io.open(env_path, encoding="utf-8") as f:
                return f.read()
        return None
    return _decrypt_env_file(env_path)


def migrate_plain_envs(member_id: str, master_key_val: str) -> dict:
    """一次性迁移：把会员空间内所有明文 .env 加密为 .enc 并删除明文。

    登录成功后自动调用（幂等：无明文文件时零操作）。
    返回 {migrated: n, skipped: n}。
    """
    migrated = skipped = 0
    acc_dir = member_store.member_accounts_dir(member_id)
    if not os.path.isdir(acc_dir):
        return {"migrated": 0, "skipped": 0}
    for name in os.listdir(acc_dir):
        d = os.path.join(acc_dir, name)
        envp = os.path.join(d, ".env")
        if not os.path.isfile(envp):
            continue
        try:
            with io.open(envp, encoding="utf-8") as f:
                plain = f.read()
            enc = member_store.encrypt_text(master_key_val, plain)
            with open(envp + _ENC_SUFFIX, "w", encoding="ascii") as f:
                f.write(enc)
            os.remove(envp)
            migrated += 1
            logger.info(f"[member] 已加密迁移账号凭证: {name}")
        except Exception as e:  # noqa: BLE001
            skipped += 1
            logger.error(f"[MEM-004] " + f"[member] 加密迁移失败（保留明文）: {name}: {e}")
    return {"migrated": migrated, "skipped": skipped}
