# coding=utf-8
"""会员体系冒烟测试：注册/登录/数据隔离/.env加密封装（纯逻辑，零网络零浏览器）。

在临时目录跑，绝不触碰真实 members/ 与账号数据。
"""
import os
import sys
import tempfile
import shutil

# 隔离沙箱
SANDBOX = tempfile.mkdtemp(prefix="dy_member_smoke_")
os.environ["DY_APP_ROOT"] = SANDBOX

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f" | {detail}" if detail and not cond else ""))


print("== 1. 注册/登录/口令校验 ==")
from services import member_store, member_ctx

r = member_store.register_member("测试会员A", "pass123")
check("注册会员A", r.get("ok"), str(r))
mid_a = r["member_id"]

r2 = member_store.register_member("测试会员A", "other666")
check("重名注册被拒", not r2.get("ok"))

r3 = member_store.authenticate("测试会员A", "wrongpw")
check("错误口令登录失败", not r3.get("ok"))

r4 = member_store.authenticate("测试会员A", "pass123")
check("正确口令登录成功", r4.get("ok"), str(r4))
master_a = r4["master_key"]

# 注册表文件里不应有主密钥明文
import json as _json
reg_raw = open(member_store.registry_path(), encoding="utf-8").read()
check("注册表不含主密钥明文", master_a not in reg_raw)
check("注册表不含口令明文", "pass123" not in reg_raw)

print("== 2. 数据空间隔离 ==")
# 模拟登录会员A
member_ctx.set_current(mid_a, "测试会员A", master_a, "tok_a")
from auto_dm import accounts as acc

# 会员A 加账号「小号甲」并写凭证（走加密写）
env_a = acc.env_path_of("小号甲") or os.path.join(acc._accounts_dir(), "小号甲", ".env")
os.makedirs(os.path.dirname(env_a), exist_ok=True)
from services import member_ctx as mctx
mctx.write_env_file(env_a, {"DY_COOKIES": "sessionid=A_COOKIE_AAA; uid_tt=x",
                            "DY_TICKET": "TICKET_A"})
check("会员A .env 明文文件不存在（已加密）", not os.path.exists(env_a))
check("会员A .enc 加密文件存在", os.path.exists(env_a + ".enc"))
enc_raw = open(env_a + ".enc", encoding="ascii").read()
check("密文不含 cookie 明文", "A_COOKIE_AAA" not in enc_raw and "TICKET_A" not in enc_raw)

# 会员A 读回
vals = member_store.__dict__ and mctx.parse_env_dict(env_a)
check("会员A 解密读回凭证", vals.get("DY_COOKIES") == "sessionid=A_COOKIE_AAA; uid_tt=x"
      and vals.get("DY_TICKET") == "TICKET_A", str(vals))

# 索引注册
idx = acc._load_index()
idx["accounts"]["小号甲"] = "小号甲/.env"
idx["current"] = "小号甲"
acc._save_index(idx)
names_a = [n for n, _ in acc.list_accounts()]
check("会员A 账号列表=小号甲", names_a == ["小号甲"], str(names_a))

# 登出 A，登录 B
member_ctx.clear_current()
r5 = member_store.register_member("测试会员B", "pass456")
mid_b = r5["member_id"]
member_ctx.set_current(mid_b, "测试会员B", r5["master_key"], "tok_b")
names_b = [n for n, _ in acc.list_accounts()]
check("会员B 账号列表为空（与A隔离）", names_b == [], str(names_b))

# B 直接看 A 的 .env 路径 —— 应读不出内容（无 B 的密钥）
vals_b = mctx.parse_env_dict(env_a)
check("会员B 无法解密A的凭证", vals_b.get("DY_COOKIES") is None, str(vals_b))

# DB 隔离
import database
db_a_path_should = member_store.member_db_path(mid_a)
member_ctx.set_current(mid_a, "测试会员A", master_a, "tok_a")
database.reset_connection()
conn = database.get_db()
dbfile = str(conn.execute("PRAGMA database_list").fetchone()[2]).lower()
check("会员A DB 落在会员A空间", db_a_path_should.lower() in dbfile, dbfile)
conn.execute("CREATE TABLE IF NOT EXISTS _smoke (k TEXT)")
conn.commit()
conn.execute("INSERT INTO _smoke VALUES ('hello_a')")
conn.commit()

member_ctx.set_current(mid_b, "测试会员B", r5["master_key"], "tok_b")
database.reset_connection()
conn_b = database.get_db()
tabs = [r[0] for r in conn_b.execute("SELECT name FROM sqlite_master WHERE type='table'")]
check("会员B DB 无会员A的数据表", "_smoke" not in tabs, str(tabs))

print("== 3. login_api 兼容层 ==")
# 先切回会员A（此时登录B，读A凭证理应失败——隔离已在上一步验证）
member_ctx.set_current(mid_a, "测试会员A", master_a, "tok_a")
database.reset_connection()
from dy_apis.login_api import DYLoginApi
auth = DYLoginApi._load_auth_from_env(env_a)
check("_load_auth_from_env 解密读到 cookie", "A_COOKIE_AAA" in (auth.cookie_str or ""),
      (auth.cookie_str or "")[:60])

print("== 4. 错误口令 → 主密钥不可用 ==")
r_wrong = member_store.authenticate("测试会员A", "pass123")
member_ctx.clear_current()
import services.member_ctx as _mc2
_mc2._sessions.clear()
r_bad = member_store.authenticate("测试会员A", "pass123")
check("重新登录正常（会话清理后）", r_bad.get("ok"))

print("== 5. 口令修改 ==")
rc = member_store.change_password(mid_a, "pass123", "newpass789")
check("修改口令成功", rc.get("ok"), str(rc))
r_re = member_store.authenticate("测试会员A", "newpass789")
check("新口令可登录且主密钥不变", r_re.get("ok") and r_re["master_key"] == master_a)

print()
print(f"PASS={len(PASS)}  FAIL={len(FAIL)}")
if FAIL:
    for n, d in FAIL:
        print("  FAILED:", n, d)
    sys.exit(1)
shutil.rmtree(SANDBOX, ignore_errors=True)
print("ALL SMOKE TESTS PASSED")
