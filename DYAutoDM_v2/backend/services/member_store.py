# coding=utf-8
"""会员管理体系 —— 核心存储与加密原语（v0.37.0 引入）。

设计目标（用户需求，2026-09-09）：
  登录后使用；本地存储与管理，不接云端；隔离用户数据 + 加密保护。

架构：
  members/registry.json          全局会员注册表（唯一跨会员共享文件）
      - 每会员：member_id / username / salt / pwd_hash (PBKDF2-SHA256 600k 轮)
              / wrapped_key (Fernet 加密的主密钥，口令派生 key 加密)
      - 口令绝不存明文，主密钥被口令包裹 —— 拖走文件读不出任何凭证
  members/<member_id>/           每会员独立数据空间（完全隔离）
      - data/dyautodm.db         独立 SQLite（会话/消息/账号索引/配置全在里面）
      - auto_dm/accounts/<name>/.env + profile/   独立抖音账号（.env 整文件加密）
      - data/origin_images/      独立图片缓存

加密方案（用户选定）：
  - 口令：PBKDF2-HMAC-SHA256，600_000 轮，16B 随机盐
  - 主密钥：每会员 32B 随机 → Fernet key；登录时用口令派生 key 解包
  - 账号 .env：整文件 Fernet 加密（读时解密、写时加密），明文不落盘
  - 运行期主密钥只在内存/子进程环境变量（DY_MEMBER_KEY），绝不写盘

铁律合规：本模块零网络请求、零浏览器操作，纯本地文件 + 密码学原语。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import threading

from loguru import logger

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

def members_root() -> str:
    """会员数据根目录：<app_root>/members/。"""
    try:
        from vbrowser import app_root
        root = app_root()
    except Exception:
        root = os.environ.get("DY_APP_ROOT") or os.getcwd()
    p = os.path.join(root, "members")
    os.makedirs(p, exist_ok=True)
    return p


def registry_path() -> str:
    return os.path.join(members_root(), "registry.json")


def member_dir(member_id: str) -> str:
    p = os.path.join(members_root(), member_id)
    os.makedirs(p, exist_ok=True)
    return p


def member_accounts_dir(member_id: str) -> str:
    p = os.path.join(member_dir(member_id), "auto_dm", "accounts")
    os.makedirs(p, exist_ok=True)
    return p


def member_data_dir(member_id: str) -> str:
    p = os.path.join(member_dir(member_id), "data")
    os.makedirs(p, exist_ok=True)
    return p


def member_db_path(member_id: str) -> str:
    return os.path.join(member_data_dir(member_id), "dyautodm.db")


# ---------------------------------------------------------------------------
# 注册表读写（带线程锁 + 原子写）
# ---------------------------------------------------------------------------

_REG_LOCK = threading.RLock()


def _load_registry() -> dict:
    p = registry_path()
    if not os.path.exists(p):
        return {"version": 1, "members": {}}
    try:
        with open(p, encoding="utf-8") as f:
            reg = json.load(f)
        if not isinstance(reg, dict) or "members" not in reg:
            return {"version": 1, "members": {}}
        return reg
    except Exception as e:  # noqa: BLE001
        logger.error(f"[MEM-005] " + f"[member] 注册表读取失败（返回空表，避免误删）: {e}")
        return {"version": 1, "members": {}}


def _save_registry(reg: dict) -> None:
    p = registry_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


# ---------------------------------------------------------------------------
# 密码学原语（全部标准库 + cryptography，零外部服务）
# ---------------------------------------------------------------------------

_PBKDF2_ITERS = 600_000


def _derive_key(password: str, salt: bytes, iters: int = _PBKDF2_ITERS) -> bytes:
    """口令 → 32B 派生密钥（用于口令哈希校验与主密钥包裹）。"""
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)


def hash_password(password: str) -> dict:
    """生成 {salt, iterations, hash}（均为 hex）。"""
    salt = secrets.token_bytes(16)
    dk = _derive_key(password, salt)
    return {
        "salt": salt.hex(),
        "iterations": _PBKDF2_ITERS,
        "hash": dk.hex(),
    }


def verify_password(password: str, rec: dict) -> bool:
    try:
        salt = bytes.fromhex(rec["salt"])
        iters = int(rec.get("iterations", _PBKDF2_ITERS))
        dk = _derive_key(password, salt, iters)
        return secrets.compare_digest(dk.hex(), rec["hash"])
    except Exception:
        return False


def new_master_key() -> str:
    """生成新主密钥（Fernet 兼容的 url-safe base64 32B）。"""
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode("ascii")


def wrap_master_key(master_key: str, password: str) -> str:
    """用口令派生 key 包裹主密钥（Fernet 加密），返回 token 字符串。

    2026-09-17 安全修补（审查 P1-5）：原实现用**单轮无盐 SHA-256**
    派生包裹密钥 —— 与同文件 `_derive_key` 的 600k 轮 PBKDF2 + 16B 盐
    严重不对称：registry.json 一旦被拖走，攻击者可用廉价单轮 SHA-256
    离线爆破 wrapped_key，**直接绕过 600k 轮设计**，且无盐可跨会员预计算。

    现改为与 `_derive_key` 同规格的 PBKDF2，并把 salt/iterations 一并
    存进 token（`v2$<salt_hex>$<iters>$<fernet_token>`）。

    **兼容性**：`unwrap_master_key` 仍识别旧格式（无 `v2$` 前缀）并回落
    到 SHA-256，已存在的会员无需重置即可正常登录；下次改密会自动升级。
    """
    from cryptography.fernet import Fernet
    salt = secrets.token_bytes(16)
    dk = _derive_key(password, salt)
    wrap_key = base64.urlsafe_b64encode(dk)
    tok = Fernet(wrap_key).encrypt(master_key.encode("ascii")).decode("ascii")
    return f"v2${salt.hex()}${_PBKDF2_ITERS}${tok}"


def unwrap_master_key(wrapped: str, password: str) -> str | None:
    """口令解包主密钥；口令错误/密文损坏返回 None。

    兼容两种格式：
      - 新：`v2$<salt_hex>$<iters>$<fernet_token>`（PBKDF2，见 wrap_master_key）
      - 旧：裸 Fernet token（单轮 SHA-256 派生，仅用于读取历史数据）
    """
    from cryptography.fernet import Fernet, InvalidToken
    try:
        s = str(wrapped or "")
        if s.startswith("v2$"):
            _, salt_hex, iters_s, tok = s.split("$", 3)
            dk = _derive_key(password, bytes.fromhex(salt_hex), int(iters_s))
            wrap_key = base64.urlsafe_b64encode(dk)
            payload = tok
        else:
            # 旧格式：单轮 SHA-256（保留读取能力，不再写入）
            wrap_key = base64.urlsafe_b64encode(
                hashlib.sha256(password.encode("utf-8")).digest())
            payload = s
        return Fernet(wrap_key).decrypt(payload.encode("ascii")).decode("ascii")
    except (InvalidToken, Exception):  # noqa: BLE001
        return None


def fernet_of(master_key: str) -> "Fernet | None":
    from cryptography.fernet import Fernet
    try:
        return Fernet(master_key.encode("ascii"))
    except Exception:
        return None


def encrypt_text(master_key: str, plaintext: str) -> str:
    f = fernet_of(master_key)
    if f is None:
        raise ValueError("主密钥无效")
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_text(master_key: str, token: str) -> str:
    f = fernet_of(master_key)
    if f is None:
        raise ValueError("主密钥无效")
    return f.decrypt(token.encode("ascii")).decode("utf-8")


# ---------------------------------------------------------------------------
# 会员生命周期
# ---------------------------------------------------------------------------

_USERNAME_RE = re.compile(r"^[\w\u4e00-\u9fff\-]{2,32}$")


def _new_member_id() -> str:
    return "m" + secrets.token_hex(8)


def register_member(username: str, password: str) -> dict:
    """注册新会员。返回 {ok, member_id?, msg?}。

    本地软件场景：开放自助注册（用户选定方案），无管理员审批。
    """
    username = (username or "").strip()
    if not _USERNAME_RE.match(username):
        return {"ok": False, "msg": "用户名限 2-32 位（中文/字母/数字/下划线/连字符）"}
    if len(password or "") < 6:
        return {"ok": False, "msg": "口令至少 6 位"}
    with _REG_LOCK:
        reg = _load_registry()
        for m in reg["members"].values():
            if m.get("username") == username:
                return {"ok": False, "msg": f"用户名已存在: {username}"}
        mid = _new_member_id()
        while mid in reg["members"]:
            mid = _new_member_id()
        master = new_master_key()
        rec = {
            "member_id": mid,
            "username": username,
            "created_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
            "pwd": hash_password(password),
            "wrapped_key": wrap_master_key(master, password),
        }
        reg["members"][mid] = rec
        _save_registry(reg)
    # 建数据空间目录
    member_accounts_dir(mid)
    member_data_dir(mid)
    logger.info(f"[member] 新会员已注册: {username} ({mid})")
    return {"ok": True, "member_id": mid, "master_key": master}


def authenticate(username: str, password: str) -> dict:
    """口令登录。成功返回 {ok, member_id, master_key}；失败 {ok:False, msg}。"""
    username = (username or "").strip()
    with _REG_LOCK:
        reg = _load_registry()
        rec = None
        for m in reg["members"].values():
            if m.get("username") == username:
                rec = m
                break
    if rec is None:
        return {"ok": False, "msg": "用户名或口令错误"}
    if not verify_password(password, rec.get("pwd", {})):
        return {"ok": False, "msg": "用户名或口令错误"}
    master = unwrap_master_key(rec.get("wrapped_key", ""), password)
    if master is None:
        return {"ok": False, "msg": "主密钥解包失败（口令正确但密钥损坏，数据空间不可用）"}
    return {"ok": True, "member_id": rec["member_id"], "master_key": master}


def change_password(member_id: str, old_password: str, new_password: str) -> dict:
    """修改口令：验证旧口令 → 重包裹主密钥（主密钥不变，数据不解密重加密）。"""
    if len(new_password or "") < 6:
        return {"ok": False, "msg": "新口令至少 6 位"}
    with _REG_LOCK:
        reg = _load_registry()
        rec = reg["members"].get(member_id)
        if rec is None:
            return {"ok": False, "msg": "会员不存在"}
        if not verify_password(old_password, rec.get("pwd", {})):
            return {"ok": False, "msg": "旧口令错误"}
        if not verify_password(old_password, rec.get("pwd", {})):
            return {"ok": False, "msg": "旧口令错误"}
        # 先用旧口令解包主密钥，再用新口令重新包裹（主密钥不变，数据无需重加密）
        master = unwrap_master_key(rec.get("wrapped_key", ""), old_password)
        if master is None:
            return {"ok": False, "msg": "主密钥解包失败，无法更换口令"}
        rec["pwd"] = hash_password(new_password)
        rec["wrapped_key"] = wrap_master_key(master, new_password)
        _save_registry(reg)
    logger.info(f"[member] 口令已更换: {rec.get('username')} ({member_id})")
    return {"ok": True}


def list_members() -> list[dict]:
    """会员列表（不含任何密钥材料）。"""
    with _REG_LOCK:
        reg = _load_registry()
    out = []
    for m in reg["members"].values():
        out.append({
            "memberId": m.get("member_id"),
            "username": m.get("username"),
            "createdAt": m.get("created_at", ""),
        })
    out.sort(key=lambda x: x.get("createdAt") or "")
    return out


def delete_member(member_id: str, password: str) -> dict:
    """删除会员：验证口令 → 删注册表记录 + 整个数据空间（含全部账号/profile）。"""
    import shutil
    with _REG_LOCK:
        reg = _load_registry()
        rec = reg["members"].get(member_id)
        if rec is None:
            return {"ok": False, "msg": "会员不存在"}
        if not verify_password(password, rec.get("pwd", {})):
            return {"ok": False, "msg": "口令错误"}
        d = member_dir(member_id)
        shutil.rmtree(d, ignore_errors=True)
        del reg["members"][member_id]
        _save_registry(reg)
    logger.warning(f"[MEM-006] " + f"[member] 会员已删除（含数据空间）: {rec.get('username')} ({member_id})")
    return {"ok": True}
