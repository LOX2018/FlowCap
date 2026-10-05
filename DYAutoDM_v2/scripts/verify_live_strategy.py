# -*- coding: utf-8 -*-
"""v0.43.93 验证：直播策略（纯策略）重构 + force_rescan 彻底移除。

用户原话（2026-09-19，即本批设计契约）：
  「直播策略放到直播监听页面中『直播间』板块的配置标签中，该编辑页面中只保留
   直播策略，策略以外的全部删除。不要『监听 / 目标直播间 / 配置标签』tab 切换栏，
   也不需要子 tab 页面。『强制重扫』策略早就废弃了，彻底移除。」

四层判据（缺一层即证据不足）：
  A 静态：策略无身份字段 / 无子 tab 组件 / force_rescan 全仓无残留 / 无目标直播间模块
  B 真模块（隔离 DB）：策略 CRUD 与「删除解绑」契约
  C 真 HTTP（TestClient）：端点语义（含 400 显式失败）
  D 后端引擎：apply_runtime_config 不再产出 force_rescan

跑法：cd backend && python ../scripts/verify_live_strategy.py
（不启 uvicorn、不碰浏览器、不碰会员环境；DY_APP_ROOT 指向临时目录）
"""
import ast
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BK = os.path.join(ROOT, "backend")
FE = os.path.join(ROOT, "frontend", "src")

# ── 环境隔离门禁（验证脚本自设，绝不污染用户实例）─────────────────────────
_TMP = os.path.join(tempfile.gettempdir(), "dyautodm_strategy_verify")
FORBIDDEN = (r"C:\temp\dyautodm_design", r"C:\temp\dyautodm_test")
if os.path.abspath(_TMP) in [os.path.abspath(x) for x in FORBIDDEN]:
    raise SystemExit("环境门禁失败：临时目录与用户环境重合")
os.environ["DY_APP_ROOT"] = _TMP
shutil.rmtree(_TMP, ignore_errors=True)
os.makedirs(_TMP, exist_ok=True)
sys.path.insert(0, BK)

PASS = FAIL = 0
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  FAIL {name}  {detail}")


def src(rel: str, base: str = BK) -> str:
    with open(os.path.join(base, rel), "rb") as f:
        return f.read().decode("utf-8")


def py_files(base: str):
    for dirpath, _dirs, files in os.walk(base):
        if "__pycache__" in dirpath or "build" in dirpath:
            continue
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def fe_files():
    for dirpath, _dirs, files in os.walk(FE):
        for fn in files:
            if fn.endswith((".ts", ".tsx")):
                yield os.path.join(dirpath, fn)


# ══════════════════════════════════════════════════════════════════════════
print("A. 静态契约")

lc = src("api/live_config.py")
live_fe = src("components/live/live-page.tsx", FE)
rcp = src("components/live/RoomConfigPage.tsx", FE)
cli = src("api/client.ts", FE)

# A1 策略字段白名单：无身份字段、无 force_rescan
field_block = lc.split("_FIELDS = {")[1].split("}")[0]
forbidden_in_fields = [k for k in ("room_id", "live_url", "force_rescan") if k in field_block]
check("A1 策略字段白名单不含身份字段（room_id/live_url/force_rescan）",
      not forbidden_in_fields, f"命中={forbidden_in_fields}")
check("A2 策略字段仍含全部发送策略项",
      all(k in field_block for k in ("max_target", "interval", "delay", "dm_pool",
                                     "acct", "auto_link_mic", "link_mic_mode")),
      f"block={field_block.strip()[:120]}")

# A3 策略体模型无身份字段
body_block = lc.split("class StrategyBody(BaseModel):")[1].split("class ")[0]
check("A3 StrategyBody 无 room_id / live_url / force_rescan 字段",
      not any(re.search(rf"^\s+{k}:", body_block, re.M)
              for k in ("room_id", "live_url", "force_rescan")),
      body_block[:200])
check("A4 新键由服务端生成 lc_ 前缀（不再用 room_id 当键）",
      "def new_strategy_id" in lc and 'f"lc_{int(time.time()' in lc)

# A5 force_rescan 全仓彻底移除（后端业务代码）
# 白名单：live_config._normalize_stored 的「剥除列表」含这些**字符串常量** ——
# 它的职责正是把存量记录里的废弃字段删掉，属于合法引用（不是读写该配置项）。
_ALLOWED = {os.path.join("api", "live_config.py")}
hits = []
for f in py_files(BK):
    name = os.path.basename(f)
    if name.startswith("test_"):
        continue
    rel = os.path.relpath(f, BK)
    if rel in _ALLOWED:
        continue
    t = open(f, "rb").read().decode("utf-8", "replace")
    if re.search(r"\bforce_rescan\b|\bforceRescan\b", t):
        hits.append(rel)
