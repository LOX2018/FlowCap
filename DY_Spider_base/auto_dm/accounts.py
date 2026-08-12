# coding=utf-8
"""多账号管理层（抖音自动私信）。

设计目标：
  - 支持在 GUI 中切换多个抖音账号（每个账号独立一套 .env 凭证）；
  - 监控每个账号的“私信签名状态”（DY_TICKET / DY_PRIVATE_KEY 是否存在 + 探活）。

存储结构：
  auto_dm/accounts.json        账号索引（name -> env 相对路径）
  auto_dm/accounts/<name>/.env 每个账号独立的凭证文件
  根目录 .env                  作为“默认账号”（兼容旧用法，name="默认账号”）

调用方（run.py / auth_helper / login_api）统一通过 env_path 参数指定要读写的
.env 文件，从而实现账号切换。
"""

import os
import json
import time
from dotenv import load_dotenv

from auto_dm.vbrowser import app_root  # 统一应用根：源码态=项目根，打包态=exe 所在目录

_ROOT = app_root()  # DY_Spider_base（源码态）/ exe 所在目录（打包态，随附资源根）
_ACCOUNTS_DIR = os.path.join(_ROOT, "auto_dm", "accounts")
_INDEX_PATH = os.path.join(_ACCOUNTS_DIR, "accounts.json")
_DEFAULT_ENV = os.path.join(_ROOT, ".env")          # 旧版/默认账号
_DEFAULT_NAME = "默认账号"

# 探活结果缓存（避免频繁请求）：name -> (ts, ok, info)
_status_cache = {}


def _ensure_dirs():
    os.makedirs(_ACCOUNTS_DIR, exist_ok=True)


def _load_index():
    _ensure_dirs()
    if not os.path.exists(_INDEX_PATH):
        # 兼容旧版：若根 .env 存在，自动登记为“默认账号”
        idx = {"current": _DEFAULT_NAME, "accounts": {_DEFAULT_NAME: ".env"}}
        if os.path.exists(_DEFAULT_ENV):
            _save_index(idx)
        return idx
    try:
        with open(_INDEX_PATH, "r", encoding="utf-8") as f:
            idx = json.load(f)
    except Exception:
        return {"current": _DEFAULT_NAME, "accounts": {_DEFAULT_NAME: ".env"}}

    # 兼容性修正：旧版把 rel 存成了 "accounts/<name>/.env"（带前缀），
    # 与 _ACCOUNTS_DIR 拼接会变成双重 accounts 前缀。这里归一化为 "<name>/.env"，
    # 并把旧错误路径下已生成的 .env 文件迁移到正确位置。
    fixed = False
    for name, rel in list(idx.get("accounts", {}).items()):
        if rel.startswith("accounts/") or rel.startswith("accounts\\"):
            clean_rel = rel[len("accounts/"):] if rel.startswith("accounts/") else rel[len("accounts\\"):]
            bad_path = os.path.join(_ACCOUNTS_DIR, rel)
            good_path = os.path.join(_ACCOUNTS_DIR, clean_rel)
            if os.path.exists(bad_path) and not os.path.exists(good_path):
                os.makedirs(os.path.dirname(good_path), exist_ok=True)
                try:
                    os.replace(bad_path, good_path)
                except Exception:
                    pass
                # 清理旧的双重前缀目录
                try:
                    bad_dir = os.path.dirname(bad_path)
                    if bad_dir.startswith(_ACCOUNTS_DIR) and os.path.isdir(bad_dir):
                        import shutil
                        shutil.rmtree(bad_dir, ignore_errors=True)
                except Exception:
                    pass
            idx["accounts"][name] = clean_rel
            fixed = True
    if fixed:
        _save_index(idx)
    return idx


def _save_index(idx):
    _ensure_dirs()
    with open(_INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)


def list_accounts():
    """返回 [(name, env_path), ...]，env_path 为绝对路径。"""
    idx = _load_index()
    out = []
    for name, rel in idx.get("accounts", {}).items():
        if rel == ".env":
            out.append((name, _DEFAULT_ENV))
        else:
            out.append((name, os.path.join(_ACCOUNTS_DIR, rel)))
    return out


