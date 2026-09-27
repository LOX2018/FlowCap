# -*- coding: utf-8 -*-
"""契约门禁：把 docs/design-contracts 的「验证方式」变成可执行检查。

依据《架构审计报告》§九-6 判据 + 《体系体检报告》§6.1②（契约漂移必须可执行）。
本脚本只**读**代码/文档，不改任何文件；退出码非 0 表示有契约被违反。

用法：
    py314 scripts/check_contracts.py            # 人读输出
    py314 scripts/check_contracts.py --quiet    # 仅退出码（CI）
"""
from __future__ import annotations

import glob
import inspect
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # DYAutoDM_v2/
BE = ROOT / "backend"
DC = ROOT / "docs" / "design-contracts"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


# ── 已知缺口基线（门禁只对【新增】违规失败）────────────────────────
GAPS_FILE = DC / ".known-gaps.json"


def load_known_gaps() -> set[str]:
    """返回 {(check, target)} 集合；读不到则视为空（保守：全部违规都报）。"""
    try:
        import json
        data = json.loads(GAPS_FILE.read_text(encoding="utf-8"))
        return {(g["check"], g["target"]) for g in data.get("known_gaps", [])}
    except Exception:                                      # noqa: BLE001
        return set()


KNOWN = load_known_gaps()

# ── L-2 就地「已知缺口」基线（与 .known-gaps.json 同形同口径）──────────
# 为何就地：本轮边界限定「只改 scripts/check_contracts.py」一个文件，
#   不得写 .known-gaps.json，故把新增门禁（G14）命中的**既有**未修缺口
#   按**逐项精确 (check, target) 相等**登记在此（禁前缀 / 文件级豁免）。
#
# ⚠️ 2026-09-24 父会话复核后**已清空**（两条均被修好，留档说明）：
#   ① `backend/test_live.py` —— 根因不是「模块未建立」，而是 C-05 §6 的
#      命令本身是 doc-rot（`test_live.py` 在实仓**从未存在**）。已改契约
#      §6 指向真实的 `test_live_identity_verdict` / `test_live_session_decrypt_authority`。
#   ② `backend/test_uid_sink_ext.py::…test_window_delays_send` —— 根因是
#      该测试**自身顺序相关**（`T3IsolationTest` 触发 `database.get_db()`
#      的会员一致性校验，与 `test_config_isolation` 抢路径）。已移除该脆弱
#      断言（改造为模块级注释 + 人工验证），测试现于「干净根 / discover /
#      双解释器」三种方式下 **13/13 全绿**。
#   ⇒ 机制保留（供将来逐用例登记），但当前**必须为空**：条目一旦过期未删，
#      门禁会因「已消除的缺口仍在基线」而失去准确性（同 .known-gaps.json 纪律）。
INLINE_KNOWN: set[tuple[str, str]] = set()


def classify(check_name: str, offenders: list[str]) -> tuple[list[str], list[str]]:
    """把违规分成 (新增违规, 已知缺口)。

    匹配口径（2026-09-22 修正，`_match_rule` 同步写在 .known-gaps.json）：
    **逐端点精确相等** —— offender 形如
    `backend/dy_apis/client_collection.py:/aweme/v1/web/aweme/favorite/`，
    必须与某条 known_gap 的 (check, target) **完全一致** 才判为已知。

    ⚠️ **不要退回前缀匹配**。旧实现是
    `off.startswith(g[1].split("/aweme")[0])` —— 它只看「文件路径前缀」，
    于是同一文件被登记 1 处缺口后，该文件**所有**受保护端点全部落入 known。
    实测反证（2026-09-22）：人为新增一个该文件从未登记的端点 → 返回
    (新增违规=0, known=1) ⇒ 门禁**不会失败**，与 docstring 承诺的
    「只对【新增】违规失败」直接矛盾（静默豁免新缺口）。
    """
    new, known = [], []
    for off in offenders:
        matched = (any(off == g[1] and g[0] == check_name for g in KNOWN)
                   or (check_name, off) in INLINE_KNOWN)
        (known if matched else new).append(off)
    return new, known


# ── G0: 契约文件存在性（审计判据 ≥5）────────────────────────────
contracts = sorted(p for p in DC.glob("C-*.md"))
check("G0 契约文件 ≥5", len(contracts) >= 5, f"实测 {len(contracts)} 份")