check("A5 后端业务代码无 force_rescan / forceRescan 残留（白名单：存量剥除列表）",
      not hits, f"命中={hits}")

# A5b 白名单文件里这些名字**只作为「要剥除的脏字段」出现**，不得被读作配置
_lc_src = src("api/live_config.py")
check("A5b 白名单文件仅在剥除列表/注释中出现该字段名（非配置读写）",
      _lc_src.count("force_rescan") <= 2 and "_normalize_stored" in _lc_src,
      f"出现 {_lc_src.count('force_rescan')} 次")

# A6 force_fresh 仍保留（凭证过龄/重新扫码两条合法路径）—— 移除 force_rescan 不得误伤它
adm = src("core/auto_dm.py")
check("A6 _build_one_auth 的 force_fresh 能力保留（只移除 force_rescan 配置项）",
      "def _build_one_auth" in adm and "force_fresh" in adm and "max_age" in adm)
check("A6b 重新扫码重建路径不再读 force_rescan（改为直接 True）",
      "force_fresh=True, max_age=0" in adm and "self.force_rescan" not in adm)

# A7 前端无 force_rescan / 无目标直播间
fhits = []
for f in fe_files():
    t = open(f, "rb").read().decode("utf-8", "replace")
    if re.search(r"force_rescan|forceRescan", t):
        fhits.append(os.path.relpath(f, FE))
check("A7 前端无 force_rescan / forceRescan 残留", not fhits, f"命中={fhits}")

# A8 无子 tab / 无目标直播间页
check("A8 live-page 无 subView 子 tab 状态",
      "subView" not in live_fe, "仍存在 subView")
check("A8b 直播页无「目标直播间 / 配置标签」tab 文案",
      "目标直播间" not in live_fe and '"配置标签"' not in live_fe)
check("A8c TargetRoomPage.tsx 已删除",
      not os.path.exists(os.path.join(FE, "components", "live", "TargetRoomPage.tsx")))
check("A8d 后端 target_rooms 模块已删除",
      not os.path.exists(os.path.join(BK, "api", "target_rooms.py")))
check("A8e main.py 不再注册 target-rooms",
      "target_rooms" not in src("main.py"))

# A9 策略编辑器：只保留策略字段
check("A9 策略编辑页保留全部策略控件",
      all(k in rcp for k in ("策略名称", "发送上限", "间隔（秒）", "延迟抖动（秒）",
                             "私信词库", "自动申请连麦", "连麦方式", "监听账号")))
_rcp_jsx = rcp.split("export default function RoomConfigPage")[-1]
check("A9b 策略编辑页 JSX **不含**身份/废弃字段控件",
      not any(k in _rcp_jsx for k in ("备注名", "直播间号或 URL", "直播链接", "强制重扫")),
      "仍出现身份/废弃字段")
check("A9c 新建草稿无身份字段",
      not re.search(r"EMPTY_DRAFT[^}]*room_id", rcp, re.S)
      and not re.search(r"EMPTY_DRAFT[^}]*force_rescan", rcp, re.S))
# 草稿模式（2026-09-19 用户第三次定调：取消「新建策略」按钮）
_rcp_jsx = rcp.split("export default function RoomConfigPage")[-1]
_rcp_footer = _rcp_jsx.split("flex justify-end gap-2")[-1]
check("A9d 已取消「新建策略」按钮（用户要求：改为有变化即草稿模式）",
      "＋ 新建策略" not in _rcp_jsx and "const startNew" not in _rcp_jsx)
check("A9e strategy-new 回归「清空表单」，仍在保存按钮左侧",
      'data-od-id="strategy-new"' in _rcp_footer and "清空表单" in _rcp_footer
      and _rcp_footer.index('data-od-id="strategy-new"') < _rcp_footer.index("save}"),
      "版式不符")
check("A9f 草稿模式：有变化即出现列表区草稿行（用户指明的位置）",
      "const [dirty, setDirty]" in _rcp_jsx and "const touch =" in _rcp_jsx
      and 'data-od-id="strategy-draft-row"' in _rcp_jsx
      and "const draftActive = dirty && !editing" in _rcp_jsx)
check("A9g 标题区文案 =「策略详情」（用户指定）", "策略详情" in _rcp_jsx)
check("A10 策略为弹窗（data-od-id=live-strategy-modal），非整页子视图",
      'data-od-id="live-strategy-modal"' in rcp and "fixed inset-0 z-50" in rcp)

# A11 直播间板块内嵌策略选择 + 管理入口
sec = live_fe.split('title="直播间"')[1][:2500] if 'title="直播间"' in live_fe else ""
check("A11 策略下拉在「直播间」板块内",
      "选择直播策略" in sec and "selCfgId" in sec, f"sec={sec[:160]}")
