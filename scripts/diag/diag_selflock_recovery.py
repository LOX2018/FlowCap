# -*- coding: utf-8 -*-
"""自锁恢复的真机判据（只读，不启 BCC/backend、不做任何写操作）。

用途：证明「会话活性门禁」能区分两种凭证，并据此让保活回写走对分支：
  - 账号 .env 里那套（扫码瞬间快照）→ 服务端**不承认** → 门禁应判 False（拒写）
  - profile 里那套（浏览器实时态）  → 服务端**承认**     → 门禁应判 True（放行写回）
两条结论同时成立，才说明自锁（坏快照被永久固化）被打破。

跑法（账号名只从环境变量取，脚本内不写死任何真实账号）：
    set DY_DIAG_ACCOUNT=<账号名>
    python scripts/diag/diag_selflock_recovery.py
"""
import os
import sqlite3
import sys

_DESIGN_ROOT = r"C:\temp\flowcap_design"
_FORBIDDEN = (r"C:\temp\flowcap_test",)
if os.path.abspath(_DESIGN_ROOT) in [os.path.abspath(x) for x in _FORBIDDEN]:
    sys.exit("[环境门禁] 拒绝在主分支环境运行")
os.environ["FLOWCAP_APP_ROOT"] = _DESIGN_ROOT

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
_BACKEND = os.path.join(_REPO, "backend")
sys.path.insert(0, _BACKEND)
os.chdir(_BACKEND)

ACCOUNT = (os.environ.get("DY_DIAG_ACCOUNT") or "").strip()
if not ACCOUNT:
    sys.exit("请设置 DY_DIAG_ACCOUNT=<账号名>（脚本不内置任何真实账号名）")


def _env_cookie(name):
    from services import member_ctx
    from auto_dm import accounts as A
    p = A.env_path_of(name)
    d = member_ctx.parse_env_dict(p)
    return (d.get("DY_COOKIES") or "").strip()


def _profile_cookie(name):
    from auto_dm import accounts as A
    p = A.env_path_of(name)
    base = os.path.dirname(p)
    db = os.path.join(base, "profile", "_camoufox", "cookies.sqlite")
    if not os.path.exists(db):
        return "", db
    con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    rows = con.execute(
        "SELECT name,value FROM moz_cookies WHERE host LIKE '%douyin.com'"
    ).fetchall()
    con.close()
    return "; ".join(f"{n}={v}" for n, v in rows), db


def _probe(cookie_str):
    from auto_dm import accounts as A
    from dy_apis.login_api import DouyinAuth

    class _Auth(DouyinAuth):
        pass

    a = _Auth()
    d = {}
    for kv in cookie_str.split(";"):
        kv = kv.strip()
        if "=" in kv:
            k, v = kv.split("=", 1)
            d[k.strip()] = v
    a.cookie = d
    a.cookie_str = cookie_str
    return A._live_session_probe_raw(a)


def main() -> int:
    print("=" * 74)
    print(f"自锁恢复判据 · 账号[{ACCOUNT}] · 环境={_DESIGN_ROOT}")
    print("=" * 74)

    env_ck = _env_cookie(ACCOUNT)
    prof_ck, db = _profile_cookie(ACCOUNT)
    print(f".env  cookie 字段数 = {len([x for x in env_ck.split(';') if '=' in x])}")
    print(f"profile cookie 字段数 = {len([x for x in prof_ck.split(';') if '=' in x])}  ({db})")

    env_state, env_detail = _probe(env_ck)
    prof_state, prof_detail = _probe(prof_ck)
    print(f"\n[1] .env 凭证 → 会话活性 = {env_state}   （门禁应 False 才拦得住坏快照）")
    print(f"    {env_detail}")
    print(f"[2] profile 凭证 → 会话活性 = {prof_state} （门禁应 True 才允许写回）")
    print(f"    {prof_detail}")

    ok = True
    if env_state is True:
        print("\n⚠️ .env 凭证竟被服务端承认 —— 该账号此刻没有自锁问题（无需恢复）。")
    elif env_state is False and prof_state is True:
        print("\n✅ 自锁恢复链成立：坏快照会被门禁拦下(False)，"
              "而保活从 profile 读到的实时态会被放行(True) → .env 能被刷新为可用会话。")
    else:
        ok = False
        print(f"\n❌ 判据未成立（.env={env_state}, profile={prof_state}）——"
              f" 需人工在指纹浏览器重新登录该账号后复测。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