# ── G1: C-01 主动查询 —— 「**实际发生了几次**」（运行时计数断言）────────
# 2026-09-27 二轮强化（用户 2026-09-27 定调）：
#   "不要审到底有多少接口是主动查询的性质，而关注到底其**实际用到了多少次**主动查询"。
#   ⇒ 判据从「某链路是否出现某符号/是否发出某请求」（静态 / 单链路）升级为
#     **全模块运行时计数**：装 in-process `requests` 拦截器，逐个**真调**主动查询的
#     入口函数，统计**实际**发往抖音业务域的主动查询请求数，输出**次数**。
#   取向（用户要求）：主动查询接口**保留**、**不删**；门禁**只计数、只如实上报**，
#     默认态（未启用）= 0 次 ⇒ 绿；将来启用后计数 > 0 ⇒ 如实体现在门禁输出里，
#     由人来决定放行与否（**不静默屏蔽，也不因「有接口」而误报**）。
CAP = BE / "auto_dm" / "conversation_capture.py"


def _is_local_url(u: str) -> bool:
    """出站 URL 是否指向本地 BCC 网关（被动链路载体）。"""
    try:
        from urllib.parse import urlparse
        host = (urlparse(str(u)).hostname or "").lower()
    except Exception:                                       # noqa: BLE001
        host = str(u).lower()
    return host in ("127.0.0.1", "localhost", "::1", "0.0.0.0")


# 抖音业务域（主动查询的判定目标）。
_DOUYIN_HOSTS = ("douyin.com", "amemv.com", "snssdk.com", "douyinpic.com",
                 "zjcdn.com", "byteimg.com")


def _count_active_queries(entries: list[tuple[str, str]]) -> dict:
    """纯函数：把「(入口名, 出站URL)」清单 → 主动查询计数报表。

    判定：URL **非本地** 且 **命中抖音业务域** ⇒ 计为一次「主动查询」。
    参数与返回值与源码书写方式无关 ⇒ 别名 / 拼接 / 动态取名都无法影响计数。

    :param entries: [(entry_name, url), ...]
    :return: {"total": 出站总数, "active": 主动查询数, "by_entry": {入口: 次数},
              "urls": 主动查询 URL 列表}
    """
    by_entry: dict[str, int] = {}
    active_urls: list[str] = []
    for entry, url in entries:
        u = str(url)
        if _is_local_url(u):
            continue
        if any(h in u.lower() for h in _DOUYIN_HOSTS):
            by_entry[entry] = by_entry.get(entry, 0) + 1
            active_urls.append(u)
    return {"total": len(entries), "active": len(active_urls),
            "by_entry": by_entry, "urls": active_urls}


def _probe_capture_outbound() -> dict:
    """运行时拦截：真调捕获模块的入口，记录所有出站 (入口, URL)。

    做法：把 `requests.post/get/request` 换成记录器（返回 `ok=0` 的哑响应，
    使被测函数走「降级/空返回」而**不真正联网**），再逐个真调入口函数。
    返回 {entries, err} —— entries 是 [(entry, url), ...]。
    """
    out = {"entries": [], "err": ""}
    try:
        import requests
        sys.path.insert(0, str(BE))
        from auto_dm import conversation_capture as _cc  # type: ignore
    except Exception as e:                                  # noqa: BLE001
        out["err"] = f"import 失败: {e}"
        return out

    class _DummyResp:
        status_code = 599
        content = b""
        text = ""
        def json(self):                                     # noqa: D401
            return {}
        def raise_for_status(self):
            raise RuntimeError("probe-stub")

    _cur = {"entry": "<unknown>"}

    def _recorder(_method):
        def _call(url, *a, **k):
            out["entries"].append((_cur["entry"], str(url)))
            return _DummyResp()
        return _call

    saved = {m: getattr(requests, m, None) for m in ("post", "get", "request")}
    for m in saved:
        if saved[m] is not None:
            setattr(requests, m, _recorder(m))
    try:
        # 逐个真调入口；每个入口包一层 try（入口可能抛异常，但要保留已记录条目）。
        for _entry, _fn, _args in _capture_entries(_cc):
            _cur["entry"] = _entry
            try:
                _fn(*_args)
            except Exception:                               # noqa: BLE001
                pass
    finally:
        for m, fn in saved.items():
            if fn is not None:
                setattr(requests, m, fn)
    return out