check("A11b 「直播间」板块内有管理策略按钮（弹窗入口）",
      "管理策略" in sec and "setCfgMgr(true)" in sec)
_st_jsx = live_fe.count("<SegmentedTabs")
check("A12 无三 tab 切换栏（只剩「单账户 / 多账户总览」这一处切换）",
      _st_jsx == 2, f"JSX <SegmentedTabs 次数={_st_jsx}")

# A13 前端 API 清理
check("A13 前端无目标直播间 API / 类型",
      "listTargetRooms" not in cli and "export interface TargetRoom" not in cli)
check("A13b RoomConfig 类型无身份/废弃字段",
      not re.search(r"interface RoomConfig \{[^}]*force_rescan", cli, re.S)
      and not re.search(r"interface RoomConfig \{[^}]*live_url", cli, re.S))

# ══════════════════════════════════════════════════════════════════════════
print("B. 真模块（隔离 DB，不起服务）")
import asyncio  # noqa: E402
import database  # noqa: E402
from api import live_config as LC  # noqa: E402

_run = asyncio.run


def reset_kv():
    conn = database.get_db()
    conn.execute("DELETE FROM kv_store")
    conn.commit()


reset_kv()

r1 = _run(LC.save_strategy(LC.StrategyBody(name="标准-快速", max_target=50, interval=45,
                                           delay="40,80", auto_link_mic=True,
                                           dm_pool=[{"text": "你好", "enabled": True}])))
r2 = _run(LC.save_strategy(LC.StrategyBody(name="保守-慢速", max_target=8, interval=300)))
check("B1 可并存多条策略", r1.get("ok") and r2.get("ok")
      and r1["config"]["id"] != r2["config"]["id"], f"{r1} | {r2}")
check("B1b 两条策略参数互不覆盖",
      r1["config"]["max_target"] == 50 and r2["config"]["max_target"] == 8)
check("B1c 策略记录**不含任何身份字段**",
      not ({"room_id", "live_url", "force_rescan"} & set(r1["config"].keys())),
      f"keys={sorted(r1['config'].keys())}")
check("B1d id 为服务端生成的 lc_ 前缀", str(r1["config"]["id"]).startswith("lc_"))

sid1 = r1["config"]["id"]
r3 = _run(LC.save_strategy(LC.StrategyBody(id=sid1, name="标准-快速", max_target=60)))
lst = LC._load_all()
check("B2 同 id 保存为更新而非新增", len(lst) == 2 and r3["config"]["max_target"] == 60,
      f"n={len(lst)}")
check("B2b 未传字段沿用旧值（interval 保留）", lst[sid1].get("interval") == 45)

r4 = _run(LC.save_strategy(LC.StrategyBody(id="!!非法!!", name="x")))
check("B3 非法 id 显式失败（不静默生成）", r4.get("ok") is False, f"{r4}")

r5 = _run(LC.save_strategy(LC.StrategyBody(name="中文名策略")))
check("B4 中文/自定义名称可作策略（通过 name 区分，不靠 room_id）", r5.get("ok"))

# 删除 → 只删自己；无 target_rooms 体系后不再有解绑副作用
_before = set(LC._load_all().keys())
d = _run(LC.delete_strategy(r2["config"]["id"]))
_after = set(LC._load_all().keys())
check("B5 删除策略：仅少被删的那一条（其它完整保留）",
      d.get("ok") and (r2["config"]["id"] not in _after)
      and _before - _after == {r2["config"]["id"]} and r1["config"]["id"] in _after,
      f"before={len(_before)} after={len(_after)} d={d}")
check("B5b 删除不存在的策略显式失败",
      _run(LC.delete_strategy("nope")).get("ok") is False)

# 直播间号只用于推导 live_url（策略本身不含）
check("B6 resolve_live_url 由 room_id 推导（策略不自带 live_url）",
      LC.resolve_live_url("840377749201") == "https://live.douyin.com/840377749201")
check("B6b 无 room_id → 空（保持任务原有直播间，换房间属换任务）",
      LC.resolve_live_url("") == "" and LC.resolve_live_url(None) == "")
# 存量旧记录（v0.43.92 形态：键=直播间号，体内带身份/废弃字段）读取时必须归一化
_before = LC._load_all()
LC._save_all({**_before, "992931212705_old": {
    "room_id": "992931212705", "name": "旧记录", "live_url": "992931212705",
    "max_target": 100, "force_rescan": False, "dm_pool": [],
}})
_items = LC._run_sync_list = None
import asyncio as _a  # noqa: E402
_lst = _a.run(LC.list_strategies())["items"]
_old = [x for x in _lst if x["id"] == "992931212705_old"][0]
check("B7 存量旧记录读取即归一化：剥掉 room_id/live_url/force_rescan",
      not ({"room_id", "live_url", "force_rescan"} & set(_old.keys())),
      f"keys={sorted(_old.keys())}")
