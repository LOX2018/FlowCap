# coding=utf-8
"""会员体系 API 路由（v0.37.0）。

路由（前缀 /api/member）：
  POST /register           开放自助注册（用户选定方案）
  POST /login              口令登录，成功返回 token
  POST /logout             注销当前会话
  GET  /state              前端启动时查询登录态（无需 token）
  POST /change-password    修改口令（需登录）
  GET  /list               会员列表（脱敏，不含任何密钥材料）

鉴权模型：
  - 其余业务路由（/api/overview 等）经 middleware 强制校验 X-Member-Token；
    未登录一律 401（豁免：/api/member/*、/api/status 健康探测、/api/live/ws）。
  - token 是进程内随机 hex，重启即失效（本地软件场景可接受）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from services import member_store, member_ctx

router = APIRouter()


class AuthIn(BaseModel):
    username: str
    password: str


class RegisterIn(AuthIn):
    pass


class ChangePwdIn(BaseModel):
    oldPassword: str
    newPassword: str


class DeleteIn(BaseModel):
    memberId: str
    password: str


@router.post("/register")
async def register(body: RegisterIn) -> dict:
    # 注册要生成 PBKDF2 600k 轮哈希 + Fernet 密钥，同样不能阻塞事件循环
    r = await asyncio.to_thread(
        member_store.register_member, body.username, body.password)
    if not r.get("ok"):
        raise HTTPException(400, r.get("msg", "注册失败"))
    return {"ok": True, "memberId": r["member_id"]}


def _bootstrap_accounts_index() -> int:
    """会员空间账号基础设施为空时，从全局空间引导（一次性，幂等）。

    背景（2026-09-08 实测缺口）：账号索引存 SQLite kv_store（key=accounts_index），
    会员登录后 database 切到会员空间 DB（空索引）→ list_accounts()=0 →
    守护永不拉起 → 前端永远「拉后端对齐」。且即使复制索引，会员空间的
    accounts/<name>/.env 与 profile 也不存在 → BCC 报 profile 不存在 / 需重扫码。

    方案（三步，全部幂等）：
    1. 账号目录：对全局 <app_root>/auto_dm/accounts/<name> 建 Windows junction
       到会员空间同名路径 —— .env 与 profile 物理同一份（单 profile 铁律不破坏，
       零复制、零重扫码），逻辑上会员空间可见。
    2. 账号索引：全局 DB 的 accounts_index 复制进会员 DB 的 kv_store。
    3. 只在会员侧缺失/为空时动作；已有账号的会员空间不受影响。
    """
    import json
    import sqlite3
    import subprocess
    from database import get_kv_json, set_kv_json
    import vbrowser

    root = vbrowser.app_root()
    global_acc_dir = os.path.join(root, "auto_dm", "accounts")
    member_acc_dir = member_ctx.accounts_root()
    if member_acc_dir is None:
        return 0
    os.makedirs(member_acc_dir, exist_ok=True)

    # 1) 读全局索引决定要引导哪些账号
    global_db = os.path.join(root, "data", "flowcap.db")
    accounts: dict = {}
    if os.path.isfile(global_db):
        conn = sqlite3.connect(global_db, timeout=5)
        try:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key='accounts_index'").fetchone()
            if row:
                accounts = (json.loads(row[0]).get("accounts")) or {}
        finally:
            conn.close()
    if not accounts:
        return 0

    # 2) 为每个账号建 junction（已存在则跳过）
    linked = 0
    for name in accounts:
        src = os.path.join(global_acc_dir, name)
        dst = os.path.join(member_acc_dir, name)
        if not os.path.isdir(src):
            logger.warning(f"[MEM-008] " + f"[member] 全局账号目录缺失，跳过: {name}")
            continue
        if os.path.exists(dst):
            linked += 1
            continue
        try:
            r = subprocess.run(["cmd", "/c", "mklink", "/J", dst, src],
                               capture_output=True)
            if r.returncode == 0 and os.path.isdir(dst):
                linked += 1
            else:
                logger.error(f"[MEM-009] " + "[member] junction 创建失败: "
                    f"{r.stderr.decode('gbk', errors='replace').strip()}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"[MEM-009] " + f"[member] junction 异常 {name}: {e}")

    # 3) 索引复制（仅会员侧为空时）
    idx = get_kv_json("accounts_index", None)
    if not (idx and idx.get("accounts")):
        # current 必须落到真实账号：否则 current_name() 返回 None，
        # overview 的 browser_daemon_port()/recv_daemon_port() 会按 None 算出
        # 错端口（守护恒显示离线），且多处按 current 取账号的逻辑会空转。
        _first = next(iter(accounts), None)
        set_kv_json("accounts_index", {"current": _first, "accounts": accounts})
    return linked


async def _post_login_init(member_id: str, master_key: str) -> None:
    """登录后初始化（后台任务）：.env 加密迁移 + 守护拉起。

    全部重活丢 to_thread：ensure_daemons_for 内部有同步 socket 探测
    （每端口 0.2~0.3s）与 subprocess.Popen（拉起 114MB onefile 会短暂
    打满 CPU/磁盘），绝不能在事件循环里直跑。
    """
    # ② 明文 .env 一次性加密迁移（幂等，本地文件 I/O）
    try:
        mig = await asyncio.to_thread(
            member_ctx.migrate_plain_envs, member_id, master_key)
        if mig.get("migrated"):
            logger.info(f"[member] 凭证加密迁移完成: {mig}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[MEM-002] " + f"[member] .env 迁移异常（不阻塞登录）: {e}")
    # ②b 账号索引 bootstrap（2026-09-08 实测缺口）：账号索引存在全局 DB 的
    #  kv_store.accounts_index，会员登录后 database 切到会员空间 DB（空索引）
    #  → list_accounts()=0 → 守护永不拉起 → 前端「拉后端对齐」无限转。
    #  会员空间索引为空且全局索引有账号时，一次性复制过来（幂等）。
    try:
        n_copied = await asyncio.to_thread(_bootstrap_accounts_index)
        if n_copied:
            logger.info(f"[member] 账号空间引导完成: {n_copied} 个账号(junction+索引)")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[MEM-007] " + f"[member] 账号索引引导失败（不阻塞登录）: {e}")
    # ②c dm_pool 分区迁移补跑（2026-10-04 审查发现的数据丢失窗口）：
    #  lifespan 里的迁移早于 `restore_persisted_session()`，此刻 member_ctx
    #  仍为 None，目标库靠 `members/.session.json` 回退解析。若该文件缺失
    #  （用户登出/被清）而会员库里还有 live.dm_pool ⇒ 迁移落在全局库上空跑，
    #  而读取方已改读 send.dm_pool ⇒ 本次会话词库静默为空。
    #  登录后此处再跑一次（幂等：send 已有值或无旧值均跳过），补上该窗口。
    try:
        from services import app_config as _ac
        moved = await asyncio.to_thread(_ac.migrate_dm_pool_live_to_send)
        if moved:
            logger.info(f"[member] 私信词库分区迁移完成（登录后补跑）: {moved} 处")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[MEM-010] " + f"[member] dm_pool 分区迁移失败（不阻塞登录）: {e}")
    # ③ 拉起该会员账号的守护（等效原 lifespan 的 _auto_start_daemons）
    try:
        from auto_dm.daemon_launcher import ensure_daemons_for
        from auto_dm import accounts as _acct
        names = [n[0] if isinstance(n, (tuple, list)) else n
                 for n in await asyncio.to_thread(_acct.list_accounts)]
        for n in names:
            await asyncio.to_thread(ensure_daemons_for, n, False)
        logger.info(f"[member] 登录初始化完成（{len(names)} 个账号守护拉起）")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[MEM-003] " + f"[member] 登录后守护拉起失败（不影响登录）: {e}")


@router.post("/login")
async def login(body: AuthIn) -> dict:
    # 同步直调会卡死 FastAPI 事件循环（所有轮询/WS 冻结）——必须丢线程池。
    r = await asyncio.to_thread(member_store.authenticate, body.username, body.password)
    if not r.get("ok"):
        # 登录失败不区分「用户名不存在」与「口令错误」（防枚举）
        raise HTTPException(401, r.get("msg", "登录失败"))
    token = member_ctx.create_session(r["member_id"], body.username.strip(),
                                      r["master_key"])
    member_ctx.set_current(r["member_id"], body.username.strip(),
                           r["master_key"], token)
    # 登录后初始化会员数据空间：
    # ① 重置 DB 连接（get_db 下次按会员路径重建 + 建表/迁移）——快速，留在请求内
    try:
        import database
        database.reset_connection()
        database.get_db()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[MEM-001] " + f"[member] 会员 DB 初始化失败: {e}")

    # ②③ .env 加密迁移 + 守护拉起：整体移入后台任务，绝不在请求路径里做
    # （原实现在登录请求内同步跑 ensure_daemons_for：每账号 4 次 0.2~0.3s 的
    #  socket 探测 + Popen 拉起 114MB onefile（随即解包打满 CPU/磁盘），叠加
    #  PBKDF2 曾把事件循环冻住数秒——前端表现为「登录时连接后端卡死」）。
    asyncio.get_running_loop().create_task(_post_login_init(r["member_id"], r["master_key"]))

    logger.info(f"[member] 登录成功: {body.username}")
    return {
        "ok": True,
        "token": token,
        "memberId": r["member_id"],
        "username": body.username.strip(),
    }


@router.post("/logout")
async def logout(request: Request) -> dict:
    token = request.headers.get("x-member-token", "")
    member_ctx.destroy_session(token)
    member_ctx.clear_current()
    # DB 连接回默认路径（下次 get_db 重建）
    try:
        import database
        database.reset_connection()
    except Exception:
        pass
    return {"ok": True}


@router.get("/state")
async def state(request: Request) -> dict:
    """前端启动时查询登录态。带有效 token 时返回会员信息。"""
    token = request.headers.get("x-member-token", "")
    s = member_ctx.get_session(token)
    if not s:
        return {"loggedIn": False}
    return {"loggedIn": True, "username": s["username"], "memberId": s["member_id"]}


@router.post("/change-password")
async def change_password(request: Request, body: ChangePwdIn) -> dict:
    s = member_ctx.get_session(request.headers.get("x-member-token", ""))
    if not s:
        raise HTTPException(401, "未登录")
    # 同 login：PBKDF2 两次派生 ~700ms，不能阻塞事件循环
    r = await asyncio.to_thread(member_store.change_password,
                                s["member_id"], body.oldPassword, body.newPassword)
    if not r.get("ok"):
        raise HTTPException(400, r.get("msg", "修改失败"))
    return {"ok": True}


@router.get("/list")
async def list_members() -> dict:
    return {"members": member_store.list_members()}


@router.post("/delete")
async def delete_member(request: Request, body: DeleteIn) -> dict:
    """删除会员（需该会员口令确认）。删除后若删的是当前登录会员则强制登出。"""
    # 口令校验含 PBKDF2 派生，丢线程池
    r = await asyncio.to_thread(
        member_store.delete_member, body.memberId, body.password)
    if not r.get("ok"):
        raise HTTPException(400, r.get("msg", "删除失败"))
    cur = member_ctx.current()
    if cur and cur.get("member_id") == body.memberId:
        member_ctx.clear_current()
    return {"ok": True}