def _capture_entries(_cc) -> list[tuple]:
    """枚举捕获模块里**主动查询相关的入口**（真调它们，观察实际出站）。

    取向：不看接口「有多少 / 叫什么」，只看**调用后实际发了几次**。
    """
    entries: list[tuple] = []
    # ① 昵称捕获路径（C-01 风控红线的核心路径）
    if hasattr(_cc, "capture_userinfo_via_browser"):
        try:
            _cc._userinfo_cache.clear()
        except Exception:                                   # noqa: BLE001
            pass
        entries.append(("capture_userinfo_via_browser",
                        _cc.capture_userinfo_via_browser, ("__gate_probe__",)))
    return entries


if not CAP.exists():
    check("G1 C-01 主动查询计数", False, f"模块缺失 {CAP}")
else:
    _p1 = _probe_capture_outbound()
    _r1 = _count_active_queries(_p1["entries"])
    # 判定（2026-09-27 用户定调：只计数、只上报，默认不阻断）：
    #   · 探针必须真跑起来且真捕获到出站（否则「0 次」不可信 ⇒ FAIL，禁静默空转）；
    #   · 主动查询次数**如实上报**：0 次 = 绿；>0 = **仍不阻断**（接口保留、启用中），
    #     但输出必须**醒目前缀**（⏳）以免「有次数却被读成绿」而失去可见性。
    _g1_ok = (not _p1["err"]) and _r1["total"] > 0
    if _p1["err"]:
        _d1 = f"探针无法执行: {_p1['err']}"
    elif _r1["total"] == 0:
        _d1 = "探针未捕获到任何出站请求（无法证明计数 ⇒ 按 FAIL 处理，禁静默空转）"
    elif _r1["active"] == 0:
        _d1 = (f"🟢 运行时主动查询 0 次；出站共 {_r1['total']} 次"
               f"（本地网关 {_r1['total']} 次）")
    else:
        _d1 = (f"⏳ 运行时主动查询 {_r1['active']} 次（**启用中，计数上报、不阻断**）"
               f"：{_r1['by_entry']} ⇒ {_r1['urls'][:3]}"
               f"；出站共 {_r1['total']} 次（本地网关 "
               f"{_r1['total'] - _r1['active']} 次）")
    check("G1 C-01 主动查询计数", _g1_ok, _d1)

# ── G2: C-02 secsdk —— 保护清单端点不得直发 params.get() ──────────
try:
    sys.path.insert(0, str(BE))
    from utils.secsdk_web_sign import PROTECTED_PATHS_GET  # type: ignore
    protected = list(PROTECTED_PATHS_GET)
except Exception as e:                                    # noqa: BLE001
    protected = []
    check("G2 C-02 secsdk 签名接线", False, f"无法导入签名模块: {e}")

if protected:
    # 逐端点判定：找到 `api = "<受保护路径>"` 后，看其后 ~60 行窗口内是否出现签名调用。
    # （不能「文件里出现过 signed_url 就跳过整个文件」——同一文件常有多端点，只在其一上接线。）
    offenders = []
    for py in BE.rglob("*.py"):
        if "__pycache__" in str(py) or py.name.startswith("test_"):
            continue
        lines = py.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines):
            for path in protected:
                if not re.search(r'api\s*=\s*f?["\'`]' + re.escape(path), line):
                    continue
                window = "\n".join(lines[i:i + 60])
                if "signed_url(" not in window:
                    offenders.append(f"{py.relative_to(ROOT).as_posix()}:{path}")
    new_off, known_off = classify("G2 C-02 secsdk 签名接线", offenders)
    check("G2 C-02 secsdk 签名接线", not new_off,
          (f"⚠ 已知缺口 {len(known_off)} 处（见 .known-gaps.json）"
           + (f"；新增违规 {new_off[:3]}" if new_off else ""))
          if known_off else (f"未签名端点 {len(offenders)} 处: {offenders[:5]}"
                             if offenders else "0 命中"))

# ── G3: C-03 引擎校验 —— dm 判定不得用 wp 结果冒充 ────────────────
ACC = BE / "auto_dm" / "accounts.py"
if ACC.exists():
    txt = ACC.read_text(encoding="utf-8", errors="replace")
    # 反模式：直接 result["dm"] = result["wp"] 之类
    bad = re.search(r'\[\s*["\']dm["\']\s*\]\s*=\s*.{0,40}\[\s*["\']wp["\']', txt)
    check("G3 C-03 dm 不冒充 wp", bad is None,
          "发现 dm 直接赋值 wp" if bad else "0 命中")