def current_name():
    return _load_index().get("current", _DEFAULT_NAME)


def current_env_path():
    """当前选中账号（current，作为默认监测/发送账号）的 .env 绝对路径。"""
    idx = _load_index()
    name = idx.get("current", _DEFAULT_NAME)
    rel = idx.get("accounts", {}).get(name, ".env")
    return _DEFAULT_ENV if rel == ".env" else os.path.join(_ACCOUNTS_DIR, rel)


def _env_path_of(name):
    idx = _load_index()
    rel = idx.get("accounts", {}).get(name, ".env")
    return _DEFAULT_ENV if rel == ".env" else os.path.join(_ACCOUNTS_DIR, rel)


def env_path_of(name):
    """公开别名：根据账号名取 .env 绝对路径。"""
    return _env_path_of(name)


def profile_dir_of(env_path):
    """根据账号 .env 路径推导该账号独占的浏览器 profile 目录（指纹封存隔离）。

    每个账号一个独立 profile 目录，避免新增账号复用别的账号的浏览器登录态/指纹。
    规则：env_path 为 '.../accounts/<name>/.env' 时，profile 目录取 '.../accounts/<name>/profile'；
    若为根 .env（默认账号），则取项目根 'vb_profile_default'。
    """
    env_path = os.path.abspath(env_path)
    parent = os.path.dirname(env_path)          # .../accounts/<name>
    if (os.path.basename(env_path) == ".env"
            and os.path.basename(os.path.dirname(parent)) == "accounts"):
        # .../accounts/<name>/.env  ->  .../accounts/<name>/profile
        return os.path.join(parent, "profile")
    # 默认账号 / 其他：退回项目根下的独立目录
    return os.path.join(_ROOT, "vb_profile_default")


def monitor_name():
    """监测账号（用于直播间监听弹幕，需管理器权限才能看到完整昵称）。"""
    idx = _load_index()
    name = idx.get("monitor") or idx.get("current", _DEFAULT_NAME)
    if name not in idx.get("accounts", {}):
        name = _DEFAULT_NAME
    return name


def sender_name():
    """发送账号（用于私信发送，需有私信权限）。"""
    idx = _load_index()
    name = idx.get("sender") or idx.get("current", _DEFAULT_NAME)
    if name not in idx.get("accounts", {}):
        name = _DEFAULT_NAME
    return name


def monitor_env_path():
    return _env_path_of(monitor_name())


def sender_env_path():
    return _env_path_of(sender_name())


def set_monitor(name):
    idx = _load_index()
    if name not in idx.get("accounts", {}):
        raise ValueError(f"账号不存在: {name}")
    idx["monitor"] = name
    _save_index(idx)


def set_sender(name):
    idx = _load_index()
    if name not in idx.get("accounts", {}):
        raise ValueError(f"账号不存在: {name}")
    idx["sender"] = name
    _save_index(idx)


def set_current(name):
    idx = _load_index()
    if name not in idx.get("accounts", {}):
        raise ValueError(f"账号不存在: {name}")
    idx["current"] = name
    _save_index(idx)


def add_account(name):
    """新增账号（空的独立 .env，无任何预置凭证）。返回该账号的 env 绝对路径。

    新账号的 .env 初始为空文件，只有用户在该账号下完成扫码登录后，
    凭证（DY_COOKIES / DY_TICKET 等）才会由基座 save_credential 写回。
    """
    if not name or not name.strip():
        raise ValueError("账号名不能为空")
    name = name.strip()
    idx = _load_index()
    if name in idx.get("accounts", {}):
        raise ValueError(f"账号已存在: {name}")
    rel = os.path.join(name, ".env")   # 相对 _ACCOUNTS_DIR，避免与 _ACCOUNTS_DIR 拼接出双重前缀
    env_path = os.path.join(_ACCOUNTS_DIR, name, ".env")
    os.makedirs(os.path.dirname(env_path), exist_ok=True)
    # 创建完全空的 .env（不预置任何凭证内容，仅扫码后才会写入）
    if not os.path.exists(env_path):
        with open(env_path, "w", encoding="utf-8") as f:
            pass
    idx["accounts"][name] = rel
    _save_index(idx)
    return env_path


