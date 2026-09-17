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
import asyncio
import platform
import subprocess
import threading
from dotenv import load_dotenv, dotenv_values
from loguru import logger

from auto_dm.vbrowser import app_root  # 统一应用根：源码态=项目根，打包态=exe 所在目录

_ROOT = app_root()  # DY_Spider_base（源码态）/ exe 所在目录（打包态，随附资源根）

# 会员体系（v0.37.0）：登录后账号目录切到会员数据空间
# <app_root>/members/<member_id>/auto_dm/accounts，各会员完全隔离；
# 未登录时保持原路径（老数据兼容）。当前会员身份由 DY_MEMBER 环境变量
# （backend 登录后注入）或运行时上下文提供。
def _member_accounts_dir():
    try:
        from services import member_ctx
        d = member_ctx.accounts_root()
        if d:
            return d
    except Exception:
        pass
    return None

_MEMBER_ACC_DIR = _member_accounts_dir()
_ACCOUNTS_DIR = _MEMBER_ACC_DIR or os.path.join(_ROOT, "auto_dm", "accounts")
_INDEX_PATH = os.path.join(_ACCOUNTS_DIR, "accounts.json")

def _accounts_dir() -> str:
    """每次调用动态解析账号根目录（会员登录后切到会员数据空间）。"""
    d = _member_accounts_dir()
    if d:
        return d
    return _ACCOUNTS_DIR

# 探活结果缓存（避免频繁请求）：name -> (ts, ok, info)
_status_cache = {}


def _ensure_dirs():
    os.makedirs(_accounts_dir(), exist_ok=True)


def _load_index():
    """加载账号索引（从 SQLite kv_store，替代 accounts.json）。

    新设计：取消「默认账号」概念——不存在任何账号时返回空索引
    （{"current": null, "accounts": {}}）。所有账号都必须通过「新增账号」才能被管理。
    """
    _ensure_dirs()
    try:
        from database import get_kv_json
        idx = get_kv_json("accounts_index", {"current": None, "accounts": {}})
        if idx is None:
            idx = {"current": None, "accounts": {}}
    except Exception:
        idx = {"current": None, "accounts": {}}

    # 归一化：旧版可能把 rel 存成 "accounts/<name>/.env"（带前缀），
    # 与 _accounts_dir() 拼接会变成双重 accounts 前缀。这里归一化为 "<name>/.env"，
    # 并把旧错误路径下已生成的 .env 文件迁移到正确位置。
    fixed = False
    for name, rel in list(idx.get("accounts", {}).items()):
        if rel.startswith("accounts/") or rel.startswith("accounts\\"):
            clean_rel = rel[len("accounts/"):] if rel.startswith("accounts/") else rel[len("accounts\\"):]
            bad_path = os.path.join(_accounts_dir(), rel)
            good_path = os.path.join(_accounts_dir(), clean_rel)
            if os.path.exists(bad_path) and not os.path.exists(good_path):
                os.makedirs(os.path.dirname(good_path), exist_ok=True)
                try:
                    os.replace(bad_path, good_path)
                except Exception:
                    pass
                # 清理旧的双重前缀目录
                try:
                    bad_dir = os.path.dirname(bad_path)
                    if bad_dir.startswith(_accounts_dir()) and os.path.isdir(bad_dir):
                        # 2026-09-17 修补（审查 P2-2）：改用带越界校验的
                        # 安全删除（原为裸 shutil.rmtree）。
                        _rmtree_account_dir(bad_dir)
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
    """保存账号索引到 SQLite kv_store（替代 accounts.json）。"""
    try:
        from database import set_kv_json
        set_kv_json("accounts_index", idx)
    except Exception as e:
        logger.warning(f"[ACC-006] " + f"[accounts] 保存账号索引失败: {e}")


def list_accounts():
    """返回 [(name, env_path), ...]，env_path 为绝对路径。

    取消默认账号：仅返回索引中通过「新增账号」登记的账号，不再注入根 .env。
    """
    idx = _load_index()
    out = []
    for name, rel in idx.get("accounts", {}).items():
        out.append((name, os.path.join(_accounts_dir(), rel)))
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
    return os.path.join(_accounts_dir(), rel)


def _env_path_of(name):
    idx = _load_index()
    rel = idx.get("accounts", {}).get(name)
    if not rel:
        return None
    return os.path.join(_accounts_dir(), rel)


def env_path_of(name):
    """公开别名：根据账号名取 .env 绝对路径。"""
    return _env_path_of(name)


def name_of_env_path(env_path):
    """P1-B 辅助：.env 绝对路径反查账号名（accounts/<name>/.env 形态）。

    sender 经 recv_daemon /send_by_uid 直发时需要 account 名定位端口；
    非 accounts/<name>/.env 形态（开发调试态）返回 None。
    """
    if not env_path:
        return None
    try:
        p = os.path.abspath(env_path)
        parent = os.path.basename(os.path.dirname(p))
        if parent and parent != "accounts":
            return parent
    except Exception:
        pass
    return None


