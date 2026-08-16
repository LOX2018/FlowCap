# coding=utf-8
"""多账号管理层（抖音自动私信）。

设计目标：
  - 支持在 GUI 中切换多个抖音账号（每个账号独立一套 .env 凭证）；
  - 监控每个账号的“私信签名状态”（DY_TICKET / DY_PRIVATE_KEY 是否存在 + 探活）。

存储结构：
  auto_dm/accounts.json        账号索引（name -> env 相对路径）
  auto_dm/accounts/<name>/.env 每个账号独立的凭证文件

注意：已取消「默认账号」概念——所有账号都必须通过「新增账号」才能被管理，
不存在根 .env 默认账号，也没有任何隐式回退。无账号时相关函数返回 None。

调用方（run.py / auth_helper / login_api）统一通过 env_path 参数指定要读写的
.env 文件，从而实现账号切换。
"""

import os
import json
import time
from dotenv import load_dotenv, dotenv_values

from auto_dm.vbrowser import app_root  # 统一应用根：源码态=项目根，打包态=exe 所在目录

_ROOT = app_root()  # DY_Spider_base（源码态）/ exe 所在目录（打包态，随附资源根）
_ACCOUNTS_DIR = os.path.join(_ROOT, "auto_dm", "accounts")
_INDEX_PATH = os.path.join(_ACCOUNTS_DIR, "accounts.json")

# 探活结果缓存（避免频繁请求）：name -> (ts, ok, info)
_status_cache = {}


def _ensure_dirs():
    os.makedirs(_ACCOUNTS_DIR, exist_ok=True)


def _load_index():
    """加载账号索引。

    新设计：取消「默认账号」概念——不存在任何账号时返回空索引
    （{"current": null, "accounts": {}}）。所有账号都必须通过「新增账号」才能被管理。
    """
    _ensure_dirs()
    if not os.path.exists(_INDEX_PATH):
        return {"current": None, "accounts": {}}
    try:
        with open(_INDEX_PATH, "r", encoding="utf-8") as f:
            idx = json.load(f)
    except Exception:
        return {"current": None, "accounts": {}}

    # 归一化：旧版可能把 rel 存成 "accounts/<name>/.env"（带前缀），
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
    # 若 current 指向不存在的账号，置空（取消默认账号后不允许落到不存在的账号）
    if idx.get("current") and idx["current"] not in idx.get("accounts", {}):
        idx["current"] = None
    if fixed:
        _save_index(idx)
    return idx


def _save_index(idx):
    _ensure_dirs()
    with open(_INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)


def list_accounts():
    """返回 [(name, env_path), ...]，env_path 为绝对路径。

    取消默认账号：仅返回索引中通过「新增账号」登记的账号，不再注入根 .env。
    """
    idx = _load_index()
    out = []
    for name, rel in idx.get("accounts", {}).items():
        out.append((name, os.path.join(_ACCOUNTS_DIR, rel)))
    return out


def current_name():
    """当前选中账号名；无任何账号时返回 None。"""
    return _load_index().get("current")


def current_env_path():
    """当前选中账号（current，作为默认监测/发送账号）的 .env 绝对路径。
    若无当前账号返回 None。
    """
    idx = _load_index()
    name = idx.get("current")
    if not name:
        return None
    rel = idx.get("accounts", {}).get(name)
    if not rel:
        return None
    return os.path.join(_ACCOUNTS_DIR, rel)


def _env_path_of(name):
    idx = _load_index()
    rel = idx.get("accounts", {}).get(name)
    if not rel:
        return None
    return os.path.join(_ACCOUNTS_DIR, rel)


def env_path_of(name):
    """公开别名：根据账号名取 .env 绝对路径。"""
    return _env_path_of(name)


# 每账号守护端口分配：用账号名稳定哈希，保证每个账号的
# 凭证守护(browser_daemon)与私信守护(recv_daemon)有各自唯一、稳定的端口，
# 从而实现「每个账号独立启停守护」，互不冲突。
# 范围 [10000, 10999]，避开 9911/9912/8765/8877/8080 等常用端口。
_BPORT_BASE = 10000
_RPORT_BASE = 10500
_BPORT_SPAN = 500   # browser 端口段 [10000,10499]
_RPORT_SPAN = 500   # recv 端口段 [10500,10999]