check("B7b 存量记录 id 以**键名**为准（键才是真源）",
      _old["id"] == "992931212705_old", _old.get("id"))
check("B7c 存量记录保留其策略参数（max_target=100）", _old.get("max_target") == 100)
LC._save_all(_before)   # 还原，不留残留

check("B6c 生效账号：页面选择优先于策略 acct",
      LC.resolve_effective_account({"acct": "策略号"}, "页面号") == "页面号"
      and LC.resolve_effective_account({"acct": "策略号"}, "") == "策略号")

# ══════════════════════════════════════════════════════════════════════════
print("C. 真 HTTP（TestClient，含 400 语义）")
try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    reset_kv()
    app = FastAPI()
    app.state.adm = None  # 引擎未初始化 → restart 必须如实回 idle
    app.include_router(LC.router, prefix="/api/live/config-tags")
    app.include_router(LC.router, prefix="/api/live/room-configs")
    c = TestClient(app)

    r = c.post("/api/live/config-tags", json={"name": "A策略", "max_target": 7})
    check("C1 POST 建策略 → 200/ok", r.status_code == 200 and r.json().get("ok"), r.text[:200])
    r = c.post("/api/live/room-configs", json={"name": "B策略", "max_target": 3})
    check("C2 兼容前缀可写同一份存储", r.json().get("ok"), r.text[:200])
    r = c.get("/api/live/config-tags")
    names = sorted(x["name"] for x in r.json()["items"])
    check("C3 两条策略可读（多策略）", names == ["A策略", "B策略"], f"{names}")
    r = c.post("/api/live/config-tags", json={"id": "!!bad!!", "name": "x"})
    check("C4 非法 id → 显式失败", r.json().get("ok") is False, r.text[:200])

    sid = [x["id"] for x in c.get("/api/live/config-tags").json()["items"]
           if x["name"] == "A策略"][0]
    r = c.post(f"/api/live/config-tags/{sid}/restart",
               json={"room_id": "840377749201", "account": "页面号"})
    j = r.json()
    check("C5 restart 引擎未运行 → 外层 ok=True 但 restart.ok=false（不谎报）",
          r.status_code == 200 and j.get("ok") and j.get("restart", {}).get("ok") is False,
          r.text[:260])
    check("C5b not_applied 不再出现 force_rescan",
          "force_rescan" not in (j.get("restart", {}).get("not_applied") or []),
          str(j.get("restart")))
    r = c.post(f"/api/live/config-tags/{sid}/apply",
               json={"room_id": "840377749201", "account": "页面号"})
    cfg = r.json().get("config") or {}
    check("C6 apply 写入直播间链接与生效账号（上下文来自调用方）",
          cfg.get("live_id") == "840377749201" and cfg.get("acct") == "页面号"
          and "force_rescan" not in cfg, f"{cfg}")
    r = c.post("/api/live/config-tags/__nope__/restart")
    check("C7 不存在策略 restart → 显式失败", r.json().get("ok") is False, r.text[:200])
    r = c.delete(f"/api/live/config-tags/{sid}")
    check("C8 删除策略 → ok 且不回传 force_rescan 等身份字段",
          r.json().get("ok") and "force_rescan" not in r.text, r.text[:200])
except Exception as e:
    check("C0 TestClient 层可运行", False, f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════════════
print("D. 后端引擎：热更代码级契约")
check("D1 apply_runtime_config 不再产出 force_rescan（not_applied 少了该项）",
      'not_applied.append("force_rescan")' not in adm)
check("D1b 热更仍保留 live_url / acct 两项「换任务」语义",
      'not_applied.append("live_url")' in adm and 'not_applied.append("acct")' in adm)
check("D2 快照不再输出 force_rescan 字段",
      '"force_rescan": bool(self.force_rescan)' not in adm)
check("D3 TaskConfig 模型不再有 force_rescan / forceRescan",
      not re.search(r"class TaskConfig[\s\S]*?force_rescan", src("models/task.py")),
      "models/task.py 仍含 force_rescan")
check("D4 app_config general 分区不再声明 force_rescan",
      "force_rescan" not in src("services/app_config_schema.py"))
check("D5 api/tasks.py 不再读写 force_rescan",
      not re.search(r"force_rescan|forceRescan", src("api/tasks.py")))

print(f"\nPASS={PASS} FAIL={FAIL}")
if FAILED:
    print("失败项：")
    for x in FAILED:
        print("  -", x)
print("env_root=", os.environ["DY_APP_ROOT"])
sys.exit(1 if FAIL else 0)