# 每账号守护端口分配：用账号名稳定哈希，保证每个账号的
# 凭证守护(browser_daemon)与私信守护(recv_daemon)有各自唯一、稳定的端口，
# 从而实现「每个账号独立启停守护」，互不冲突。
# 2026-09-06 全局治理（2.2B 扩大 span）：与 config.py 同步，500 槽位
# 20 账号碰撞率 ≈32%，扩到 2000 槽位后降到 ≈9.1%。
# 范围 [10000, 13999]。
_BPORT_BASE = 10000
_RPORT_BASE = 12000
_BPORT_SPAN = 2000  # browser 端口段 [10000,11999]
_RPORT_SPAN = 2000  # recv 端口段 [12000,13999]


def _stable_port(name, base, span, salt=""):
    """基于账号名的稳定端口分配。

    2026-09-06 全局治理（端口碰撞修复）：
    原实现对 browser / recv 用**同一个 crc32(name) 值**，只是 base 不同
    （10000 vs 10500）。这意味着两个守护的端口偏移【完全同步】——
    一旦某账号在 browser 段撞车，它在 recv 段必然也撞车，且是
    「同一对账号互撞」，排查时表现为两个账号的守护互相串号。

    加 salt 让 browser / recv 使用**不同的哈希输入**，即使 crc32 值相同
    也可通过 salt 错开偏移，打破同步性。salt 只为区分用途，
    不改变「同账号同名 → 同端口」的稳定性（向后兼容）。

    注：500 槽位下 20 账号碰撞率约 32%（生日悖论），这是 span 的固有限制；
    salt 解决的是「browser/recv 同步撞车」，彻底解决需扩大 span（待评估）。
    """
    # 用 zlib.crc32 稳定哈希：不依赖 PYTHONHASHSEED（hash() 跨进程随机，会导致
    # web_bridge 算的端口与 subprocess 拉起的守护进程算的端口不一致）。
    try:
        import zlib
        h = zlib.crc32(((name or "") + salt).encode("utf-8")) % span
    except Exception:
        h = 0
    return base + h


def browser_daemon_port(name=None):
    """该账号的凭证守护(browser_daemon)专属端口（稳定分配）。"""
    name = name or current_name()
    return _stable_port(name, _BPORT_BASE, _BPORT_SPAN, salt="|bcc")


def recv_daemon_port(name=None):
    """该账号的私信守护(recv_daemon)专属端口（稳定分配）。

    2026-09-06：加 salt="|recv" 与 browser 段错开哈希输入，
    避免两守护端口偏移同步（详见 _stable_port 说明）。
    """
    name = name or current_name()
    return _stable_port(name, _RPORT_BASE, _RPORT_SPAN, salt="|recv")


# 2026-09-06 BCC 懒加载（用户架构决策：启动不拉 BCC，按需自动拉起）。
# spawn 状态去重：并发懒加载只拉一次（模块级锁 + 记录已拉起端口）。
# 启动冷静期：进程启动后 DY_BCC_LAZY_DELAY 秒内禁止懒加载 BCC（防启动时
# 快闪唤醒），之后才接受懒加载。
from datetime import datetime as _datetime
_PROCESS_START_TS: "_datetime" = _datetime.now()
_bcc_lazy_lock = threading.Lock()
_bcc_lazy_spawned: set[str] = set()



# ── 2026-09-14 v0.43.8：BCC「用户主动停止」状态 ──────────────────────
# 背景：v0.43.6 改为「随启动拉起」后，任何 API 请求经 ensure_bcc /
# ensure_daemons_for / browser_gate 都会发现 BCC 不在 → 立刻重新拉起，
# 导致用户「刚关掉又被拉起」——关不掉。
# 修法：把「停止」提升为一等状态。停止后所有自动路径只读不拉，
# 直到用户显式恢复（UI 启动守护 / 删除标记 / 重启应用）。
_BCC_USER_STOPPED: bool = False   # 进程内状态（本进程生命周期有效）


def bcc_user_stopped() -> bool:
    """BCC 是否被用户主动停止（进程内状态 + 持久化标记文件 双判据）。"""
    if _BCC_USER_STOPPED:
        return True
    try:
        import app_root as _ar
        _root = _ar.app_root()
    except Exception:
        _root = os.environ.get("DY_APP_ROOT", "") or os.getcwd()
    try:
        return os.path.exists(os.path.join(_root, ".bcc_user_stopped"))
    except Exception:
        return False


def bcc_mark_user_stopped(stopped: bool = True) -> None:
    """用户显式停止/恢复 BCC：写进程内状态 + 持久化标记文件。"""
    global _BCC_USER_STOPPED
    _BCC_USER_STOPPED = bool(stopped)
    try:
        import app_root as _ar
        _root = _ar.app_root()
    except Exception:
        _root = os.environ.get("DY_APP_ROOT", "") or os.getcwd()
    _fp = os.path.join(_root, ".bcc_user_stopped")
    try:
        if stopped:
            # 2026-09-17 修补（OCR 审查 HIGH）：原为 `io.open(...)` 但本文件
            # **未导入 io** → 每次都抛 NameError，被下面的 except 静默吞掉
            # ⇒ `.bcc_user_stopped` 标记**永不写入**，进程重启后
            # `bcc_user_stopped()` 读不到标记，会无视用户「已停止 BCC」的
            # 显式意图而自动拉起。改用内置 open（与 io.open 等价）。
            with open(_fp, "w", encoding="utf-8") as _fh:
                _fh.write("1")
        else:
            if os.path.exists(_fp):
                os.remove(_fp)
    except Exception:
        pass