def _stable_port(name, base, span):
    # 用 zlib.crc32 稳定哈希：不依赖 PYTHONHASHSEED（hash() 跨进程随机，会导致
    # web_bridge 算的端口与 subprocess 拉起的守护进程算的端口不一致）。
    try:
        import zlib
        h = zlib.crc32((name or "").encode("utf-8")) % span
    except Exception:
        h = 0
    return base + h


def browser_daemon_port(name=None):
    """该账号的凭证守护(browser_daemon)专属端口（稳定分配）。"""
    name = name or current_name()
    return _stable_port(name, _BPORT_BASE, _BPORT_SPAN)


def recv_daemon_port(name=None):
    """该账号的私信守护(recv_daemon)专属端口（稳定分配）。"""
    name = name or current_name()
    return _stable_port(name, _RPORT_BASE, _RPORT_SPAN)


def _port_open(port, timeout=0.5):
    """快速探测本机端口是否有进程监听（短超时）。"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def verify_account(name=None, timeout=8, dm_loopback=False):
    """双引擎校验：分别判定 wp 引擎（凭证守护捕获）与私信引擎（可拉取私信列表）是否正常。

    判定来源改为「守护进程」，而非纯网络探活：
      - wp 引擎：① 该账号凭证守护(browser_daemon)是否在跑；
                 ② 守护保活的凭证能否被 _load_auth_from_env 还原出完整签名四件套
                   （ticket/ts_sign/sdk_cert/web_protect，即“捕获正常”）。
      - 私信引擎：用 DouyinAPI.get_conversation_list 拉取全部私信会话列表，
                  成功则说明私信凭证（imapi 私有网关签名）有效、列表可读取。

    dm_loopback=False 时只做 wp 引擎+uid 探活（getAccounts 轮询调用，不污染私信）；
    dm_loopback=True 时额外触发私信引擎列表拉取测试（点「校验」按钮时调用）。

    返回 {ok, wp:{level,label,detail}, dm:{level,label,detail}, uid}。
    """
    name = name or current_name()
    env_path = env_path_of(name)
    bport = browser_daemon_port(name)

    result = {
        "ok": False,
        "uid": None,
        "wp": {"level": "unknown", "label": "未校验", "detail": ""},
        "dm": {"level": "unknown", "label": "未校验", "detail": ""},
    }

    # ---- wp 引擎校验 ----
    try:
        from dy_apis.login_api import DYLoginApi
        if not _port_open(bport, timeout=1.0):
            result["wp"] = {
                "level": "stopped",
                "label": "凭证守护未运行",
                "detail": f"该账号凭证守护(browser_daemon)未启动，无法确认 wp 捕获状态。请先启动凭证守护。",
            }
        else:
            auth = DYLoginApi._load_auth_from_env(env_path)
            uid = None
            try:
                from dy_apis.douyin_api import DouyinAPI
                uid = DouyinAPI.get_my_uid(auth)
            except Exception:
                uid = None
            if uid:
                result["uid"] = uid
            # 检查 wp 引擎“捕获”的关键签名：web_protect/keys 四件套是否齐全
            _has_sign = bool(auth.ticket and auth.ts_sign and auth.client_cert
                             and auth.private_key and (getattr(auth, "web_protect_str", None)
                                                       or getattr(auth, "ree_public_key", None)))
            if uid and _has_sign:
                result["wp"] = {
                    "level": "ok",
                    "label": "正常（捕获齐全）",
                    "detail": f"凭证守护运行中，wp 签名四件套已捕获(uid={uid})。",
                }
            elif _has_sign and not uid:
                # 有签名但探活(uid)失败：进一步判断是“风控需重新授权”还是“网络抖动”
                _cookie = getattr(auth, "cookie", None) or {}
                _sv = _cookie.get("s_v_web_id") or ""
                _no_wpsign = not getattr(auth, "web_protect_str", None)
                _is_verify_page = _sv.startswith("verify_") or _sv.startswith("verify_msppk8gp")
                if _is_verify_page or _no_wpsign:
                    result["wp"] = {
                        "level": "fail",
                        "label": "需重新授权（账号疑似风控）",
                        "detail": "wp 签名四件套虽已捕获，但 cookie 中 s_v_web_id 为风控验证页占位值"
                                  "（%s）或 web_protect 未持久化，get_my_uid 探活失败。"
                                  "请点「重新获取凭证」重新扫码，确保在正常网络/设备下完成登录"
                                  "（避开抖音风控验证页）。" % _sv[:24],
                    }
                else:
                    result["wp"] = {
                        "level": "warn",
                        "label": "捕获齐全但探活失败",
                        "detail": "wp 签名四件套已捕获，但 get_my_uid 探活失败（守护可能刚重启/网络抖动），可稍后重试引擎校验。",
                    }
            elif uid and not _has_sign:
                result["wp"] = {
                    "level": "warn",
                    "label": "已登录但签名缺失",
                    "detail": f"已登录(uid={uid})但 wp 签名四件套缺失，请重新获取凭证以抓 web_protect/keys。",
                }
            else:
                result["wp"] = {
                    "level": "fail",
                    "label": "凭证无效",
                    "detail": "凭证守护运行中，但登录态与签名均缺失，请重新获取凭证。",
                }
    except Exception as e:
        result["wp"] = {
            "level": "error",
            "label": "校验异常",
            "detail": f"wp 引擎校验抛出异常: {e}",
        }

    # ---- 私信引擎校验（拉取私信列表，仅 dm_loopback=True 时执行）----
    if dm_loopback:
        try:
            from dy_apis.login_api import DYLoginApi
            from dy_apis.douyin_api import DouyinAPI
            auth = DYLoginApi._load_auth_from_env(env_path)
            uid = result["uid"] or DouyinAPI.get_my_uid(auth)
            if not uid:
                result["dm"] = {
                    "level": "skip",
                    "label": "跳过（无 uid）",
                    "detail": "无法获取自身 uid，私信列表拉取测试跳过。请先确保 wp 引擎已登录。",
                }
            else:
                convs = DouyinAPI.get_conversation_list(auth)
                result["dm"] = {
                    "level": "ok",
                    "label": "列表可拉取",
                    "detail": f"已成功拉取私信会话列表（共 {len(convs)} 个会话），"
                              f"私信凭证（imapi 签名）有效。",
                }
        except Exception as e:
            result["dm"] = {
                "level": "fail",
                "label": "列表拉取失败",
                "detail": f"拉取私信列表失败: {e}",
            }
    else:
        # 轮询场景：私信引擎状态沿用“接收守护是否在跑”做轻量标注
        result["dm"] = {
            "level": "idle",
            "label": "待校验",
            "detail": "点击账号卡片「引擎校验」按钮可触发私信引擎列表拉取测试。",
        }

    result["ok"] = (result["wp"]["level"] in ("ok", "warn")
                    and result["dm"]["level"] in ("ok", "warn", "skip"))
    return result


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
    """监测账号（用于直播间监听弹幕，需管理器权限才能看到完整昵称）。
    取消默认账号：若无绑定账号返回 None。
    """
    idx = _load_index()
    name = idx.get("monitor") or idx.get("current")
    if not name or name not in idx.get("accounts", {}):
        return None
    return name


def sender_name():
    """发送账号（用于私信发送，需有私信权限）。
    取消默认账号：若无绑定账号返回 None。
    """
    idx = _load_index()
    name = idx.get("sender") or idx.get("current")
    if not name or name not in idx.get("accounts", {}):
        return None
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
    try:
        for name, env_path in list_accounts():
            if clear_credentials_of(env_path):
                cleared += 1
    except Exception:
        pass
    logger.info(f"[账号] 软件退出：已清空 {cleared} 个账号的登录凭证（日志/配置/账号结构保留）")
    return cleared


def remove_account(name):
    """删除账号（同时删除其 .env 目录）。所有账号都需通过新增创建，故均可删除。"""
    idx = _load_index()
    if name not in idx.get("accounts", {}):
        raise ValueError(f"账号不存在: {name}")
    rel = idx["accounts"].pop(name)
    env_path = os.path.join(_ACCOUNTS_DIR, rel)
    try:
        if os.path.isdir(os.path.dirname(env_path)):
            import shutil
            shutil.rmtree(os.path.dirname(env_path))
    except Exception:
        pass
    # current/monitor/sender 若指向被删账号则清空
    for k in ("current", "monitor", "sender"):
        if idx.get(k) == name:
            idx[k] = None
    _save_index(idx)


def _read_status(env_path):
    """读取该 env 的签名配置情况（不探活，纯本地）。

    用 dotenv_values 显式从 .env 文件读键，避免 os.getenv 读到进程级残留的
    其他账号环境变量（否则空 .env 的账号会误读到上一个账号的 DY_COOKIES/TICKET，
    导致跨账号 UID 重复、凭证误判）。
    """
    if not os.path.exists(env_path):
        return {"exists": False, "has_ticket": False, "has_private_key": False,
                "has_cookie": False, "has_web_protect": False}
    vals = dotenv_values(env_path)
    ticket = vals.get("DY_TICKET")
    pkey = vals.get("DY_PRIVATE_KEY")
    cookie = vals.get("DY_COOKIES")
    webp = vals.get("DY_WEB_PROTECT")
    keys = vals.get("DY_KEYS")
    # web_protect/keys 是抖音私信 IM 私有网关签名（即用户所说“wp 凭证”）的关键，
    # 与 ticket/私钥相互独立，需单独暴露，便于账户管理页区分“wp 凭证失效”与“私信签名失效”。
    has_web_protect = bool(webp) and bool(keys)
    return {
        "exists": True,
        "has_ticket": bool(ticket),
        "has_private_key": bool(pkey),
        "has_cookie": bool(cookie),
        "has_web_protect": has_web_protect,
    }


def account_status(name=None, force=False, timeout=10):
    """返回账号状态字典：包含签名存在性与探活结果。

    与 verify_account（启动自检 / 账号卡片双引擎校验）共用【同一真实探活】逻辑，
    杜绝“自检通过但账号管理页失败”的不一致：二者都基于
      「凭证守护是否运行 + 文件签名四件套齐全 + 向抖音服务端真实探活 get_my_uid 成功」
    来判定是否有效。本函数直接委托 verify_account，保证两处结论完全一致。

    timeout: 探活（get_my_uid）超时秒数。
    """
    if name is None:
        name = current_name()
    if not name:
        return {"name": None, "env": None, "level": "missing",
                "label": "无账号（请先新增）", "alive": False,
                "has_ticket": False, "has_private_key": False,
                "has_cookie": False, "has_web_protect": False}
    env_path = env_path_of(name)
    if not env_path:
        return {"name": name, "env": None, "level": "missing",
                "label": "账号不存在（请先新增）", "alive": False,
                "has_ticket": False, "has_private_key": False,
                "has_cookie": False, "has_web_protect": False}

    # 委托 verify_account 作为唯一真相源（真实探活）
    v = verify_account(name, timeout=timeout, dm_loopback=False)
    wp = v.get("wp", {})
    wp_level = wp.get("level")
    level = "ok" if wp_level == "ok" else (
        "nosign" if wp_level == "nosign" else (
            "expired" if wp_level in ("fail", "error") else "missing"
        )
    )
    # 透传 has_* 字段（供前端细粒度展示）
    local = _read_status(env_path)
    return {
        "name": name,
        "env": env_path,
        "level": level,
        "label": wp.get("label", "未知"),
        "alive": wp_level == "ok",
        "has_ticket": bool(local.get("has_ticket")),
        "has_private_key": bool(local.get("has_private_key")),
        "has_cookie": bool(local.get("has_cookie")),
        "has_web_protect": bool(local.get("has_web_protect")),
        "uid": v.get("uid"),
    }


def _probe(env_path, timeout):
    """用 get_my_uid 探活。返回 (ok, uid_or_error)。"""
    try:
        from builder.auth import DouyinAuth
        from dy_apis.douyin_api import DouyinAPI
        import threading

        def worker():
            try:
                # 用 dotenv_values 显式读该账号 .env，避免 os.getenv 读到进程级
                # 残留的其他账号环境变量导致跨账号 UID 重复。
                from dy_apis.login_api import DYLoginApi as _DL
                vals = dotenv_values(env_path)
                web_protect = vals.get("DY_WEB_PROTECT") or ""
                keys = vals.get("DY_KEYS") or ""
                auth = DouyinAuth()
                # 与 _load_auth_from_env 完全对齐：优先用持久化的 web_protect/keys
                # （含 ree_public_key 等派生签名）重建 auth；仅旧 .env 无这两个键时
                # 退回四件套 + 手动补 ree_public_key。否则仅用四件套自建会话会被
                # 服务端 SIGN_REJECTED，误判“失效（私信签名被服务端拒绝）”。
                auth.perepare_auth(vals.get("DY_COOKIES", "") or "", web_protect, keys)
                if not (web_protect and keys):
                    auth.ticket = vals.get("DY_TICKET") or None
                    auth.ts_sign = vals.get("DY_TS_SIGN") or None
                    auth.client_cert = vals.get("DY_CLIENT_CERT") or None
                    auth.private_key = _DL._decode_private_key(vals.get("DY_PRIVATE_KEY") or "")
                    if auth.private_key:
                        import base64 as _b64
                        auth.ree_public_key = _b64.b64encode(auth.private_key.encode()).decode()
                else:
                    auth.ticket = vals.get("DY_TICKET") or None
                    auth.private_key = _DL._decode_private_key(vals.get("DY_PRIVATE_KEY") or "")
                if not getattr(auth, "cookie_str", None) and auth.cookie:
                    auth.cookie_str = "; ".join(f"{k}={v}" for k, v in auth.cookie.items())
                uid = DouyinAPI.get_my_uid(auth)
                if not uid:
                    return None
                # 严格校验：对自身 uid 建会话，验证服务端是否真的接受私信签名(web_protect/keys)。
                # 与启动 _verify_credential 一致，避免“四件套在但服务端拒绝签名”被误判为有效。
                try:
                    DouyinAPI.create_conversation(auth, int(uid))
                except Exception:
                    return "SIGN_REJECTED"
                return uid
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


# ===== 私信凭证失效自动重新捕获（V2 新增）=====
# 触发场景：私信发送链路检测到凭证失效类错误（三件套缺失 / INVALID_REQUEST / KICK）
# 时，后端自动拉起指纹浏览器打开 chat?isPopup=1 重新捕获 web_protect/keys，写回 .env。
# 节流：同一账号 5 分钟内只弹一次，避免高频失败反复弹窗打扰用户。
_RECAP_INTERVAL = 300  # 秒
_recap_state: dict = {}  # name -> {"running": bool, "last": float, "error": str}


def auto_recapture(name: str = None, landing_url: str = "https://www.douyin.com/chat?isPopup=1"):
    """私信凭证失效时自动重新捕获（best-effort，不阻塞调用方）。

    - name 缺省时用当前账号（current）。
    - 带节流守卫：同账号 5 分钟内只触发一次；已有捕获在跑则跳过。
    - 改造点：调用方（sender）在发送失败时调本函数，由后台线程弹指纹浏览器
      打开 chat?isPopup=1 重新授权，捕获成功后写回 .env，后续发送复用新凭证。
    """
    try:
        if not name:
            name = current_name()
        st = _recap_state.setdefault(name, {"running": False, "last": 0.0, "error": ""})
        now = time.time()
        # 节流：运行中 或 距上次触发不足间隔，则跳过
        if st["running"] or (now - st["last"]) < _RECAP_INTERVAL:
            logger.debug(f"[recap] 账号 {name} 自动重捕获跳过（节流/进行中）")
            return
        st["last"] = now
        t = threading.Thread(target=_do_auto_recapture, args=(name, landing_url), daemon=True)
        t.start()
    except Exception as e:
        logger.warning(f"[recap] 账号 {name} 发起自动重捕获失败: {e}")


def _do_auto_recapture(name: str, landing_url: str):
    """后台线程：停守护释放 profile 锁 → 强制重扫（打开 chat?isPopup=1）→ 写回 .env。"""
    st = _recap_state.setdefault(name, {"running": False, "last": 0.0, "error": ""})
    st["running"] = True
    st["error"] = ""
    try:
        # 复用 api 层的停守护逻辑（向该账号凭证守护端口发 /quit，释放 Chromium profile 锁）
        try:
            from api.accounts import _quit_browser_daemon
            _quit_browser_daemon(name)
        except Exception as e:
            logger.warning(f"[recap] 账号 {name} 停止凭证守护失败（可能未运行）: {e}")
        from auth_helper import enrich_auth
        env_path = env_path_of(name)
        logger.info(f"[recap] 账号 {name} 私信凭证失效，自动拉起指纹浏览器重新捕获（{landing_url}）")
        auth, _ = enrich_auth(None, force=True, env_path=env_path, landing_url=landing_url)
        st["error"] = "" if getattr(auth, "cookie", None) else "捕获未完成（未拿到登录态）"
        logger.success(f"[recap] 账号 {name} 自动重新捕获完成")
    except Exception as e:
        st["error"] = str(e)
        logger.error(f"[recap] 账号 {name} 自动重新捕获异常: {e}")
    finally:
        st["running"] = False
