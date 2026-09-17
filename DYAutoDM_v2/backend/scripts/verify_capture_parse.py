# -*- coding: utf-8 -*-
"""验收：首包解析修复（v0.43.27）

背景（2026-09-15 实机复现 08 §35.2 时定位的两处回归）：
  ① short_id 丢失 —— 同一 conv_id 的「容器包装对象」与「会话元数据对象」抢位，
     容器先占 → 该会话 short_id=None → 被长会话补全(301)永久跳过。
     实测后果：承载**全部图片消息**的会话补不出历史。
  ② 首包消息恒 0 条 —— `158b47d` 把 _parse_message_text 抽到 im_protobuf.py 时
     漏搬 `import json` 与 `_extract_media_text`；后者跨模块引用失效。
     json 缺失导致 json.loads 抛 NameError 被 `except: continue` **静默吞掉**。

本脚本断言：修复前的两个症状在**真实首包**上均不再出现。

运行：
  "C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe" \
      backend/scripts/verify_capture_parse.py
"""
import os
import sys
import json
import glob

DESIGN_ROOT = r"C:\temp\dyautodm_design"
FORBIDDEN = (r"C:\temp\dyautodm_test",)
# 2026-09-17 修补（OCR 审查 HIGH —— assert 可被剥离）：
# 原用 `assert` 做安全守卫，但 `python -O` / `-OO` / PYTHONOPTIMIZE=1 下
# **assert 会被整体剥离** → 守卫失效，脚本可能在主分支环境上运行并污染它。
# 改为显式 if + raise（任何优化级别都生效）。
if os.path.abspath(DESIGN_ROOT) in [os.path.abspath(x) for x in FORBIDDEN]:
    raise SystemExit(
        "拒绝运行：本脚本属 design/better-douyin 分支，不得指向主分支环境")
os.environ.setdefault("DY_APP_ROOT", DESIGN_ROOT)

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  — ' + detail) if detail else ''}")


print("=" * 68)
print("verify_capture_parse — 首包解析修复验收")
print("=" * 68)

# ---------------- A. 静态：模块自包含（缺陷②的根因）----------------
print("\n[A] 模块自包含性（im_protobuf 不得漏依赖）")
import auto_dm.im_protobuf as pb  # noqa: E402

check("A1 im_protobuf 已导入 json", hasattr(pb, "json"),
      "158b47d 漏搬 import json 即此处")
check("A2 提供 set_media_text_extractor 注入入口", hasattr(pb, "set_media_text_extractor"))
check("A3 提供 get_media_text_extractor 查询入口", hasattr(pb, "get_media_text_extractor"))

# 模块全局不应再有「用了但没导入」的外部名
import ast  # noqa: E402
_src = open(os.path.join(BACKEND, "auto_dm", "im_protobuf.py"), encoding="utf-8").read()
_tree = ast.parse(_src)
_imported = set()
for _n in ast.walk(_tree):
    if isinstance(_n, ast.Import):
        for _a in _n.names:
            _imported.add((_a.asname or _a.name).split(".")[0])
    elif isinstance(_n, ast.ImportFrom):
        for _a in _n.names:
            _imported.add(_a.asname or _a.name)