def ensure_bcc(name=None, wait_ready: bool = True, timeout: float = 45,
               skip_cooldown: bool = False) -> dict:
    """确保该账号的 BCC 正在运行；不在则拉起并等端口就绪（懒加载）。

    消费者（WP 发送 / 更新会话 / 昵称捕获）在调用 BCC 接口前先调本函数。
    已运行 -> 直接返回；未运行 -> spawn 后轮询端口（onefile 冷启动 ~15s）。
    skip_cooldown=True：豁免启动冷静期（预对齐路径专用——启动时主动拉齐
    BCC 正是冷静期当初要防的「快闪唤醒」的有意形式，非误触发）。
    返回 {"ok": bool, "port": int|None, "msg": str}。
    """
    name = name or current_name()
    port = browser_daemon_port(name)
    # 2026-09-14 v0.43.8：用户主动停止后，自动路径**绝不**重新拉起。
    if bcc_user_stopped():
        return {"ok": False, "port": None,
                "msg": "BCC 已被用户停止（自动拉起已禁用）；请从界面显式启动"}
    if _port_open(port, timeout=0.3):
        return {"ok": True, "port": port, "msg": "已在运行"}
    # 2026-09-06 启动冷静期（防「启动时 BCC 快闪唤醒」）：
    # 后端进程启动后 30s 内禁止懒加载 BCC —— 给前端 / 启动期所有路径
    # （getAccounts/凭证校验/update_account）充分时间完成，期间只读不拉
    # 进程；30s 后才接受懒加载。
    # 同时也覆盖环境变量 DY_BCC_LAZY_DELAY（秒），便于测试 / 紧急回退。
    # 2026-09-08：预对齐路径（启动即拉齐守护，skip_cooldown=True）豁免——
    # 该路径是用户明确要求「登录前对齐」的实现，不是误触发。
    if not skip_cooldown:
        try:
            # 2026-09-13：默认 30 → **0**（BCC 已改为随启动拉起，冷静期无存在
            # 必要）。保留环境变量以便极端场景回退；>0 时**用户显式操作会被
            # browser_gate 自动豁免**（见 _SKIP_COOLDOWN_BY_PURPOSE）。
            _delay = int(os.environ.get("DY_BCC_LAZY_DELAY", "0"))
        except Exception:
            _delay = 30
        if _delay > 0:
            from datetime import datetime as _dt
            _elapsed = (_dt.now() - _PROCESS_START_TS).total_seconds()
            if _elapsed < _delay:
                logger.debug(f"[bcc-lazy] 启动冷静期（{_elapsed:.1f}s/{_delay}s）跳过 BCC 懒加载")
                return {"ok": False, "port": None,
                        "msg": f"启动冷静期（{_delay}s）内不自动拉 BCC，请稍后再试"}
    with _bcc_lazy_lock:
        # 双检：等锁期间可能已被并发拉起
        if _port_open(port, timeout=0.3):
            return {"ok": True, "port": port, "msg": "已在运行"}
        # ⚠️ 部署位置铁律（2026-09-13）：sidecar 一律在【应用根目录】，
        # 禁止 <root>/binaries/（仅对被删除的历史部署保留容错）。
        _full = "dyautodm-browser-daemon-x86_64-pc-windows-msvc"
        binary = ""
        for _cand in (
            # 1) 标准：应用根目录（onedir 目录 → 单文件 → 无 triple 别名）
            os.path.join(_ROOT, _full, f"{_full}.exe"),
            os.path.join(_ROOT, f"{_full}.exe"),
            os.path.join(_ROOT, "dyautodm-browser-daemon.exe"),
            # 2) 兼容历史 binaries/ 部署（仅容错）
            os.path.join(_ROOT, "binaries", _full, f"{_full}.exe"),
            os.path.join(_ROOT, "binaries", f"{_full}.exe"),
            # 3) 开发态：源码树 src-tauri/binaries
            os.path.join(_ROOT, "src-tauri", "binaries", _full, f"{_full}.exe"),
            os.path.join(_ROOT, "src-tauri", "binaries", f"{_full}.exe"),
        ):
            if os.path.isfile(_cand):
                binary = _cand
                break
        if not binary:
            return {"ok": False, "port": None,
                    "msg": f"BCC 二进制不存在（应在应用根目录 {_ROOT}）"}
        if name in _bcc_lazy_spawned:
            # 之前拉过但端口没开 -> 上次失败，不重复拉（防循环）
            return {"ok": False, "port": None,
                    "msg": "BCC 此前懒加载失败（端口未就绪），请查看 BCC 日志"}
        try:
            kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            # 会员体系（v0.37.0）：子进程继承会员空间（DY_MEMBER）与主密钥（DY_MEMBER_KEY）
            _menv = {k: v for k, v in os.environ.items() if k.startswith("DY_") or k in ("PYTHONPATH", "SYSTEMROOT", "TEMP", "TMP", "COMPUTERNAME", "USERPROFILE")}
            try:
                from services import member_ctx as _mctx
                if _mctx.current():
                    _menv["DY_MEMBER"] = _mctx.current_member_id() or ""
                    _mk = _mctx.master_key()
                    if _mk:
                        _menv["DY_MEMBER_KEY"] = _mk
            except Exception:
                pass
            kwargs["env"] = _menv
            if platform.system() == "Windows":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen([binary, "--account", name, "--port", str(port)], **kwargs)
            _bcc_lazy_spawned.add(name)
            logger.info(f"[bcc-lazy] 已懒加载 BCC account={name} port={port}")
        except Exception as e:
            return {"ok": False, "port": None, "msg": f"BCC 拉起失败: {e}"}
    if wait_ready:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _port_open(port, timeout=0.4):
                return {"ok": True, "port": port, "msg": "懒加载就绪"}
            time.sleep(0.5)
        return {"ok": False, "port": port, "msg": f"BCC 懒加载后 {timeout}s 端口未就绪"}
    return {"ok": True, "port": port, "msg": "已拉起（未等待就绪）"}


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


