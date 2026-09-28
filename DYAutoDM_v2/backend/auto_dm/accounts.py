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
from loguru import logger
from typing import Dict, Optional, Tuple

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
        logger.debug(f'[SILENT-00] auto_dm.accounts: member_ctx.accounts_root failed')
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
                    logger.debug(f'[SILENT-00] auto_dm.accounts: os.replace bad->good path failed')
                    pass
                # 清理旧的双重前缀目录
                try:
                    bad_dir = os.path.dirname(bad_path)
                    if bad_dir.startswith(_accounts_dir()) and os.path.isdir(bad_dir):
                        # 2026-09-17 修补（审查 P2-2）：改用带越界校验的
                        # 安全删除（原为裸 shutil.rmtree）。
                        _rmtree_account_dir(bad_dir)
                except Exception:
                    logger.debug(f'[SILENT-00] auto_dm.accounts: _rmtree_account_dir migration failed')
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
        logger.debug(f'[SILENT-00] auto_dm.accounts: name_of_env_path os ops failed')
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
# ── 2026-09-28 修（DSSCC-BCC-001，用户报「BCC 又异常」）──────────────────
# 原实现：`_bcc_lazy_spawned: set[str]`，语义是「已拉起过」，
# 但**只在 Popen 成功后 add，进程死亡 / 拉起失败 / 用户手动处理后均不清理**。
# ⇒ 一旦首次拉起失败，该账号在**本进程整个生命周期内被永久钉死**：
#   后续一切路径（用户点「打开浏览器」「刷新凭证」「引擎校验」自动拉齐）
#   恒返回「BCC 此前懒加载失败（端口未就绪）」，即使残留进程早已清扫、
#   端口早已空闲。
# 实测证据（2026-09-28 run_20260928_090932.log）：
#   09:36 首次拉起失败 → 09:36:36「打开指纹浏览器失败 · 拉起浏览器容器失败
#   （BCC 此前懒加载失败（端口未就绪）…）」→ 之后该进程再也拉不起 BCC。
#
# 现行契约（DbC）：
#   pre : 端口未监听（函数开头已判定）
#   inv : `_bcc_lazy_fail[name]` = 上次失败时间戳 + 该退避窗内连续失败次数；
#         仅当「连续失败 ≥ 上限 **且** 仍在退避窗内」才拒绝拉起；
#         端口就绪或窗口过后 **自动放行重试**（失败不再不可逆）。
#   post: 端口就绪 ⇒ 删除失败态（干净重试）；失败 ⇒ count+1、ts 刷新。
_bcc_lazy_fail: dict[str, dict] = {}   # name -> {"ts": float, "count": int}
_BCC_LAZY_FAIL_LIMIT = max(1, int(os.environ.get("DY_BCC_LAZY_FAIL_LIMIT", "3") or 3))
_BCC_LAZY_FAIL_BACKOFF = max(0.0, float(os.environ.get("DY_BCC_LAZY_FAIL_BACKOFF", "120") or 120))
# ── 2026-09-28 修（DSSCC-BCC-002）：**拉起节流**（安全闸，不可省）──────────────
# 只做「可重试」会打开一个新风险面：对**持续性失效**的账号（身份漂移 / 无凭证 /
# 内核不可用），任何「可重试」都会退化为**无限重启风暴** —— 本项目最忌的风控信号
# （历史血案：BCC-025 分支未接熔断 ⇒ 单日 155 次重启，见 knowledge 06/07）。
# 实测复现：放开重试后，张老师（AUTH-050 身份漂移、永久失效）在数分钟内被
# 反复拉起 8+ 个 camoufox 进程。
# 故在**唯一 spawn 出口**加「每账号滑动窗口内的拉起点数」硬上限：把不可逆的
# 「永久拒绝」换成**有速率上限的可重试** —— 瞬时故障能自愈，永久故障被节流而非风暴。
_bcc_spawn_hist: dict[str, list] = {}   # name -> [spawn 时间戳]
_BCC_SPAWN_MAX = max(1, int(os.environ.get("DY_BCC_SPAWN_MAX_PER_WINDOW", "4") or 4))
_BCC_SPAWN_WINDOW = max(30.0, float(os.environ.get("DY_BCC_SPAWN_WINDOW", "900") or 900))



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
        logger.debug(f'[SILENT-00] auto_dm.accounts: bcc_mark_user_stopped write/remove failed')
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
        _bcc_lazy_fail.pop(name, None)      # 已在运行 ⇒ 清失败态（可干净重试）
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
            _bcc_lazy_fail.pop(name, None)      # 端口已就绪 ⇒ 清失败态（可干净重试）
            return {"ok": True, "port": port, "msg": "已在运行"}
        # 失败退避（取代原「一次失败永久拒绝」的不可逆契约）。
        # 仅当「连续失败 ≥ 上限」且「仍在退避窗内」才拒绝 —— 窗口一过自动重试。
        _fs = _bcc_lazy_fail.get(name)
        if _fs and _fs.get("count", 0) >= _BCC_LAZY_FAIL_LIMIT:
            _age = time.time() - float(_fs.get("ts") or 0)
            if _age < _BCC_LAZY_FAIL_BACKOFF:
                return {"ok": False, "port": None,
                        "msg": (f"BCC 连续拉起失败 {_fs['count']} 次，"
                                f"退避 {max(0, int(_BCC_LAZY_FAIL_BACKOFF - _age))}s 后自动重试"
                                f"（请同时检查 BCC 日志）")}
            # 退避窗已过 ⇒ 清零，放行重试
            _bcc_lazy_fail.pop(name, None)
        # ── 拉起节流（DSSCC-BCC-002）：把「永久拒绝」换成「有速率上限的可重试」──
        # 唯一 spawn 出口，任何路径（用户点按钮 / 自动拉齐 / 保活）都受此约束。
        _now = time.time()
        _hist = [t for t in _bcc_spawn_hist.get(name, []) if _now - t < _BCC_SPAWN_WINDOW]
        _bcc_spawn_hist[name] = _hist
        if len(_hist) >= _BCC_SPAWN_MAX:
            return {"ok": False, "port": None,
                    "msg": (f"BCC 拉起被节流：{int(_BCC_SPAWN_WINDOW)}s 内已拉起 "
                            f"{len(_hist)} 次（上限 {_BCC_SPAWN_MAX}）—— "
                            f"持续性失效（如身份漂移/无凭证）需人工处理，"
                            f"节流防止浏览器反复重启引发风控")}
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
        # 注：原此处为「本进程内曾拉起过 ⇒ 永久拒绝再拉」，已被上方的
        # **退避窗**语义取代（失败可重试，不再不可逆）——见 `_bcc_lazy_fail` 契约。
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
                logger.debug(f'[SILENT-00] auto_dm.accounts: member_ctx set env vars failed')
                pass
            kwargs["env"] = _menv
            if platform.system() == "Windows":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen([binary, "--account", name, "--port", str(port)], **kwargs)
            _bcc_spawn_hist.setdefault(name, []).append(time.time())   # 节流记账
            logger.info(f"[bcc-lazy] 已懒加载 BCC account={name} port={port}")
        except Exception as e:
            _bcc_lazy_fail[name] = {
                "ts": time.time(),
                "count": int((_bcc_lazy_fail.get(name) or {}).get("count", 0)) + 1,
            }
            return {"ok": False, "port": None, "msg": f"BCC 拉起失败: {e}"}
    if wait_ready:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _port_open(port, timeout=0.4):
                _bcc_lazy_fail.pop(name, None)   # 就绪 ⇒ 清失败态
                return {"ok": True, "port": port, "msg": "懒加载就绪"}
            time.sleep(0.5)
        _bcc_lazy_fail[name] = {
            "ts": time.time(),
            "count": int((_bcc_lazy_fail.get(name) or {}).get("count", 0)) + 1,
        }
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
            logger.debug(f'[SILENT-00] auto_dm.accounts: s.close() failed')
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
      - 私信引擎：对**自身 uid** 发起一次真实 imapi 写操作
                （`create_conversation`，cmd 609，不投递任何消息）
                → 服务端接受则说明私信凭证**可写**。
                这是唯一能识别「账号级只读态」（读可用、写被拒）的判据；
                仅看四件套字段/探活/守护端口都会把它误判为有效。

    dm_loopback=False 时（getAccounts 30s 轮询）：**不新增网络请求**，
      只读 IM 写探针的缓存态给出诚实三态（ok / fail / unknown），
      **不再用 wp 结果冒充 dm**。
    dm_loopback=True 时（用户点「引擎校验」）：强探活（force）真实写校验，
      守护进程状态降级为 detail 里的**补充信息**（守护没起 ≠ 凭证无效）。

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
                # 有签名但探活(uid)失败。**必须区分两种成因**，文案不能混：
                #   ① uid_probe 判定「陈旧/不可信」（AUTH-050，身份漂移）
                #      → 这不是网络抖动，是**凭证身份失效**，重试无用，须重扫；
                #   ② 真·网络抖动/偶发风控 → 可稍后重试。
                # 修复前一律写成 ①→「网络抖动」，会把身份失效误导成临时故障，
                # 用户反复「稍后重试」而问题永不消失（张老师实测即此形态）。
                _stale = False
                try:
                    from services.uid_probe import _uid_consistent_with_history
                    from dy_apis.login_api import DYLoginApi as _LA
                    _a2 = _LA._load_auth_from_env(env_path)
                    _probe_uid = None
                    try:
                        # 直接问一次（零缓存语义）：拿到的值若与历史不一致
                        # 说明服务端认的是另一个身份。
                        _probe_uid = _a2.get_uid() if hasattr(_a2, "get_uid") else None
                    except Exception:
                        _probe_uid = None
                    if _probe_uid and not _uid_consistent_with_history(name, _probe_uid):
                        _stale = True
                except Exception:
                    _stale = False
                if _stale:
                    result["wp"] = {
                        "level": "fail",
                        "label": "身份漂移（AUTH-050）",
                        "detail": "探活拿到的 uid 与该账号历史会话不一致 —— "
                                  "登录身份已被轮换/替换（属凭证失效，非网络问题）。"
                                  "稍后重试无效，请对该账号【重新扫码】。",
                    }
                else:
                    result["wp"] = {
                        "level": "warn",
                        "label": "捕获齐全但探活失败",
                        "detail": "wp 签名四件套已捕获，但 get_my_uid 探活失败"
                                  "（网络抖动/账号偶发风控），"
                                  "可稍后重试引擎校验；私信写校验结果见私信引擎。",
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

    # ---- 私信引擎校验（**真实 IM 写校验**，不再只探端口）----
    # 2026-09-21 v0.44.17 重写。
    #
    # 设计契约（本函数 docstring 第 432 行早已写明，但实现长期未兑现）：
    #   「私信引擎：拉取私信会话列表成功则说明私信凭证有效」。
    # 实测缺陷：旧实现只做 `_port_open(bport/rport)` —— **只探测端口是否 LISTEN，
    #   完全不碰 imapi**。于是张老师账号（609 建会话被服务端拒、发送全废）
    #   依然被判 `ok / 私信守护正常`。
    #   第 638 行自己的注释都写着「端口在 ≠ 凭证有效」，dm 引擎却正是这么判的。
    #
    # 现改为**真实写校验**：调 probe_im_write → create_conversation(自身 uid)。
    #   · 用户手动点「引擎校验」→ force=True（必真打网，不看缓存）；
    #   · 守护进程状态降级为**补充信息**，附在 detail 里，不再单独决定成败
    #     （守护没起 ≠ 凭证无效；这两件事必须分开判，见单 profile 铁律 #35）。
    if dm_loopback:
        try:
            _w_ok, _w_detail = probe_im_write(name, force=True)
            # 守护态仅作补充信息（不影响 level 判定）
            _daemon_note = ""
            try:
                from auto_dm.daemon_launcher import ensure_daemons_for
                bport = browser_daemon_port(name)
                rport = recv_daemon_port(name)
                ensure_daemons_for(name)
                b_ok = _port_open(bport, timeout=0.5)
                r_ok = _port_open(rport, timeout=0.5)
                if not (b_ok and r_ok):
                    _miss = []
                    if not r_ok:
                        _miss.append(f"recv_daemon(port={rport})")
                    if not b_ok:
                        _miss.append(f"browser_daemon(port={bport})")
                    _daemon_note = f"；守护未就绪：{'、'.join(_miss)}"
            except Exception as _de:
                _daemon_note = f"；守护态检测失败: {_de}"
            if _w_ok:
                result["dm"] = {
                    "level": "ok",
                    "label": "私信凭证可用（写校验通过）",
                    "detail": f"{_w_detail}{_daemon_note}",
                }
            else:
                result["dm"] = {
                    "level": "fail",
                    "label": "私信凭证不可写",
                    "detail": f"{_w_detail}。私信发送将全部失败，"
                              f"请对该账号执行【重新扫码】重建登录态。",
                }
        except Exception as e:
            result["dm"] = {
                "level": "error",
                "label": "私信写校验异常",
                "detail": f"IM 写校验未能完成: {e}",
            }
    else:
        # 轮询场景（dm_loopback=False）：**不得用 wp 结果冒充 dm**。
        #
        # 历史缺陷（本次修正）：旧实现把 `wp_level == "ok"` 直接映射成
        # `dm = {"level": "ok", "label": "正常（沿用 wp）"}`。
        # 但 wp 是「web_protect 四件套 + query/user 探活」，与 imapi 私信凭证
        # 是**两条独立通路**——wp 正常完全不代表 imapi 可写（张老师实测：
        # wp 全绿而 609 拒绝）。在前端 30s 轮询下，这会让「私信引擎」长期
        # 谎报 ok，用户点开页面看到的是绿灯，实际一条都发不出去。
        #
        # 现改为基于 **IM 写探针的缓存态**给出诚实三态（不新增网络请求：
        # 轮询路径只读缓存，缓存为空则如实报「未校验」而非伪造 ok）。
        _cw_ok: Optional[bool] = None
        _cw_detail = ""
        try:
            with _im_write_lock:
                _hit = _im_write_cache.get(name)
            if _hit:
                _cw_ok, _cw_detail = _hit[1], _hit[2]
        except Exception:
            _cw_ok = None
        if _cw_ok is True:
            result["dm"] = {
                "level": "ok",
                "label": "私信凭证可用（写校验通过）",
                "detail": _cw_detail or "imapi 写校验通过。",
            }
        elif _cw_ok is False:
            result["dm"] = {
                "level": "fail",
                "label": "私信凭证不可写",
                "detail": _cw_detail or "imapi 写校验未通过，请重新获取凭证。",
            }
        else:
            # 三态之「未知」：**诚实降级**，不伪造 ok 也不谎报 fail。
            result["dm"] = {
                "level": "unknown",
                "label": "私信凭证未校验",
                "detail": "尚未执行 IM 写校验；点「引擎校验」可检测私信凭证"
                          "能否真实写入 imapi。",
            }
    result["ok"] = (result["wp"]["level"] in ("ok", "warn")
                    and result["dm"]["level"] in ("ok", "warn", "skip"))
    return result


def verify_credential(
    name: str,
    *,
    lightweight: bool = False,
    force_probe: bool = False,
    timeout: float = 8,
    auto_fix: bool = True,
) -> dict:
    """账号凭证有效性判据——项目唯一**凭证有效性**决策入口。

    lightweight=True 时：
        - 仅调用 credentials_complete 判静态字段齐全性
        - 零网络、零探活、零 IM 写校验
        - 用于 crawl/platform/enrich_auth 等「只需知道凭证字段齐全」的路径

    lightweight=False（默认）时：
        - 同当前 verify_account 的完整双引擎校验
        - 含 wp 探活 + dm 写探针 + 身份漂移检测 + auto_fix

    返回 {ok, wp:{level,label,detail}, dm:{level,label,detail},
           uid, has_ticket, has_cookie, has_signature, has_web_protect,
           auto_fix_triggered}
    """
    name = name or current_name()
    if not name:
        return {"ok": False,
                "wp": {"level": "fail", "label": "未指定账号", "detail": "name 为空"},
                "dm": {"level": "skip", "label": "跳过", "detail": "未指定账号"},
                "uid": None,
                "has_ticket": False, "has_cookie": False,
                "has_signature": False, "has_web_protect": False,
                "auto_fix_triggered": False}
    env_path = env_path_of(name)
    if not env_path:
        return {"ok": False,
                "wp": {"level": "fail", "label": "账号不存在", "detail": f"账号 {name} 未登记"},
                "dm": {"level": "skip", "label": "跳过", "detail": "账号不存在"},
                "uid": None,
                "has_ticket": False, "has_cookie": False,
                "has_signature": False, "has_web_protect": False,
                "auto_fix_triggered": False}

    # ── lightweight：仅静态字段检查，零网络 ──
    if lightweight:
        _complete, _reason = credentials_complete(env_path)
        _status = _read_status(env_path)
        _has_ticket = bool(_status.get("has_ticket"))
        _has_cookie = bool(_status.get("has_cookie"))
        _has_webp = bool(_status.get("has_web_protect"))
        _has_sig = _has_ticket and _has_webp and bool(_status.get("has_private_key"))
        _wp_level = "ok" if _complete else "fail"
        _wp_label = "凭证齐全" if _complete else "凭证不完整"
        _wp_detail = _reason if not _complete else "静态字段检查通过"
        return {
            "ok": _complete,
            "wp": {"level": _wp_level, "label": _wp_label, "detail": _wp_detail},
            "dm": {"level": "skip", "label": "跳过（lightweight）",
                   "detail": "lightweight 模式跳过 IM 写校验"},
            "uid": None,
            "has_ticket": _has_ticket,
            "has_cookie": _has_cookie,
            "has_signature": _has_sig,
            "has_web_protect": _has_webp,
            "auto_fix_triggered": False,
        }

    # ── 完整双引擎校验 ──
    try:
        _v = verify_account(name, timeout=timeout, dm_loopback=True, auto_fix=auto_fix)
    except Exception as _e:
        _v = {"ok": False,
              "wp": {"level": "error", "label": "校验异常", "detail": str(_e)},
              "dm": {"level": "error", "label": "校验异常", "detail": str(_e)},
              "uid": None, "auto_fix_triggered": False}
    _status = _read_status(env_path)
    _has_ticket = bool(_status.get("has_ticket"))
    _has_cookie = bool(_status.get("has_cookie"))
    _has_webp = bool(_status.get("has_web_protect"))
    _has_sig = _has_ticket and _has_webp and bool(_status.get("has_private_key"))
    _v["has_ticket"] = _has_ticket
    _v["has_cookie"] = _has_cookie
    _v["has_signature"] = _has_sig
    _v["has_web_protect"] = _has_webp
    return _v


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
    # 🔴 2026-09-21：明文 .env 已废弃 —— 不再预创建空明文文件；
    # 凭证由扫码后经 member_ctx.write_env_file 加密写入 <env_path>.enc。
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
    """清空加密凭证里的敏感字段，返回是否发生了改动（**只操作 .enc**）。

    🔴 2026-09-21：明文 .env 已废弃 —— 走 member_ctx.write_env_file，
    以空值覆盖即被其「过滤空值键」语义删除。
    """
    from services import member_ctx
    if not member_ctx.env_exists(env_path):
        return False
    cur = member_ctx.parse_env_dict(env_path)
    if not any(k in cur for k in _CREDENTIAL_KEYS):
        return False
    member_ctx.write_env_file(env_path, {k: "" for k in _CREDENTIAL_KEYS}, merge=True)
    return True


def clear_credentials_of(env_path):
    """仅清空单个账号凭证的敏感字段（保留其他配置）。**只操作 .enc**。"""
    if not env_path:
        return False
    try:
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
        logger.debug(f'[SILENT-00] auto_dm.accounts: clear_credentials list_accounts iter failed')
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
        logger.debug(f'[SILENT-00] auto_dm.accounts: _rmtree_account_dir delete_account failed')
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
    # 🔴 2026-09-21：凭证永久加密（明文 .env 已废弃）
    # 会员体系（v0.37.0）：会员空间内经解密视图读（只认 .enc 加密文件）
    from services import member_ctx
    if not member_ctx.env_exists(env_path):
        return {"exists": False, "has_ticket": False, "has_private_key": False,
                "has_cookie": False, "has_web_protect": False}
    vals = member_ctx.parse_env_dict(env_path)
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

    🔴 P3：凭证收敛唯一入口为 verify_credential，本函数不再直接被外部模块调用。
    外部代码应统一使用 verify_credential(lightweight=True) 替代。

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
    # 🔴 2026-09-21：凭证永久加密（明文 .env 已废弃）—— 存在性只认 .enc
    from services import member_ctx
    if not member_ctx.env_exists(env_path):
        return False, "账号凭证不存在（请先新增账号并扫码）"
    vals = member_ctx.parse_env_dict(env_path)
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


# ---------------------------------------------------------------------------
# IM 写能力探针（2026-09-21 v0.44.17）
# ---------------------------------------------------------------------------
# 设计契约：私信凭证「有效」的充分条件不是「字段非空」也不是「探活通」，
# 而是**能完成一次真实的 imapi 写操作**。
#
# 为什么必须新增（实证教训，见 工作记忆/cases/2026-09-20_直播监听失败_cmd609…md §8.3）：
#   「探活能过 ⇒ 凭证有效」在**写会话**这一层**不成立**。
#   四川工伤张老师实测：探活 ✅、cmd 610 读会话列表 ✅，但 cmd 609 建会话
#   被服务端以 `unexepcted session length` 拒绝 —— 账号级「只读态」。
#   而旧 verify_account 只查「四件套字段非空 + 探活通」，把它判成 ok，
#   直到引擎启动才在 609 处失败（用户侧表现为「校验说可靠，一发就废」）。
#
# 探针选型：create_conversation(自身 uid)
#   · 只**建/取会话**，不投递任何消息 ⇒ 零真人打扰、可安全高频调用；
#   · 对自身 uid 建会话不会影响任何真实用户；
#   · 与 core/auto_dm._verify_credential 的预检同参同判据（口径统一）。
_IM_WRITE_PROBE_TTL = 120.0          # 写入路径探针结果缓存秒数（写能力变化慢，可缓存）
_im_write_cache: Dict[str, Tuple[float, bool, str]] = {}
_im_write_lock = threading.Lock()


def probe_im_write(name: str, auth=None, force: bool = False,
                   ttl: Optional[float] = None) -> Tuple[bool, str]:
    """探测账号是否具备 imapi **写**能力（cmd 609 建会话）。

    返回 (ok, detail)。ok=True 表示服务端接受该账号的写会话请求。

    缓存：默认 120s（写能力不会秒变；但比读凭证的 300s 短，便于只读态自愈后
    较快重新放行）。force=True 跳过缓存（用户手动点「引擎校验」时用）。

    异常一律转为 (False, 原因) —— 绝不让探针异常冒泡成「校验异常」而丢失
    「凭证不可用」这一确定结论。
    """
    if not name:
        return False, "未指定账号"
    if not force:
        with _im_write_lock:
            hit = _im_write_cache.get(name)
        _ttl = ttl if ttl is not None else _IM_WRITE_PROBE_TTL
        if hit and (time.time() - hit[0]) < _ttl:
            return hit[1], hit[2]
    try:
        from dy_apis.login_api import DYLoginApi
        from dy_apis.douyin_api import DouyinAPI

        if auth is None:
            env_path = env_path_of(name)
            if not env_path:
                return False, "账号 .env 不存在"
            auth = DYLoginApi._load_auth_from_env(env_path)
        if not auth or not getattr(auth, "cookie", None):
            return False, "凭证为空（无 cookie），无法进行写校验"

        # 目标 uid：优先用**会话体系** uid（conv_id 推断，权威且零网络），
        # 因为它才是 imapi 认的身份；web 探活 uid 可能是另一个体系。
        my_uid = ""
        try:
            from services.conv_identity import my_uid as _conv_my_uid
            my_uid = str(_conv_my_uid(name) or "")
        except Exception:
            my_uid = ""
        if not my_uid:
            # 无历史会话（新账号）→ 退化为 web 探活 uid
            try:
                from services.uid_probe import get_uid as _uid_get
                _u = _uid_get(name)
                my_uid = str(_u) if _u else ""
            except Exception:
                my_uid = ""
        if not my_uid or not my_uid.isdigit():
            return False, "无法确定自身 uid（无历史会话且探活失败），无法进行写校验"

        DouyinAPI.create_conversation(auth, int(my_uid))
        detail = f"imapi 写校验通过（cmd 609 建会话被接受，uid={my_uid}）"
        with _im_write_lock:
            _im_write_cache[name] = (time.time(), True, detail)
        return True, detail
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "INVALID_REQUEST" in msg or "KICK" in msg:
            detail = (f"imapi 写校验被拒（{msg[:120]}）—— 凭证被服务端判非法，"
                      f"请重新扫码")
        elif "session length" in msg:
            # 账号级「只读态」：读可以、写不行。属凭证需重建，不是网络问题。
            detail = (f"imapi 写校验被拒：{msg[:120]} —— 该账号 web 会话被服务端"
                      f"判为「只读」（读可用、写不可用），请重新扫码重建登录态")
        elif "HTTP" in msg:
            detail = f"imapi 写校验网络失败：{msg[:120]}"
        else:
            detail = f"imapi 写校验异常：{msg[:120]}"
        with _im_write_lock:
            _im_write_cache[name] = (time.time(), False, detail)
        logger.warning(f"[ACC-030] [verify] 账号 {name} {detail}")
        return False, detail


def invalidate_im_write_cache(name: str = "") -> None:
    """作废 IM 写校验缓存（重扫/重捕凭证后调用）。"""
    with _im_write_lock:
        if name:
            _im_write_cache.pop(name, None)
        else:
            _im_write_cache.clear()



# ---------------------------------------------------------------------------
# 直播「昵称解密权」探针（ENG-018，2026-09-21）
# ---------------------------------------------------------------------------
# 设计契约（用户 2026-09-21 明确）：
#   抖音直播**本身支持匿名观看**；凭证的职责是「部分直播间昵称加密时的解密权」。
# 实测缺陷（本函数存在的理由）：
#   张老师账号 cookie 70 个字段齐全（sessionid/sid_tt/ttwid/uid_tt 全在），
#   但服务端判其「未登录」⇒ 直播帧下发脱敏数据
#   （uid=111111 + 昵称 `威***` + sec_uid 空），与**完全不带 cookie 的真匿名**
#   现象逐字相同。⇒「有 cookie」是**代理信号**，判据为真、能力为零。
#   正确判据 = 主站 `user/profile/self/` 是否承认该会话（status_code=0 + sec_uid）。
#
# 判据落点（§〇·己·3）：真实业务通路上的**登录态确认**，不新增任何昵称类主动查询。
LIVE_IDENTITY_PROBE_TTL = 300.0
_live_identity_cache: Dict[str, Tuple[float, bool, str]] = {}
_live_identity_lock = threading.Lock()


def _live_session_probe_raw(auth) -> Tuple[Optional[bool], str]:
    """原始三态探测（不缓存、只读）：True=服务端承认 / False=明确拒绝 / None=**取不到证据**。

    与 probe_live_identity 的分工：本函数**不把「探测失败」折成 False**
    —— 「探测不到」≠「不存在」（三态铁律）。写入门禁必须能区分二者：
    只有 False 才允许据此拒绝写入，None 只能保守放行（诚实降级）。
    """
    import requests
    from builder.header import HeaderBuilder
    from utils.dy_util import tls_verify
    api = "/aweme/v1/web/user/profile/self/"
    url = (f"https://www.douyin.com{api}"
           "?device_platform=webapp&aid=6383&channel=channel_pc_web")
    r = requests.get(url, headers={"user-agent": HeaderBuilder.ua,
                                   "referer": "https://www.douyin.com/"},
                     cookies=auth.cookie, timeout=20, verify=tls_verify())
    body = r.text or ""
    try:
        js_status = (r.json() or {}).get("status_code")
    except Exception:
        js_status = None
    if bool(js_status == 0 and "MS4wLjABAAAA" in body):
        return True, "服务端承认登录态：具备直播昵称解密权"
    return False, (f"服务端判为未登录（profile/self status_code={js_status}）—— "
                   f"无直播昵称解密权，弹幕昵称将被脱敏（uid=111111）。请重新扫码；"
                   f"若重扫后仍如此，则可能是该直播间开启了「隐藏观众信息」")


def live_session_state(name: str, auth=None, force: bool = False,
                       ttl: Optional[float] = None) -> Tuple[Optional[bool], str]:
    """账号会话活性**三态**（带缓存）：True=已确认被承认 / False=已确认被拒 / None=取不到证据。

    None（探测自身失败/无 cookie）**不得**折成 False —— 调用方必须保守放行。
    判据落在**真实业务通路**（`user/profile/self/` 是否被服务端承认）上，
    而不是「有 cookie / 端口活着」这类代理信号。

    ⚠️ 判据边界（禁止超载）：本判据只覆盖「会话活性/能力授权」这一类失效。
    **身份漂移**用 `query/user` + 历史 conv_id 判（`_uid_consistent_with_history`）；
    二者结论**互不代替**（`query/user` 容忍陈旧会话，`profile/self` 严格）。
    """
    if not name:
        return None, "未指定账号"
    if not force:
        with _live_identity_lock:
            hit = _live_identity_cache.get(name)
        _ttl = ttl if ttl is not None else LIVE_IDENTITY_PROBE_TTL
        if hit and (time.time() - hit[0]) < _ttl:
            return hit[1], hit[2]
    try:
        from dy_apis.login_api import DYLoginApi
        if auth is None:
            env_path = env_path_of(name)
            if not env_path:
                return False, "账号 .env 不存在"
            auth = DYLoginApi._load_auth_from_env(env_path)
        if not auth or not getattr(auth, "cookie", None):
            detail = "无 cookie —— 无解密权（只能收到匿名脱敏弹幕）"
            with _live_identity_lock:
                _live_identity_cache[name] = (time.time(), False, detail)
            return False, detail
        state, detail = _live_session_probe_raw(auth)
        with _live_identity_lock:
            _live_identity_cache[name] = (time.time(), state, detail)
        if state is False:
            # 只记 debug：调用方（AutoDM / 重扫路径）会用 LIVE-035 显式上报并透出到 UI，
            # 这里再 warning 一次会变成同一结论双份日志（§日志去重）。
            logger.debug(f"[live-identity] 账号 {name} {detail}")
        elif state is None:
            logger.warning(f"[LIVE-036] [live-identity] 账号 {name} {detail}")
        return state, detail
    except Exception as e:  # noqa: BLE001
        detail = f"登录态探测失败：{type(e).__name__}: {str(e)[:120]}（结论未知，不据此降级）"
        logger.warning(f"[LIVE-036] [live-identity] 账号 {name} {detail}")
        return None, detail


def probe_live_identity(name: str, auth=None, force: bool = False,
                        ttl: Optional[float] = None) -> Tuple[bool, str]:
    """兼容入口（既有二元契约，行为与改造前逐字一致）：

    ok=False 同时涵盖「服务端明确拒绝」与「结论未知（探测失败）」两种。
    需要区分二者的代码（写入门禁）请改用 live_session_state() 拿三态。
    """
    state, detail = live_session_state(name, auth=auth, force=force, ttl=ttl)
    return bool(state), detail


def invalidate_live_identity_cache(name: str = "") -> None:
    """作废直播解密权探针缓存（重扫/重捕凭证后调用）。"""
    with _live_identity_lock:
        if name:
            _live_identity_cache.pop(name, None)
        else:
            _live_identity_cache.clear()


# ---------------------------------------------------------------------------
# 账号级「直播昵称解密权」权威判据（H-3 / ENG-018 返工，2026-09-22）
# ---------------------------------------------------------------------------
# 设计契约（用户 2026-09-21 定调 + 2026-09-22 返工）：
#   凭证的职责是「部分直播间昵称加密时的**解密权**」。要收得到真实
#   uid/nickname/sec_uid，必须**同时**满足两个独立条件：
#     ① 会话被服务端承认  —— 判据 = 主站 `user/profile/self/`（live_session_state）
#     ② 身份未漂移        —— 判据 = 探活 uid ∈ 该账号历史 conv_id（AUTH-050）
#   ⇒ 二者是**合取**，不是替代：任一条为「已确认失败」即无解密权；
#      任一条取不到证据时，**不得**据它降级（诚实三态）。
#
# 为什么必须显式上报这一条（而非照抄「以 ② 为主」）：
#   两个判据覆盖**两类不同失效**——`live_session_state` 自己写明「身份漂移
#   用 query/user + 历史 conv_id 判；二者结论互不代替」。若把 ② 当 ① 的替代，
#   或把 ① 当 ② 的替代，都会把「会话活性被拒」与「身份漂移」互相误判 ——
#   正是 H-3 要修的缺陷本身（旧实现用 ③ 名义上报、实为判 ①）。故按**合取**实现。
LIVE_IDENTITY_VERDICT_TTL = 300.0

_VERDICT_LABEL = {
    "not_logged_in": "无解密权（服务端不承认该会话）",
    "uid_drift": "无解密权（身份漂移：探活 uid 不在历史会话中）",
    "no_credential": "无解密权（无凭证）",
    "unknown": "取不到证据（诚实降级，不据此降级）",
    "ok": "具备直播昵称解密权",
}


def uid_identity_verdict(name: str, auth=None, force: bool = False,
                         ttl: Optional[float] = None) -> Tuple[Optional[bool], str, str, str]:
    """账号级解密权**权威三态判据**（合取，零新增风控面）。

    返回 `(state, reason, label, detail)`：

    ===============  ==================  ============================================
    state            reason             含义
    ===============  ==================  ============================================
    ``True``         ``ok``              有解密权：会话被承认 **且** 身份未漂移
    ``False``        ``not_logged_in``   已确认无解密权：服务端不承认该会话
    ``False``        ``uid_drift``       已确认无解密权：身份漂移（AUTH-050）
    ``False``        ``no_credential``   已确认无解密权：无凭证
    ``None``         ``unknown``         取不到证据 → 调用方**必须**诚实降级
    ===============  ==================  ============================================

    ⚠️ `None` 与被确证的 `False` 语义相反，调用方不得把二者混同（§〇·己·2）。
    `reason` 是机器可判定的键（供分支与测试），`label` 是给人看的中文结论。

    风控面：身份侧读数来自 `services.uid_probe.uid_verdict()`（即 `get_uid()`
    的出口记录），**不额外打网、不绕过 300s TTL 与同账号串行锁**；
    会话侧复用 `live_session_state()` 的既有缓存。
    """
    # ① 会话活性（服务端是否承认登录态）
    sess_ok, sess_detail = live_session_state(name, auth=auth, force=force, ttl=ttl)
    if sess_ok is False:
        _rs = "no_credential" if ("无 cookie" in (sess_detail or "")) else "not_logged_in"
        return False, _rs, _VERDICT_LABEL[_rs], f"[会话活性] {sess_detail}"

    # ② 身份漂移（探活 uid 是否仍在历史 conv_id 中）
    from services.uid_probe import uid_verdict as _uidv
    id_ok, id_detail = _uidv(name, ttl=(LIVE_IDENTITY_VERDICT_TTL if ttl is None else ttl))
    if id_ok is False:
        return False, "uid_drift", _VERDICT_LABEL["uid_drift"], f"[身份一致性] {id_detail}"
    if id_ok is None:
        # 身份侧无证据：若会话侧亦无证据 → 整体 unknown；
        # 若会话侧已确认被承认 → 仍不得据此判「有解密权」（诚实降级）。
        _why = (f"会话侧：{'已确认被承认' if sess_ok else '取不到证据'}；"
                f"身份侧：{id_detail}")
        return None, "unknown", _VERDICT_LABEL["unknown"], _why

    # 到这里：身份未漂移（True）
    if sess_ok is True:
        return True, "ok", _VERDICT_LABEL["ok"], (
            f"[会话活性] {sess_detail}；[身份一致性] {id_detail}")
    return None, "unknown", _VERDICT_LABEL["unknown"], (
        f"会话侧取不到证据：{sess_detail}；[身份一致性] {id_detail}")



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
                # 🔴 2026-09-21：凭证永久加密（明文 .env 已废弃）—— 统一走 member_ctx
                from services import member_ctx as _mc
                vals = _mc.parse_env_dict(env_path)
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
                    # 2026-09-21 v0.44.17：写校验失败同样写入 IM 写探针缓存，
                    # 让引擎校验立刻能报出「不可写」，而不是等下次手动校验。
                    try:
                        with _im_write_lock:
                            _im_write_cache[name] = (
                                time.time(), False,
                                "imapi 写校验被拒（凭证落盘门禁）——请重新扫码")
                    except Exception:
                        logger.debug(f'[SILENT-00] auto_dm.accounts: _im_write_cache SIGN_REJECTED failed')
                        pass
                    return "SIGN_REJECTED"
                # 2026-09-21 v0.44.17：写校验通过 → 同步写探针缓存（鲜值），
                # 使引擎校验/轮询立刻显示「私信凭证可用」，零额外网络请求。
                try:
                    with _im_write_lock:
                        _im_write_cache[name] = (
                            time.time(), True,
                            f"imapi 写校验通过（cmd 609 建会话被接受，uid={uid}）")
                except Exception:
                    logger.debug(f'[SILENT-00] auto_dm.accounts: _im_write_cache OK failed')
                    pass
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
    # ══════════════════════════════════════════════════════════════════════
    # 2026-09-19 v0.43.98【双实例根治】整段独占 profile 所有权。
    #
    # 病根：本线程「停守护 → 另起 chromium 独占 profile →（调用方随后）拉回 BCC」
    # 三段分布在两个模块，中间无互斥 —— 与 verify_account 的 dm 段
    # ensure_daemons_for 撞车时，两个 chromium 同时持有同一 profile
    # （实测 TargetClosedError：浏览器加载异常/卡顿、扫码回执读不到）。
    # 现把「停 → 用 → 还」整段放进同一把按账号维度的锁内串行。
    # ══════════════════════════════════════════════════════════════════════
    try:
        from services.browser_gate import ProfileOwnership as _Own
        _own = _Own(name, "auto_recapture")
        _own.__enter__()          # 显式进入（with 语句在 finally 里不便表达）
    except Exception as _e_own:   # 门禁不可用时降级为不加锁（不阻断业务）
        logger.warning(f"[ACC-022] " + f"[recap] 账号 {name} 未取得 profile 所有权锁"
            f"（降级不加锁，存在多实例风险）: {_e_own}")
        _own = None
    try:
        # 停该账号凭证守护释放 Chromium profile 锁（与查看/重扫同逻辑）
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
        # 归还所有权前把 BCC 拉回（在锁内完成，杜绝与并发拉起重叠）
        try:
            from auto_dm.daemon_launcher import ensure_daemons_for
            ensure_daemons_for(name, wait=False)
        except Exception as _e_rd:
            logger.debug(f"[recap] 账号 {name} 守护回拉跳过: {_e_rd}")
        st["running"] = False
        if _own is not None:
            _own.__exit__(None, None, None)


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
    # 2026-09-19 v0.43.98【双实例根治】整段独占 profile 所有权（同上，
    # 「停守护 → 另起 chromium 读 profile → 拉回 BCC」必须在同一把锁内）。
    try:
        from services.browser_gate import ProfileOwnership as _Own
        _own = _Own(name, "recapture_from_profile")
        _own.__enter__()
    except Exception as _e_own:
        logger.warning(f"[ACC-023] " + f"[recap-profile] 账号 {name} 未取得 profile 所有权锁"
            f"（降级不加锁，存在多实例风险）: {_e_own}")
        _own = None
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
            # ⚠️ 浏览器保持打开 → **不得**把 BCC 拉回（否则抢同一 profile，
            # 正是本次要根治的双实例重叠）。标记留给 finally 判断。
            st["_keep_browser_open"] = True
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
        # 归还所有权前把 BCC 拉回（在锁内完成，杜绝与并发拉起重叠）。
        # 例外：风控/验证码污染时浏览器被有意保持打开供用户处理 ——
        # 此时拉回 BCC 会立刻抢同一 profile（双实例），必须跳过。
        if not st.get("_keep_browser_open"):
            try:
                from auto_dm.daemon_launcher import ensure_daemons_for
                ensure_daemons_for(name, wait=False)
            except Exception as _e_rd:
                logger.debug(f"[recap-profile] 账号 {name} 守护回拉跳过: {_e_rd}")
        else:
            logger.info(f"[recap-profile] 账号 {name} 浏览器保持打开等待人工验证，"
                        f"暂不拉回 BCC（防双实例抢 profile）")
        st.pop("_keep_browser_open", None)
        st["running"] = False
        if _own is not None:
            _own.__exit__(None, None, None)
