import os
# from loguru import logger
from dotenv import load_dotenv

dy_auth = None
dy_live_auth = None


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
    if env_path:
        load_dotenv(env_path, override=True)
    else:
        # 不指定账号时：只补充、不覆盖（override=False 是护栏核心）
        load_dotenv(override=False)
    cookies_dy = os.getenv('DY_COOKIES')
    cookies_live = os.getenv('DY_LIVE_COOKIES')
    from builder.auth import DouyinAuth
    dy_auth = DouyinAuth()
    dy_auth.perepare_auth(cookies_dy, "", "")
    dy_auth.ticket = os.getenv('DY_TICKET') or None
    dy_auth.ts_sign = os.getenv('DY_TS_SIGN') or None
    dy_auth.client_cert = os.getenv('DY_CLIENT_CERT') or None
    dy_auth.private_key = os.getenv('DY_PRIVATE_KEY') or None
    dy_live_auth = DouyinAuth()
    dy_live_auth.perepare_auth(cookies_live, "", "")
    return dy_auth

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