def verify_account(name=None, timeout=8, dm_loopback=False, auto_fix=True):
    """双引擎校验：分别判定 wp 引擎（凭证是否有效）与私信引擎（可拉取私信列表）是否正常。

    关键原则（单 profile 铁律 #35）：
      - 引擎校验的本质 = 检查「凭证本身是否有效」，【不】监测守护进程是否启动。
      - 守护进程只在凭证有效时才能启动（browser_daemon.main 启动前 credentials_complete
        守卫，不全则 sys.exit(2) 拒绝启动），这是守护自己的前置条件，与校验职责无关。
      - 因此 wp 引擎判定只依赖：.env 凭证能否被 _load_auth_from_env 还原出完整签名
        四件套 + web_protect/keys 是否齐全 + get_my_uid 探活是否成功。
        守护起没起、端口开没开，都不应影响 wp 判定结果。
      - 私信引擎：用 DouyinAPI.get_conversation_list 拉取全部私信会话列表，
                  成功则说明私信凭证（imapi 私有网关签名）有效、列表可读取。

    dm_loopback=False 时只做 wp 引擎+uid 探活（getAccounts 轮询调用，不污染私信）；
    dm_loopback=True 时额外触发私信引擎列表拉取测试（点「校验」按钮时调用）。

    auto_fix=True（默认）时：当 wp 引擎判定为失效/需重新授权（fail/warn/error）
      且 dm_loopback=True（即用户主动校验或启动自检场景），自动触发 auto_recapture
      （弹 headed 指纹浏览器重新加载抖音页面，在已登录态下生成有效 web_protect/keys
      写回 .env），确保凭证有效后才交给守护进程保活。带 5 分钟节流，不会狂弹。
      首次使用无凭证 / 校验发现失效，都会自动唤醒浏览器重捕，无需用户手动点「重新获取凭证」。

    返回 {ok, wp:{level,label,detail}, dm:{level,label,detail}, uid,
          auto_fix_triggered: bool}。
    """
    name = name or current_name()
    env_path = env_path_of(name)

    result = {
        "ok": False,
        "uid": None,
        "wp": {"level": "unknown", "label": "未校验", "detail": ""},
        "dm": {"level": "unknown", "label": "未校验", "detail": ""},
        "auto_fix_triggered": False,
    }

    # ---- wp 引擎校验（只检查凭证是否有效，不依赖守护进程是否启动）----
    # 单 profile 铁律 #35：守护进程只在凭证有效时才能启动，这是守护自己的前置守卫；
    # 引擎校验的职责是判断「凭证本身有没有效」，与守护当前起没起无关。
    # 因此这里直接还原 .env 凭证做静态检查 + 探活，不再用 _port_open 探测守护端口。
    try:
        from dy_apis.login_api import DYLoginApi
        # 先用 credentials_complete 判断 .env 凭证是否齐全（#35 同款守卫逻辑）
        _complete, _reason = credentials_complete(env_path)
        if not _complete:
            result["wp"] = {
                "level": "fail",
                "label": "凭证未就绪",
                "detail": f".env 凭证不全（{_reason}），凭证无效无法用于私信。"
                          f"请先完成扫码获取完整凭证（四件套 + web_protect/keys）。",
            }
        else:
            auth = DYLoginApi._load_auth_from_env(env_path)
            # 2026-09-07 架构重构：UID 探活统一由 services.uid_probe 调度
            # （此前此处直接调 DouyinAPI.get_my_uid，8 处调用点各自打网，
            #  频次不可控）。现在只读取调度器的缓存结果，默认零网络请求。
            uid = None
            try:
                from services.uid_probe import get_uid as _uid_get
                uid = _uid_get(name)
            except Exception:
                uid = None
            if uid:
                result["uid"] = uid
            # 2026-09-06 P1 修复（知识库 08 §24.9 uid 轮换事故）：
            # uid 与 DB 历史会话 uid 交叉验证 —— conv_id 格式
            # `0:1:<uid_a>:<uid_b>`，该账号历史 conv_id 里必然包含其真实 uid。
            # 若本次探活 uid 从未出现在历史 conv_id 中，说明身份已漂移/轮换，
            # Web 接口认新 uid 但 imapi 会话体系仍挂老 uid（本次事故实证），
            # 凭证不可信。仅在该账号 DB 里已有历史会话时才比对（新账号跳过）。
            #
            # 2026-09-07 事实更正：**不存在"双 uid 体系"**。
            # 实测直接问抖音（query/user）：张老师凭证回答 user_uid=
            # 3887506227210423，与其 278/278 条会话完全一致；此前日志里的
            # 4175297014664416 是 **09-04 的陈旧值**（被 uid_probe 缓存复用），
            # 且带着它拉取会话 conv=0（页面级登录态失效）。原判 fail 是对的，
            # 中间那版"双 uid → warn"改动前提错误，已撤回。
            _uid_mismatch = False
            if uid:
                try:
                    from database import get_db
                    _conn = get_db()
                    _rows = _conn.execute(
                        "SELECT conv_id FROM dm_conversations WHERE account=?",
                        (name,)).fetchall()
                    if _rows:
                        _uid_s = str(uid)
                        _uid_mismatch = not any(
                            _uid_s in str(r[0]).split(":") for r in _rows)
                        if _uid_mismatch:
                            logger.error(f"[ACC-007] " + f"[verify] 账号 {name} uid 漂移：探活 uid={uid} "
                                f"不存在于该账号 {len(_rows)} 条历史会话中，"
                                f"凭证身份存疑（疑似身份被轮换/替换）。")
                except Exception as _e:
                    logger.debug(f"[verify] uid 交叉验证跳过（DB 不可用）: {_e}")
            # 检查 wp 引擎“捕获”的关键签名：web_protect/keys 四件套是否齐全
            _has_sign = bool(auth.ticket and auth.ts_sign and auth.client_cert
                             and auth.private_key and (getattr(auth, "web_protect_str", None)
                                                       or getattr(auth, "ree_public_key", None)))
            if uid and _has_sign and _uid_mismatch:
                # 真漂移 → 判 fail（2026-09-07：经实测确认此判定正确，
                # 中间那版"双 uid → warn"的前提不成立，已撤回）
                result["wp"] = {
                    "level": "fail",
                    "label": "uid 漂移（身份存疑）",
                    "detail": f"探活 uid={uid} 与该账号历史会话 uid 不一致——"
                              f"疑似登录身份被轮换/替换，imapi 会话体系仍挂老 uid，"
                              f"私信收发将全部失败。请重新扫码登录该账号。",
                }
            elif uid and _has_sign:
                result["wp"] = {
                    "level": "ok",
                    "label": "正常（捕获齐全）",
                    "detail": f"凭证有效，wp 签名四件套已捕获(uid={uid})。",
                }
            elif _has_sign and not uid:
                # 有签名但探活(uid)失败：多为网络抖动/账号偶发风控。
                # 注意：不再以 s_v_web_id='verify_' 开头判“需重新授权”——抖音新版
                # s_v_web_id 正常值就是 'verify_' 开头（基座直测实证），不再据此提示风控。
                result["wp"] = {
                    "level": "warn",
                    "label": "捕获齐全但探活失败",
                    "detail": "wp 签名四件套已捕获，但 get_my_uid 探活失败"
                              "（网络抖动/账号偶发风控），"
                              "可稍后重试引擎校验；私信凭证可能仍可用。",
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
                    "detail": "登录态与签名均缺失，请重新获取凭证。",
                }
    except Exception as e:
        result["wp"] = {
            "level": "error",
            "label": "校验异常",
            "detail": f"wp 引擎校验抛出异常: {e}",
        }

    # ---- 自动重捕（方案 A：确保凭证有效后才交守护）----
    # 仅当用户主动校验/启动自检（dm_loopback=True）且 wp 引擎判定凭证失效/需重授权时触发。
    # 弹 headed 指纹浏览器重新加载抖音页面，在已登录态下生成有效 web_protect/keys 写回 .env。
    # 带 5 分钟节流（auto_recapture 内部守卫），不会反复弹窗。
    _wp_level = result["wp"].get("level")
    if auto_fix and dm_loopback and _wp_level in ("fail", "warn", "error"):
        try:
            logger.info(
                f"[verify] 账号 {name} wp 引擎判定 {_wp_level}，自动唤醒指纹浏览器重捕凭证"
            )
            # 优先：从绑定指纹浏览器的【持久化 profile】直接读取有效凭证（复用已登录态、不重扫、
            # 保持浏览器打开直到读到 clean 凭证；遇验证码污染则提示手动处理并持续监测）。
            # 仅当读取失败（profile 自身也失效）时，再 fallback 到强制重扫（force=True）。
            _fixed_ok = False
            try:
                _fixed_ok, _fixed_msg = recapture_from_profile(
                    name, landing_url="https://www.douyin.com/chat?isPopup=1")
            except Exception as _e:
                _fixed_msg = f"从 profile 读取失败: {_e}"
            if not _fixed_ok:
                # fallback：强制重扫（打开 chat?isPopup=1 重新授权）
                logger.info(f"[verify] 账号 {name} 从 profile 读取未成功（{_fixed_msg}），"
                            f"降级为强制重扫")
                auto_recapture(name, landing_url="https://www.douyin.com/chat?isPopup=1")
                _fixed_msg = (_fixed_msg + "；已降级为强制重扫，请在指纹浏览器完成重新授权。"
                              if _fixed_msg else "已降级为强制重扫，请在指纹浏览器完成重新授权。")
            result["auto_fix_triggered"] = True
            _old_label = result["wp"].get("label", "")
            result["wp"]["detail"] = (
                "已自动唤醒该账户绑定的指纹浏览器重新捕获凭证：" + _fixed_msg +
                " 捕获成功后凭证将写回 .env 并交守护进程保活。原始判定：" + _old_label
            )
        except Exception as e:
            logger.warning(f"[ACC-008] " + f"[verify] 账号 {name} 自动重捕触发失败: {e}")

    # ---- 私信引擎校验（只校验「私信守护」有效性，不做列表捕获）----
    # 2026-08-29 收敛（用户要求）：引擎校验 = 校验守护凭证(wp) + 私信守护有效性(dm)。
    # 私信列表/会话详情的捕获不再由校验触发，改由私信页「更新会话」按钮按需触发
    # （端点 POST /api/messages/{account}/refresh）。
    if dm_loopback:
        try:
            from auto_dm.daemon_launcher import ensure_daemons_for

            bport = browser_daemon_port(name)
            rport = recv_daemon_port(name)
            # 幂等拉起（端口已开则跳过），再查活性
            ensure_daemons_for(name)
            b_ok = _port_open(bport, timeout=0.5)
            r_ok = _port_open(rport, timeout=0.5)
            if r_ok and b_ok:
                result["dm"] = {
                    "level": "ok",
                    "label": "私信守护正常",
                    "detail": f"私信守护 recv_daemon(port={rport}) 与 "
                              f"凭证守护 browser_daemon(port={bport}) 均已就绪。",
                }
            elif r_ok and not b_ok:
                result["dm"] = {
                    "level": "warn",
                    "label": "私信守护在，凭证守护缺失",
                    "detail": f"recv_daemon 已就绪(port={rport})，但 browser_daemon"
                              f"(port={bport}) 未拉起，昵称关联可能失效。",
                }
            elif b_ok and not r_ok:
                result["dm"] = {
                    "level": "warn",
                    "label": "凭证守护在，私信守护缺失",
                    "detail": f"browser_daemon 已就绪(port={bport})，但 recv_daemon"
                              f"(port={rport}) 未拉起，实时新消息不会入库"
                              f"（可在账号页点「启动私信守护」）。",
                }
            else:
                result["dm"] = {
                    "level": "fail",
                    "label": "私信守护未运行",
                    "detail": f"recv_daemon(port={rport}) 与 browser_daemon"
                              f"(port={bport}) 均未拉起，私信引擎不可用。",
                }
        except Exception as e:
            result["dm"] = {
                "level": "error",
                "label": "守护状态检测异常",
                "detail": f"检测私信守护状态失败: {e}",
            }
    else:
        # 轮询场景（dm_loopback=False）：沿用 wp 引擎结果作轻量近似，
        # 不跑真实列表拉取也不探守护端口（避免高频开销）。
        wp_level = result["wp"].get("level")
        if wp_level == "ok":
            result["dm"] = {
                "level": "ok",
                "label": "正常（沿用 wp）",
                "detail": "wp 引擎正常时私信凭证通常亦可用；点「引擎校验」可检测守护真实状态。",
            }
        elif wp_level in ("fail", "error"):
            result["dm"] = {
                "level": "fail",
                "label": "私信凭证失效",
                "detail": "wp 引擎判定失败，私信凭证亦不可信；请重新获取凭证后再校验。",
            }
        elif wp_level == "warn":
            result["dm"] = {
                "level": "warn",
                "label": "可能可用（沿用 wp）",
                "detail": "wp 引擎告警但凭证可能仍可用；点「引擎校验」可检测守护真实状态。",
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
    rel = os.path.join(name, ".env")   # 相对 _accounts_dir()，避免与 _accounts_dir() 拼接出双重前缀
    env_path = os.path.join(_accounts_dir(), name, ".env")
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
        logger.warning(f"[ACC-009] " + f"[账号] 清空凭证失败 {env_path}: {e}")
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


def _rmtree_account_dir(target_dir: str) -> bool:
    """安全删除账号目录（2026-09-17 审查 P2-2 修补）。

    原实现直接 `shutil.rmtree(path)`，而 path 由索引里的 `rel` 拼接而来 ——
    若索引被污染（异常 rel 值），可能删到预期外路径。
    现加入三重校验：① 必须是绝对路径；② 必须真实存在且是目录；
    ③ 必须位于 `_accounts_dir()` **之内**（防 `..` 穿越）。
    """
    import shutil
    base = os.path.abspath(_accounts_dir())
    tgt = os.path.abspath(target_dir)
    if not os.path.isdir(tgt):
        return False
    if os.path.commonpath([base, tgt]) != base or tgt == base:
        logger.warning(f"[ACC-006] " + f"[accounts] 拒绝删除越界目录: {tgt}（base={base}）")
        return False
    shutil.rmtree(tgt, ignore_errors=True)
    return True


def remove_account(name):
    """删除账号（同时删除其 .env 目录）。所有账号都需通过新增创建，故均可删除。"""
    idx = _load_index()
    if name not in idx.get("accounts", {}):
        raise ValueError(f"账号不存在: {name}")
    rel = idx["accounts"].pop(name)
    env_path = os.path.join(_accounts_dir(), rel)
    try:
        if os.path.isdir(os.path.dirname(env_path)):
            _rmtree_account_dir(os.path.dirname(env_path))
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
    # 会员体系（v0.37.0）：会员空间内经解密视图读（.enc 加密文件也算存在）
    _exists = os.path.exists(env_path)
    _enc_exists = os.path.exists(env_path + ".enc") if env_path else False
    if not _exists and not _enc_exists:
        return {"exists": False, "has_ticket": False, "has_private_key": False,
                "has_cookie": False, "has_web_protect": False}
    try:
        from services import member_ctx
        vals = member_ctx.parse_env_dict(env_path)
    except ImportError:
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


def credentials_complete(env_path):
    """单 profile 铁律前置校验：判定该账号凭证是否【全部齐全】。

    齐全 = .env 存在 且 同时具备：
      - cookie（DY_COOKIES，非空）
      - 私信签名四件套（DY_TICKET / DY_TS_SIGN / DY_CLIENT_CERT / DY_PRIVATE_KEY）
      - wp 凭证（DY_WEB_PROTECT + DY_KEYS，私信 IM 私有网关签名）
    只有全齐，才允许启动凭证守护 / 判定 wp 引擎有效；否则拒绝启动守护，
    明确提示用户需先完成扫码，避免“守护空跑 + 发送必 KICK”。

    【修复 2026-08-17 16:48 基座直测】不再以 "verify_" 开头判 cookie 无效——抖音新版
    s_v_web_id 正常值就是 'verify_xxx' 开头（基座全新扫码实证），仅需 cookie 非空即可。

    返回 (complete: bool, reason: str)。
    """
    # 会员体系（v0.37.0）：会员空间内支持加密 .enc（经解密视图读）
    _exists = os.path.exists(env_path) if env_path else False
    if env_path and not _exists:
        try:
            from services import member_ctx
            if member_ctx.is_member_env(env_path):
                _exists = os.path.exists(env_path + ".enc")
        except Exception:
            pass
    if not env_path or not _exists:
        return False, "账号 .env 不存在（请先新增账号并扫码）"
    try:
        from services import member_ctx
        vals = member_ctx.parse_env_dict(env_path)
    except ImportError:
        vals = dotenv_values(env_path)
    cookie = vals.get("DY_COOKIES") or ""
    ticket = vals.get("DY_TICKET")
    ts_sign = vals.get("DY_TS_SIGN")
    client_cert = vals.get("DY_CLIENT_CERT")
    pkey = vals.get("DY_PRIVATE_KEY")
    webp = vals.get("DY_WEB_PROTECT")
    keys = vals.get("DY_KEYS")

    if not cookie:
        return False, "缺失 DY_COOKIES（未登录或扫码未完成）"
    if not (ticket and ts_sign and client_cert and pkey):
        missing = [k for k, v in (("DY_TICKET", ticket), ("DY_TS_SIGN", ts_sign),
                                   ("DY_CLIENT_CERT", client_cert), ("DY_PRIVATE_KEY", pkey)) if not v]
        return False, "缺失私信签名四件套: " + ", ".join(missing)
    if not (webp and keys):
        return False, "缺失 wp 凭证（DY_WEB_PROTECT/DY_KEYS），私信 IM 网关签名不可用"
    return True, "凭证齐全"


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
                # 2026-09-07：此处是【凭证构建/落盘门禁】——auth 刚从扫码结果
                # 组装，可能尚未写入 .env，调度器按账号名拿不到这份新凭证，
                # 且门禁必须用鲜值判断登录态有效性，故保留直接探活（写入方）。
                # 成功落盘后应调 services.uid_probe.invalidate(name) 让缓存取鲜值。
                uid = DouyinAPI.get_my_uid(auth)
                if not uid:
                    return None
                # 探活成功 -> 同步给统一调度器，后续消费方直接读缓存（零打网）
                #
                # 2026-09-17 修补（OCR 审查 HIGH）：原为 `_up._cache[name] = ...`
                # 但 `_probe(env_path, timeout)` **没有 name 形参**，也无局部绑定
                # → 每次都抛 `NameError`，被下面的 except 静默吞掉 ⇒ **uid 缓存
                # 同步从未生效**，消费方全部回退到打网络探活（违背设计目标）。
                # 现由 env_path 推导账号名（唯一权威函数 name_of_env_path）。
                try:
                    from services import uid_probe as _up
                    _acc_name = name_of_env_path(env_path)
                    if _acc_name:
                        _up._cache[_acc_name] = (time.time(), int(uid))
                    else:
                        logger.debug(
                            f"[accounts] 探活成功但无法由 env_path 推导账号名，"
                            f"跳过 uid 缓存同步: {env_path}")
                except Exception as _ce:
                    logger.warning(f"[ACC-007] " + f"[accounts] uid 缓存同步失败: {_ce}")
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
        logger.warning(f"[ACC-010] " + f"[recap] 账号 {name} 发起自动重捕获失败: {e}")


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
            logger.warning(f"[ACC-011] " + f"[recap] 账号 {name} 停止凭证守护失败（可能未运行）: {e}")
        from auth_helper import enrich_auth
        env_path = env_path_of(name)
        logger.info(f"[recap] 账号 {name} 私信凭证失效，自动拉起指纹浏览器重新捕获（{landing_url}）")
        auth, _ = enrich_auth(None, force=True, env_path=env_path, landing_url=landing_url)
        st["error"] = "" if getattr(auth, "cookie", None) else "捕获未完成（未拿到登录态）"
        logger.success(f"[recap] 账号 {name} 自动重新捕获完成")
    except Exception as e:
        st["error"] = str(e)
        logger.error(f"[ACC-012] " + f"[recap] 账号 {name} 自动重新捕获异常: {e}")
    finally:
        st["running"] = False


def recapture_from_profile(name: str = None, landing_url: str = "https://www.douyin.com/chat?isPopup=1"):
    """从【该账号已登录的持久化 profile】直接读取有效凭证写回 .env（优先于强制重扫）。

    与 auto_recapture(force=True 重扫) 的区别：
      - 本函数 force=False：复用账号独占 profile（用户双击指纹浏览器区域打开的那个
        “一切正常”的浏览器就是它）里已有的登录态，【不重新扫码、不丢登录态】，直接读取
        页面上的有效 web_protect/keys 写回 .env。
      - 仅在 profile 自身也失效（无登录态且超时内拿不到有效签名）时，底层 read_auth_from_profile
        才降级为等待本次扫码（等价于重扫）。
      - 读取过程中若抖音弹出验证码/风控页，底层【保持浏览器打开、不写盘】，提示用户手动处理，
        并持续监测验证码是否通过；用户验证通过后自动恢复读取，拿到 clean 凭证才写回 .env。

    本函数是【发起器】：只在当前线程做节流校验 + 停守护释放 profile 锁，随后把真正的
    读取工作交给独立【后台线程】（_do_recapture_from_profile）执行并立即返回——绝不
    阻塞当前线程（否则在 FastAPI 事件循环线程内会被卡死，且无法同步处理验证码）。
    验证码由用户在前端/指纹浏览器窗口内自行处理，后台线程持续监测直到读到 clean 凭证
    或超时。状态写入 _recap_state，前端可轮询看到进行中/成功/失败。

    返回 (ok: bool, msg: str)：ok=True 仅表示【已发起后台读取】，不代表已读到凭证。
    """
    try:
        if not name:
            name = current_name()
        st = _recap_state.setdefault(name, {"running": False, "last": 0.0, "error": ""})
        now = time.time()
        if st["running"] or (now - st["last"]) < _RECAP_INTERVAL:
            logger.debug(f"[recap-profile] 账号 {name} 从 profile 重读跳过（节流/进行中）")
            return False, "节流中（5 分钟内已触发过一次）"
        st["last"] = now
        st["error"] = ""
        t = threading.Thread(
            target=_do_recapture_from_profile, args=(name, landing_url), daemon=True)
        t.start()
        return True, "已启动从绑定指纹浏览器持久化 profile 读取有效凭证（指纹浏览器已拉起，保持打开）"
    except Exception as e:
        return False, f"从 profile 重读发起异常: {e}"


def _do_recapture_from_profile(name: str, landing_url: str):
    """后台线程：停守护释放 profile 锁 → 从持久化 profile 读取有效凭证 → 写回 .env。

    真正执行 asyncio 的 read_auth_from_profile 的工作线程（无运行中的事件循环，可安全
    asyncio.run）。浏览器在该线程内拉起并【保持打开】，期间持续监测验证码：若抖音弹出
    验证码/风控页，提示用户手动处理，用户验证通过后自动恢复读取，拿到 clean 凭证才写回。
    """
    st = _recap_state.setdefault(name, {"running": False, "last": 0.0, "error": ""})
    st["running"] = True
    st["error"] = ""
    try:
        # 停该账号凭证守护释放 Chromium profile 锁（与查看/重扫同逻辑）
        try:
            from api.accounts import _quit_browser_daemon
            _quit_browser_daemon(name)
        except Exception as e:
            logger.warning(f"[ACC-013] " + f"[recap-profile] 账号 {name} 停止凭证守护失败（可能未运行）: {e}")
        from dy_apis.login_api import DYLoginApi
        from dy_apis.login_api import RiskControlError as _RC
        env_path = env_path_of(name)
        logger.info(f"[recap-profile] 账号 {name} 校验失败，优先从持久化 profile 读取有效凭证"
                    f"（保持指纹浏览器打开，直到读到 clean 凭证或超时）")
        try:
            # 本线程无运行中的事件循环，asyncio.run 安全
            auth = asyncio.run(
                DYLoginApi().read_auth_from_profile(env_path=env_path, landing_url=landing_url))
        except _RC as _rc:
            # 风控/验证码污染：浏览器保持打开，明确提示用户在指纹浏览器处理验证码
            st["error"] = str(_rc)
            logger.warning(f"[ACC-014] " + f"[recap-profile] 账号 {name} 读取被风控验证页污染，已拒绝写盘，"
                f"指纹浏览器保持打开请手动处理验证码: {_rc}")
            return
        except Exception as e:
            st["error"] = str(e)
            logger.error(f"[ACC-015] " + f"[recap-profile] 账号 {name} 从 profile 读取凭证失败: {e}")
            return
        # 确认 clean 后才写盘
        DYLoginApi().save_credential(auth, env_path=env_path)
        st["error"] = ""
        logger.success(f"[recap-profile] 账号 {name} 已从持久化 profile 读取并写回有效凭证")
    except Exception as e:
        st["error"] = str(e)
        logger.error(f"[ACC-016] " + f"[recap-profile] 账号 {name} 重读异常: {e}")
    finally:
        st["running"] = False