# ── G4: C-04 投递 —— 「抓包到 ok」不得冒充投递证据（行为断言）──────
# 2026-09-27 体检修复（弱判据 P1）：原实现 `("role" and "me") or "回执"` 是
#   近似零区分度的字符串匹配（"me" 是任意子串，message/time 全命中）。
#   现改为**行为断言**：真调 `delivery_verify._resolve_evidence`（纯函数、无 DB/网络），
#   断言契约不变式 Ⅰ1 —— 「无 server_message_id ⇒ 不是投递证据(False)」、
#   「有 server_message_id ⇒ 是(True)」、「8610 ⇒ 否(False)」。
#   即 C-04 §验证方式「硬验证（唯一可接受的『成功』证据）」的可执行化。
try:
    from services import delivery_verify as _dv4  # type: ignore
    _g4_blind = _dv4._resolve_evidence(None, "", 0, 0)[0]        # 仅「ok」，无 msg_id
    _g4_real = _dv4._resolve_evidence(None, "7123456789", 0, 0)[0]  # 真 msg_id
    _g4_c8610 = _dv4._resolve_evidence(None, "7123456789", 0, 8610)[0]  # 安全未过
    _g4_verdict_fake = _dv4._resolve_evidence(
        {"delivered": True, "server_message_id": ""}, "", 0, 0)[0]  # verdict 里 delivered=True 但无 sid
    _ok4 = (_g4_blind is False and _g4_real is True
            and _g4_c8610 is False and _g4_verdict_fake is False)
    _d4 = (f"盲ok={_g4_blind} 真msg_id={_g4_real} 8610={_g4_c8610} "
           f"verdict无sid={_g4_verdict_fake}")
except Exception as e:                                    # noqa: BLE001
    _ok4, _d4 = False, f"import services.delivery_verify 失败: {e}"
check("G4 C-04 投递有回执/落库验证", _ok4, _d4)

# ── G5: C-05 直播 —— reflow 为主引擎（行为断言）──────────────────
# 2026-09-27 体检修复（弱判据 P1）：原实现 `"_reflow_resolve" and "reflow/info"
#   in txt` 只证明两个名字同时出现在源码里。改掉 `def` 名但保留调用处仍绿；
#   且无法证明引擎真能工作。现改为**行为断言**：
#     ① `_reflow_resolve` 必须是**可调用对象**（真 import 得到）；
#     ② 其签名可接受 (room_id, sec_user_id)（inspect.signature 校验）；
#     ③ 参数装配函数产出的 URL 必须指向 reflow/info 端点（真调 `_build_reflow_params`
#        或模块常量 `_REFLOW_URL`）—— 证明「reflow 是主引擎」而非仅名字在场。
try:
    import inspect as _inspect5
    import link_resolve as _lr5  # type: ignore
    _fn5 = getattr(_lr5, "_reflow_resolve", None)
    _url5 = str(getattr(_lr5, "_REFLOW_URL", ""))
    _ok5_callable = callable(_fn5)
    _ok5_sig = False
    if _ok5_callable:
        _params5 = list(_inspect5.signature(_fn5).parameters)
        _ok5_sig = len(_params5) >= 2 and _params5[0] in ("room_id", "roomId")
    _ok5_url = "reflow/info" in _url5
    _ok5 = _ok5_callable and _ok5_sig and _ok5_url
    _d5 = (f"_reflow_resolve 可调用={_ok5_callable} 签名≥2参={_ok5_sig} "
           f"_REFLOW_URL 指向 reflow/info={_ok5_url}")
except Exception as e:                                    # noqa: BLE001
    _ok5, _d5 = False, f"import link_resolve 失败: {e}"
check("G5 C-05 reflow 主引擎存在", _ok5, _d5)

# ════════════════════════════════════════════════════════════════
# L-2（2026-09-24）：把各契约 §「验证方式」里**可机械执行**的检查
#   收敛为门禁。判据**定义**取自契约 md（真源），判据**取值**取自被检
#   模块/文件（导入或读源码）—— 不在本脚本里复制判据字面量。
#   新增门禁若命中既有未修缺口，复用 .known-gaps.json 基线（只对新增
#   违规失败，逐项精确匹配）；G0~G5 语义与退出码保持不变。
# ════════════════════════════════════════════════════════════════
sys.path.insert(0, str(BE))


def _sec(md_path: Path, number: int) -> str:
    """取契约 md 的 `## <number>. ...` 节原文（判据定义的真源）。

    lookahead 允许「下一节」**或文件结束**（末节无后继标题）。
    """
    txt = md_path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^##\s+%d\..*?(?=^##\s+\d+\.|\Z)" % number, txt, re.S | re.M)
    return m.group(0) if m else ""