# 退出时清空凭证时抹除的字段（登录态 + 私信签名）。这些字段失效快、且含敏感登录态，
# 每次关软件应清空，避免下次启动复用过期/他人凭证。账号结构、私信任务配置、日志均不受影响。
_CREDENTIAL_KEYS = (
    "DY_COOKIES", "DY_TICKET", "DY_TS_SIGN",
    "DY_CLIENT_CERT", "DY_PRIVATE_KEY", "DY_COOKIE_STR",
)


def _strip_credential_lines(env_path):
    """把 .env 中凭证字段行删除并重写，返回是否发生了改动。"""
    if not os.path.exists(env_path):
        return False
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    kept = [ln for ln in lines
            if not any(ln.strip().startswith(k + "=") or ln.strip().startswith(k + " =")
                       for k in _CREDENTIAL_KEYS)]
    changed = len(kept) != len(lines)
    if changed:
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(kept)
    return changed


def clear_credentials_of(env_path):
    """仅清空单个账号 .env 的凭证字段（保留文件/结构/其他配置）。"""
    if not env_path or not os.path.exists(env_path):
        return False
    try:
        load_dotenv(env_path, override=True)
        for k in _CREDENTIAL_KEYS:
            os.environ.pop(k, None)
        return _strip_credential_lines(env_path)
    except Exception as e:
        logger.warning(f"[账号] 清空凭证失败 {env_path}: {e}")
        return False


# 守护进程存活标记（由 browser_daemon 写入/删除）。
# GUI 退出时若守护仍在运行，则不清凭证（凭证由常驻浏览器容器保活）。
# 注意：必须与应用根一致（打包态=exe 旁 auto_dm/），否则守护与 GUI 各自解压到不同
# _MEIxxx 临时目录后互相找不到标记，导致“误判守护不在”而清空凭证。
_DAEMON_ALIVE_FLAG = os.path.join(_ROOT, "auto_dm", ".daemon_alive")


def daemon_is_alive():
    """判断常驻浏览器守护进程是否在运行（守护退出才清凭证的判据之一）。"""
    if not os.path.exists(_DAEMON_ALIVE_FLAG):
        return False
    try:
        with open(_DAEMON_ALIVE_FLAG, "r", encoding="utf-8") as f:
            pid = int(f.read().strip())
        try:
            import psutil
            return psutil.pid_exists(pid)
        except Exception:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                return s.connect_ex(("127.0.0.1", 9911)) == 0
            finally:
                s.close()
    except Exception:
        return False


def clear_credentials(force=False):
    """清空所有账号（含默认账号）.env 中的登录凭证字段，但保留文件本身与账号结构。

    用于“仅在守护进程也退出时才清空凭证”的场景（用户诉求：守住所不退则凭证有效）：
      - force=True 或不传：仅在【没有常驻守护进程存活】时才清空；
        若有守护进程在跑（browser_daemon 常驻容器），则不清，保留活凭证。
      - force=True 强制清空（仅用于守护进程自身退出 / 显式清理）。

    日志、账号列表、私信任务配置（config.py）均不受影响，
    仅抹除易过期/敏感的登录态与私信签名。
    """
    # 守护存活时默认不清（GUI 单独关闭不影响凭证）
    if not force and daemon_is_alive():
        logger.info("[账号] 检测到常驻守护进程仍在运行，保留登录凭证（由守护容器保活）")
        return 0
    cleared = 0
    targets = [(_DEFAULT_ENV, _DEFAULT_NAME)]
    try:
        for name, env_path in list_accounts():
            targets.append((env_path, name))
    except Exception:
        pass
    for env_path, name in targets:
        if clear_credentials_of(env_path):
            cleared += 1
    logger.info(f"[账号] 软件退出：已清空 {cleared} 个账号的登录凭证（日志/配置/账号结构保留）")
    return cleared