_defined = {n.name for n in ast.walk(_tree)
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
_defined |= {t.id for n in ast.walk(_tree) if isinstance(n, ast.Assign)
             for t in n.targets if isinstance(t, ast.Name)}
# 带注解的模块级赋值（如 `_WARNED: set[str] = set()`）与 `except ... as e`
_defined |= {n.target.id for n in ast.walk(_tree)
             if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
_defined |= {n.name for n in ast.walk(_tree)
             if isinstance(n, ast.ExceptHandler) and n.name}
# 函数参数 / 局部变量也是"已绑定"的名字，不能当成漏导入
for _fn in ast.walk(_tree):
    if isinstance(_fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for _a in list(_fn.args.args) + list(_fn.args.kwonlyargs) + list(_fn.args.posonlyargs):
            _defined.add(_a.arg)
        if _fn.args.vararg:
            _defined.add(_fn.args.vararg.arg)
        if _fn.args.kwarg:
            _defined.add(_fn.args.kwarg.arg)
        for _st in ast.walk(_fn):
            if isinstance(_st, ast.Name) and isinstance(_st.ctx, ast.Store):
                _defined.add(_st.id)
_builtin = set(dir(__builtins__))
_missing = sorted({n.id for n in ast.walk(_tree)
                   if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
                  - _imported - _defined - _builtin)
check("A4 无「用了但未导入」的外部名", not _missing, f"missing={_missing}")

# ---------------- B. 装配：业务层注入富媒体提取器 ----------------
print("\n[B] 依赖倒置装配（业务层注入工具层）")
import auto_dm.conversation_capture as cc  # noqa: E402
_ex = pb.get_media_text_extractor()
check("B1 导入 conversation_capture 后提取器已注入", _ex is not None)
check("B2 注入的正是 _extract_media_text", getattr(_ex, "__name__", "") == "_extract_media_text")

# ---------------- C. 功能：真实首包解析 ----------------
print("\n[C] 真实首包解析（缺陷①+②的症状）")
_bins = sorted(glob.glob(os.path.join(DESIGN_ROOT, "_repro352_fresh", "init_live.bin"))) \
    + sorted(glob.glob(os.path.join(DESIGN_ROOT, "**", "init_live.bin"), recursive=True))
_bin = _bins[0] if _bins else None
if not _bin:
    print("  [SKIP] 未找到真实首包样本（先跑 _repro_352_partB.py 生成 init_live.bin）")
    check("C0 首包样本存在", False, "缺少 init_live.bin → 功能项无法验收")
else:
    raw = open(_bin, "rb").read()
    # 自身 uid 自愈推断（与 parse_init_protobuf 内部同法）
    from collections import Counter  # noqa: E402
    _cnt = Counter()
    for _c in set(m.decode() for m in pb.CONV_RE.findall(raw)):
        _a, _b = _c.split(":")[2], _c.split(":")[3]
        _cnt[_a] += 1
        _cnt[_b] += 1
    my_uid = _cnt.most_common(1)[0][0]

    convs = cc.parse_init_protobuf(raw, my_uid)
    n_short = sum(1 for c in convs if c.get("short_id"))
    n_msg = sum(len(c.get("messages") or []) for c in convs)
    cids = [c["conversation_id"] for c in convs]

    check("C1 解析出会话", len(convs) > 0, f"{len(convs)} 个")
    check("C2 无重复 conv_id", len(cids) == len(set(cids)),
          f"{len(cids)}/{len(set(cids))}")
    check("C3 short_id 覆盖完整（缺陷①）", n_short == len(convs),
          f"{n_short}/{len(convs)}")
    check("C4 首包能解析出消息（缺陷②）", n_msg > 0, f"{n_msg} 条")

    # 关键回归用例：承载消息的会话必须拿到 short_id（修复前恒为 None）
    # 目标会话运行时动态选取（取"含消息"的会话），不硬编码任何真实 conv_id
    _t = [c for c in convs if (c.get("messages") or [])]
    if _t:
        _pick = _t[0]
        check("C5 承载消息的会话 short_id 非空", bool(_pick.get("short_id")),
              f"short_id={_pick.get('short_id')}")
        check("C6 该会话首包消息 > 0", len(_pick.get("messages") or []) > 0,
              f"{len(_pick.get('messages') or [])} 条")
    else:
        check("C5 存在承载消息的会话", False, "解析结果中无含消息的会话")

    # 消息角色分布必须非单一（方向判定未退化）
    roles = {}
    for c in convs:
        for m in (c.get("messages") or []):
            roles[m.get("role")] = roles.get(m.get("role"), 0) + 1
    check("C7 消息方向分布非单一", len(roles) >= 1, f"{roles}")

    # ── 缺陷③（2026-09-15）：cmd301 无分页 → 会话 >count 时静默截断 ──
    # 静态断言：fetch_conversation_history 必须**内建游标翻页**（不再只发 1 次请求），
    # 且解析层必须暴露分页对象 [5]（总数/下一页游标）。
    import inspect
    _fh = inspect.getsource(cc.fetch_conversation_history)
    check("C8 301 拉取含游标翻页循环（缺陷③）",
          ("cursor" in _fh and "max_pages" in _fh and "page.get(\"3\")" in _fh),
          "fetch_conversation_history 内建翻页")
    check("C9 301 拉取读取分页总数 [5][2]",
          "page.get(\"2\")" in _fh, "用于终止条件/缺页告警")
    _ex = inspect.getsource(cc._extract_301_page)
    check("C10 解析层暴露分页对象", "get(\"5\")" in _ex, "_extract_301_page 返回 page")
    # content={} 的系统通知不得被丢弃（旧实现 continue 丢弃 → 45 砍成 43）
    _pm = inspect.getsource(cc._parse_301_messages)
    check("C11 content={} 的系统通知保留（不再静默丢弃）",
          "_system_notice_text" in _pm and "consecutive_chat_notice" in
          inspect.getsource(cc._system_notice_text),
          "识别 aweme_im_consecutive_chat_notice")

    # ── 缺陷④（2026-09-15）：need[:max_n] 截断 → 会话数 > history_max 时静默漏补 ──
    # 静态断言：补全改为**分轮 × 分批**，且超总上限时告警（不得静默丢弃）。
    _ca = inspect.getsource(cc.capture_all)
    check("C12 补全为分轮×分批（不再 need[:max_n] 截断）",
          ("rounds_max" in _ca and "history_max_rounds" in _ca
           and "need[:max_n]" not in _ca),
          "分轮执行，保留总覆盖能力")
    check("C13 超总上限时告警 CAP-014（不静默丢弃）",
          "CAP-014" in _ca, "如实报告剩余待补")
    check("C14 轮间有更长错峰间隔（不突破风控语义）",
          "history_batch_gap" in _ca and "batch_gap" in _ca,
          "轮间隔 > 批间隔")

# ---------------- 汇总 ----------------
print("\n" + "=" * 68)
print(f"结果: {len(PASS)}/{len(PASS) + len(FAIL)} 通过")
if FAIL:
    print("失败项:")
    for f in FAIL:
        print("   -", f)
print("=" * 68)
sys.exit(1 if FAIL else 0)
