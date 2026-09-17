import os
import threading
# from loguru import logger
from dotenv import load_dotenv

dy_auth = None
dy_live_auth = None

# 2026-09-17 修补（OCR 审查 HIGH）：串行化「改 environ + 重绑全局」。
# 本模块的 dy_auth / dy_live_auth 是**进程级单例**，而 load_env 会被不同账号
# 并发调用 → 无锁时后完成者覆盖前者（跨账号凭证错用）。
_env_lock = threading.RLock()


def load_env(env_path: str | None = None):
    """加载凭证环境。

    2026-09-06 全局治理「凭证唯一真相源」护栏：
    原实现是**裸 `load_dotenv()`**（无参数）——会从【当前工作目录】向上
    找任意 .env 并注入 os.environ，而项目根 .env 历史上存着 2026-08-15
    的陈旧 DY_ 凭证，会把【账号 .env 的真实凭证】直接覆盖掉，
    表现为「凭证莫名其妙失效 / 连上的是旧账号」。

    护栏：
      1. 传 env_path 时：只加载该账号 .env（override=True，账号内自洽）
      2. 不传 env_path 时：**明确加载根 .env 且 override=False**——
         只补充真正缺失的键，绝不覆盖进程里已有的账号凭证
      3. 根 .env 已于 2026-09-06 清空 DY_ 凭证键（备份 .env.stale_backup_20260906）

    凭证唯一真相源 = 各账号 auto_dm/accounts/<name>/.env
    """
    global dy_auth, dy_live_auth

    # 2026-09-17 修补（OCR 审查 HIGH —— 多账号并发互相覆盖）：
    # 原实现无锁地重绑模块级 `dy_auth` / `dy_live_auth`，而本函数会被
    # **不同账号并发调用**（如 api/linkmic.py 的每账号解析、dy_live/server.py
    # 的监听启动）。两个账号同时进入时，后完成者会覆盖前者的全局引用 ——
    # `common_util.dy_auth` 可能指向**别的账号**的凭证（跨账号错用）。
    # 另外 `load_dotenv(..., override=True)` 是**进程级**副作用，同样无法并发安全。
    # 现有手段（不改动既有调用方）：
    #   ① 用模块锁把整段"改 environ + 重绑全局"串行化；
    #   ② 返回值始终是**本次**构造的 auth 对象（调用方优先用返回值）。
    # 注：彻底消除跨账号污染需改造为"按账号取 auth、不落全局"，属架构改动，
    # 见 TODO 注释；本次先消除可观测的并发撕裂。
    with _env_lock:
        if env_path:
            load_dotenv(env_path, override=True)
        else:
            # 不指定账号时：只补充、不覆盖（override=False 是护栏核心）
            load_dotenv(override=False)
        cookies_dy = os.getenv('DY_COOKIES')
        cookies_live = os.getenv('DY_LIVE_COOKIES')
        from builder.auth import DouyinAuth
        _auth = DouyinAuth()
        _auth.perepare_auth(cookies_dy, "", "")
        _auth.ticket = os.getenv('DY_TICKET') or None
        _auth.ts_sign = os.getenv('DY_TS_SIGN') or None
        _auth.client_cert = os.getenv('DY_CLIENT_CERT') or None
        _auth.private_key = os.getenv('DY_PRIVATE_KEY') or None
        _live = DouyinAuth()
        _live.perepare_auth(cookies_live, "", "")
        dy_auth = _auth
        dy_live_auth = _live
    return _auth

def init():
    media_base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../datas/media_datas'))
    excel_base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../datas/excel_datas'))
    for base_path in [media_base_path, excel_base_path]:
        if not os.path.exists(base_path):
            os.makedirs(base_path)
            # logger.info(f'create {base_path}')
    cookies = load_env()
    base_path = {
        'media': media_base_path,
        'excel': excel_base_path,
    }
    return cookies, base_path