def remove_account(name):
    """删除账号（同时删除其 .env 目录）。默认账号不允许删。"""
    if name == _DEFAULT_NAME:
        raise ValueError("默认账号不可删除")
    idx = _load_index()
    if name not in idx.get("accounts", {}):
        raise ValueError(f"账号不存在: {name}")
    rel = idx["accounts"].pop(name)
    if rel != ".env":
        env_path = os.path.join(_ACCOUNTS_DIR, rel)
        try:
            if os.path.isdir(os.path.dirname(env_path)):
                import shutil
                shutil.rmtree(os.path.dirname(env_path))
        except Exception:
            pass
    if idx.get("current") == name:
        idx["current"] = _DEFAULT_NAME
    _save_index(idx)


def _read_status(env_path):
    """读取该 env 的签名配置情况（不探活，纯本地）。"""
    if not os.path.exists(env_path):
        return {"exists": False, "has_ticket": False, "has_private_key": False,
                "has_cookie": False}
    load_dotenv(env_path, override=True)
    ticket = os.getenv("DY_TICKET")
    pkey = os.getenv("DY_PRIVATE_KEY")
    cookie = os.getenv("DY_COOKIES")
    return {
        "exists": True,
        "has_ticket": bool(ticket),
        "has_private_key": bool(pkey),
        "has_cookie": bool(cookie),
    }


def account_status(name=None, force=False, timeout=10):
    """返回账号状态字典：包含签名存在性与探活结果。

    force=False 时使用 60s 缓存，避免频繁网络探活。
    timeout: 探活（get_my_uid）超时秒数。
    """
    if name is None:
        name = current_name()
    idx = _load_index()
    rel = idx.get("accounts", {}).get(name, ".env")
    env_path = _DEFAULT_ENV if rel == ".env" else os.path.join(_ACCOUNTS_DIR, rel)

    local = _read_status(env_path)
    now = time.time()
    cached = _status_cache.get(name)
    if not force and cached and (now - cached[0] < 60):
        ok, info = cached[1], cached[2]
    else:
        ok, info = _probe(env_path, timeout)
        _status_cache[name] = (now, ok, info)

    if not local.get("exists"):
        return {"name": name, "env": env_path, "level": "missing",
                "label": "未配置（无 .env）", "alive": False,
                "has_ticket": False, "has_private_key": False, "has_cookie": False}
    if not (local.get("has_ticket") and local.get("has_private_key")):
        return {"name": name, "env": env_path, "level": "nosign",
                "label": "未扫码（缺私信签名）", "alive": False,
                "has_ticket": local["has_ticket"],
                "has_private_key": local["has_private_key"],
                "has_cookie": local["has_cookie"]}
    if ok:
        return {"name": name, "env": env_path, "level": "ok",
                "label": f"有效（uid={info}）", "alive": True,
                "has_ticket": True, "has_private_key": True,
                "has_cookie": local["has_cookie"], "uid": info}
    return {"name": name, "env": env_path, "level": "expired",
            "label": "失效（探活失败/超时）", "alive": False,
            "has_ticket": True, "has_private_key": True,
            "has_cookie": local["has_cookie"]}


def _probe(env_path, timeout):
    """用 get_my_uid 探活。返回 (ok, uid_or_error)。"""
    try:
        from builder.auth import DouyinAuth
        from dy_apis.douyin_api import DouyinAPI
        import threading

        def worker():
            try:
                load_dotenv(env_path, override=True)
                auth = DouyinAuth()
                cookies = os.getenv("DY_COOKIES", "") or ""
                auth.perepare_auth(cookies, "", "")
                auth.ticket = os.getenv("DY_TICKET") or None
                auth.ts_sign = os.getenv("DY_TS_SIGN") or None
                auth.client_cert = os.getenv("DY_CLIENT_CERT") or None
                auth.private_key = os.getenv("DY_PRIVATE_KEY") or None
                return DouyinAPI.get_my_uid(auth)
            except Exception as e:
                return None

        res = {}
        t = threading.Thread(target=lambda: res.update({"v": worker()}))
        t.daemon = True
        t.start()
        t.join(timeout)
        if t.is_alive():
            return False, "timeout"
        if "v" in res and res["v"]:
            return True, res["v"]
        return False, "empty"
    except Exception as e:
        return False, str(e)[:80]