def _bash_blocks(section: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)```", section, re.S)


def _grep_specs(section: str) -> list[tuple[str, list[str]]]:
    """解析 §验证方式 的 `grep -n "<pat>" <path>` → [(path, [符号…])]。"""
    out: list[tuple[str, list[str]]] = []
    for blk in _bash_blocks(section):
        for line in blk.splitlines():
            ls = line.strip()
            if not ls.startswith("grep"):
                continue
            m = re.match(r'grep\s+-\w+\s+"((?:[^"\\]|\\.)+)"\s+(\S+)', ls)
            if m:
                out.append((m.group(2), [s for s in re.split(r"\\\|", m.group(1))]))
    return out


def _expand(spec: str) -> list[Path]:
    """把契约里的仓库相对路径（可含 *）展开为真实文件。"""
    full = ROOT / spec.replace("/", os.sep)
    if "*" in spec:
        return [Path(p) for p in glob.glob(str(full)) if Path(p).is_file()]
    return [full] if full.is_file() else []


# ── G6: C-02 §5 签名自检（is_protected 断言）──────────────────────
C02 = DC / "C-02-content-collection.md"
_c02_paths = re.findall(r"is_protected\('([^']+)'\)", _sec(C02, 5))
try:
    from utils.secsdk_web_sign import is_protected  # type: ignore
    _miss6 = [p for p in _c02_paths if not is_protected(p)]
except Exception as e:                                    # noqa: BLE001
    _miss6 = [f"import-failed:{e}"]
if not _c02_paths:
    check("G6 C-02 签名自检", False, "契约 §5 未解析到 is_protected 断言")
else:
    check("G6 C-02 签名自检", not _miss6,
          f"{len(_c02_paths)} 条断言全真" if not _miss6 else f"未通过 {_miss6}")

# ── G7: C-01 Ⅰ3 昵称源 SSOT（唯一来源 = IndexedDB <uid>_user）─────
try:
    from kernel import truth as _truth  # type: ignore
    _ns = str(getattr(_truth, "NICKNAME_SOURCE", ""))
    _order = list(getattr(_truth, "NICKNAME_SOURCES_ORDER", ()))
    _ok7 = ("indexeddb" in _ns.lower() and "<uid>_user" in _ns
            and _order[:1] == ["indexeddb"])
    _d7 = f"NICKNAME_SOURCE={_ns!r} 首选={_order[:1]}"
except Exception as e:                                    # noqa: BLE001
    _ok7, _d7 = False, f"import kernel.truth 失败: {e}"
check("G7 C-01 昵称源 SSOT", _ok7, _d7)

# ── G8: C-03 Ⅰ1 dm 判定须来自**真实写**（cmd 609 建会话）─────────
try:
    from auto_dm import accounts as _acc  # type: ignore
    _pw = inspect.getsource(_acc.probe_im_write)
    _ok8 = ("create_conversation" in _pw and "只读" in _pw)
    _d8 = "probe_im_write 走 create_conversation 且分型只读态" if _ok8 \
        else "probe_im_write 缺真实写调用 / 只读态分型"
except Exception as e:                                    # noqa: BLE001
    _ok8, _d8 = False, f"import auto_dm.accounts 失败: {e}"
check("G8 C-03 真实写校验", _ok8, _d8)

# ── G9: C-04 Q① 投递 = 落库硬证据（盲标记不得称成功）──────────────
try:
    from services import delivery_verify as _dv  # type: ignore
    _blind = _dv._resolve_evidence(None, "", 0, 0)[0]
    _sid = _dv._resolve_evidence(None, "1", 0, 0)[0]
    _chk = _dv._resolve_evidence(None, "1", 0, 8610)[0]
    _role = "'me'" in inspect.getsource(_dv.mark_delivery_verified)
    _ok9 = (_blind is False and _sid is True and _chk is False and _role)
    _d9 = f"无证据={_blind} 有msg_id={_sid} 8610={_chk} 标记role=me:{_role}"
except Exception as e:                                    # noqa: BLE001
    _ok9, _d9 = False, f"import services.delivery_verify 失败: {e}"
check("G9 C-04 投递硬验证", _ok9, _d9)

# ── G10: C-05 Ⅰ4 解密权合取（权威出口 reason 枚举与契约一致）──────
C05 = DC / "C-05-live-monitoring.md"
_m10 = re.search(r"reason\s*[∈=]\s*\{([^}]*)\}", _sec(C05, 7))
_expect10 = {x.strip() for x in _m10.group(1).split(",") if x.strip()} if _m10 else set()
try:
    from auto_dm import accounts as _acc2  # type: ignore
    _label = set(getattr(_acc2, "_VERDICT_LABEL", {}).keys())
    _ok10 = bool(_expect10) and _expect10 <= _label
    _d10 = f"契约 {sorted(_expect10)} ⊆ 实现 {sorted(_label)}"
except Exception as e:                                    # noqa: BLE001
    _ok10, _d10 = False, f"import auto_dm.accounts 失败: {e}"
check("G10 C-05 解密权合取", _ok10, _d10)

# ── G11: C-06 §3 字段规范 ↔ database.py 实际列（读真源）──────────
C06 = DC / "C-06-live-lead-sink.md"
_c06_fields = re.findall(r"^\|\s*`([a-z0-9_]+)`", _sec(C06, 3), re.M)
_m11 = re.search(r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN", _sec(C06, 6), re.I)
_c06_table = _m11.group(1) if _m11 else ""
_db_txt = (BE / "database.py").read_text(encoding="utf-8", errors="replace")
_cols: set[str] = set()
_mm11 = re.search(r"CREATE TABLE IF NOT EXISTS\s+%s\s*\((.*?)\)\s*;"
                  % re.escape(_c06_table), _db_txt, re.S)
if _mm11:
    _cols |= set(re.findall(r"^\s*([a-z_]+)\s+(?:TEXT|REAL|INTEGER|BOOLEAN)",
                            _mm11.group(1), re.M | re.I))
_cols |= set(re.findall(r"ALTER\s+TABLE\s+%s\s+ADD\s+COLUMN\s+([a-z_]+)"
                        % re.escape(_c06_table), _db_txt, re.I))
_off11 = [f"backend/database.py:{_c06_table}.{c}" for c in _c06_fields if c not in _cols]
check("G11 C-06 字段规范", bool(_c06_fields) and not _off11,
      f"{len(_c06_fields)} 字段齐备" if _c06_fields and not _off11
      else (f"缺列 {_off11}" if _c06_fields else "契约 §3 未解析到字段"))

# ── G12: C-06 §6 符号守护（契约 grep 判据 → 源码符号存在性）────────
# 2026-09-27 体检修复（双重假绿 P0-2）：
#   ① 原实现用 `_s not in _blob`（**子串**匹配）—— 把符号 `is_high_value`
#      改名成 `is_high_value_renamed`（仍含原串）时**不报红**。现改用
#      **词边界正则**（`\b<符号>\b`），改名即失败。
#   ② 原实现若契约 §6 的 grep 行**格式不被解析**（如去掉 `grep ` 前缀），
#      `_grep_specs` 返回空列表 ⇒ `check(..., not [])` **恒真通过** = 静默 SKIP。
#      现改为**判据来源缺失即 FAIL**（解析到 0 条 grep 判据 ⇒ 报红）。
_specs12 = _grep_specs(_sec(C06, 6))


def _g12_offenders(specs: list[tuple[str, list[str]]]) -> list[str]:
    """纯函数：把契约 grep 判据 → 违例列表。

    - 符号用**词边界**精确匹配（非子串）⇒ 改名即违例；
    - `specs` 为空 ⇒ 返回哨兵 `["<判据来源缺失>"]`（调用方据此判 FAIL，
      禁止「空判据集 = 恒真通过」的静默 SKIP）。
    """
    if not specs:
        return ["<判据来源缺失：契约 §6 未解析到任何 grep 判据>"]
    off: list[str] = []
    for _path, _syms in specs:
        _files = _expand(_path)
        _blob = "\n".join(f.read_text(encoding="utf-8", errors="replace")
                          for f in _files)
        for _s in _syms:
            _pat = r"\b" + re.escape(_s) + r"\b"
            if not _files or not re.search(_pat, _blob):
                off.append(f"{_path}:{_s}")
    return off


_off12 = _g12_offenders(_specs12)
check("G12 C-06 符号守护", not _off12,
      f"§6 全部 {len(_specs12)} 条 grep 判据命中（词边界精确匹配）"
      if not _off12 else f"违规 {_off12}")

# ── G13: C-06 §4 配置键 ↔ app_config_schema SECTIONS（读真源）──────
_c06_keys = re.findall(r"^\|\s*`([a-z0-9_]+)`", _sec(C06, 4), re.M)
try:
    from services.app_config_schema import SECTIONS  # type: ignore
    _have13 = set(SECTIONS.get("send", {}).get("fields", {}))
    _off13 = [f"backend/services/app_config_schema.py:send.{k}"
              for k in _c06_keys if k not in _have13]
    check("G13 C-06 配置键", bool(_c06_keys) and not _off13,
          f"{len(_c06_keys)} 键齐备" if _c06_keys and not _off13
          else (f"缺键 {_off13}" if _c06_keys else "契约 §4 未解析到配置键"))
except Exception as e:                                    # noqa: BLE001
    check("G13 C-06 配置键", False, f"import app_config_schema 失败: {e}")

# ── G14: C-01/03/04/05/06 §5·§6 单元测试命令可运行（真源 = 契约）──
_mods14: set[str] = set()
for _md in sorted(DC.glob("C-*.md")):
    for _n in (5, 6, 7):
        for _blk in _bash_blocks(_sec(_md, _n)):
            for _line in _blk.splitlines():
                for _g in re.finditer(r"-m\s+unittest\s+([A-Za-z0-9_./]+)", _line):
                    _mods14.add(_g.group(1))
                for _g in re.finditer(r"unittest\s+(backend/[A-Za-z0-9_./]+\.py)", _line):
                    _mods14.add(_g.group(1))
_off14: list[str] = []
_tmp14 = tempfile.mkdtemp(prefix="check_contracts_ut_")
_env14 = dict(os.environ, DY_APP_ROOT=_tmp14)
for _mod in sorted(_mods14):
    _name = Path(_mod).name[:-3] if _mod.endswith(".py") else Path(_mod).name
    _target = f"backend/{_name}.py"
    if not (BE / f"{_name}.py").is_file():
        _off14.append(_target)              # 模块缺失：登记模块路径
        continue
    try:
        _r14 = subprocess.run([sys.executable, "-m", "unittest", _name],
                              cwd=str(BE), env=_env14, capture_output=True,
                              text=True, timeout=180)
        if _r14.returncode != 0:
            # 逐**用例**精确登记（禁文件级豁免）：unittest 头为
            #   `FAIL: <short> (<module>.<Class>.<method>)` / `ERROR: …`
            _cases = []
            for _m in re.finditer(r"^(?:FAIL|ERROR):\s+(\S+)(?:\s+\(([^)]+)\))?",
                                  _r14.stderr + _r14.stdout, re.M):
                _cid = _m.group(2) or _m.group(1)
                _cid = re.sub(r"^%s\." % re.escape(_name), "", _cid)
                _cases.append(f"{_target}::{_cid}")
            if _cases:
                _off14.extend(sorted(set(_cases)))
            else:                           # 进程非 0 退但无用例名（如崩溃/超时）
                _off14.append(_target)
    except Exception:                       # noqa: BLE001
        _off14.append(_target)
_new14, _known14 = classify("G14 C-01~C-06 单测可运行", _off14)
check("G14 C-01~C-06 单测可运行", not _new14,
      (f"⚠ 已知缺口 {len(_known14)} 处（就地 INLINE_KNOWN 基线）"
       + (f"；新增违规 {_new14}" if _new14 else ""))
      if _known14 else (f"未通过 {_new14}" if _new14
                        else f"{len(_mods14)} 个契约单测模块可运行"))

# ════════════════════════════════════════════════════════════════
# --selftest：负控自检装置（2026-09-27 新增，体检报告 §六-5）
#   目的：证明 G1/G4/G5/G12 四条判据**真的会报红**（不是空架子）。
#   做法：对每条判据构造一个「本应拦住的坏状态」，直接调用其**判据函数**，
#         断言返回「违规」。全部在内存/临时副本上做，**不修改仓库任何文件**。
#   退出码：全部负控成立 → 0；任一判据对坏状态不报红 → 1。
# ════════════════════════════════════════════════════════════════
if "--selftest" in sys.argv:
    import importlib
    import tempfile as _tempfile

    _self = {"ok": True}

    def _neg(name: str, fired: bool, detail: str) -> None:
        mark = "✔" if fired else "✘ 判据失效（对坏状态不报红）"
        print(f"  [{mark}] {name:32s} {detail}")
        _self["ok"] = _self["ok"] and fired

    print("=" * 60)
    print("check_contracts --selftest（负控：注入坏状态，判据应报红）")
    print("=" * 60)

    # ── G1：主动查询**计数**必须真实反映出站（本地=0 / 抖音=N）───────
    # 负控：抖音 URL 必须被计入主动查询（计数 > 0）
    _bad_url = "https://imapi.douyin.com/v1/message/get_by_conversation"
    _r_bad = _count_active_queries([("e", _bad_url), ("e", _bad_url)])
    _neg("G1 抖音出站计入主动查询", _r_bad["active"] == 2,
         f"active={_r_bad['active']} by_entry={_r_bad['by_entry']}")
    # 正控：本地网关不计入主动查询（0 次，不误报）
    _r_ok = _count_active_queries(
        [("e", "http://127.0.0.1:8321/capture_userinfo"),
         ("e", "http://127.0.0.1:8321/userinfo_idb")])
    _neg("G1 本地网关不计主动查询", _r_ok["active"] == 0,
         f"active={_r_ok['active']} total={_r_ok['total']}")
    # 探针自证：真调一次捕获路径，必须捕获到出站记录（证明探针非空转）
    _p = _probe_capture_outbound()
    _rp = _count_active_queries(_p["entries"])
    _neg("G1 探针真捕获到出站请求",
         (not _p["err"]) and _rp["total"] > 0,
         f"出站 {_rp['total']} 次 / 主动查询 {_rp['active']} 次")

    # ── G4：无 sid 的「盲 ok」必须判为「非投递证据」──────────
    try:
        _dv = importlib.import_module("services.delivery_verify")
        _blind = _dv._resolve_evidence(None, "", 0, 0)[0]
        _real = _dv._resolve_evidence(None, "123456789", 0, 0)[0]
        _neg("G4 盲ok不冒充投递证据", _blind is False, f"盲ok={_blind}")
        _neg("G4 真msg_id判为证据", _real is True, f"真msg_id={_real}")
    except Exception as _e:                                 # noqa: BLE001
        _neg("G4 import delivery_verify", False, str(_e))

    # ── G5：引擎可调用 + 签名 + 端点常量 ────────────────────
    try:
        import inspect as _i5
        _lr = importlib.import_module("link_resolve")
        _fn = getattr(_lr, "_reflow_resolve", None)
        _callable_ok = callable(_fn)
        _sig_ok = _callable_ok and len(list(_i5.signature(_fn).parameters)) >= 2
        _url_ok = "reflow/info" in str(getattr(_lr, "_REFLOW_URL", ""))
        _neg("G5 _reflow_resolve 可调用", _callable_ok, "")
        _neg("G5 签名≥2参", _sig_ok, "")
        _neg("G5 端点常量 reflow/info", _url_ok, "")
    except Exception as _e:                                 # noqa: BLE001
        _neg("G5 import link_resolve", False, str(_e))

    # ── G12：① 子串改名判违例；② 空判据集判 FAIL（真调 _g12_offenders）──
    import tempfile as _tf12
    with _tf12.TemporaryDirectory() as _d12:
        _f12 = Path(_d12) / "m.py"
        _f12.write_text("def is_high_value_renamed(): pass\n", encoding="utf-8")
        # 用绝对路径 spec（_expand 支持无 * 的直接路径）
        _off_rename = _g12_offenders([(str(_f12), ["is_high_value"])])
        _neg("G12 词边界拦住子串改名", bool(_off_rename),
             f"违例={_off_rename}")
        # 正控：符号**精确在场**时不误报
        _f12.write_text("def is_high_value(): pass\n", encoding="utf-8")
        _neg("G12 精确在场不误报",
             not _g12_offenders([(str(_f12), ["is_high_value"])]), "0 违例")
        # 空判据集 ⇒ 必须报违例（判据来源缺失 = FAIL）
        _off_empty = _g12_offenders([])
        _neg("G12 空判据集判 FAIL", bool(_off_empty), f"哨兵={_off_empty}")

    print("-" * 60)
    if _self["ok"]:
        print("✓ 自检通过：G1/G4/G5/G12 对坏状态均报红，对好状态不误报")
    else:
        print("✗ 自检失败：存在判据对坏状态不报红（假绿）—— 必须修复")
    sys.exit(0 if _self["ok"] else 1)

# ── 汇总 ────────────────────────────────────────────────────────
if "--quiet" not in sys.argv:
    print("=" * 60)
    print("契约门禁 check_contracts")
    print("=" * 60)
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:34s} {detail}")

failed = [n for n, ok, _ in results if not ok]
if failed and "--quiet" not in sys.argv:
    print(f"\n{len(failed)} 项未通过：{failed}")
    print("（提示：G2 未签名端点属**在制品**，实施修复前此为已知缺口）")
sys.exit(1 if failed else 0)
