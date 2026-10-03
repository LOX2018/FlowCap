#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""铁律机械门禁 —— 把「写在文档里靠自觉」的条款变成**会真拦**的检查。

## 为什么要这个脚本

2026-09-25 事故：环境隔离铁律**早就写明**（`工作记忆/01_铁律与红线.md` §四、
`dyautodm-dev-guards` §三·0），我仍然违反，并据此产出 3 个错误结论
（"内核崩溃/换浏览器"、"-foreground 是真凶"、"图形栈问题"），浪费用户大量时间。

知识库 `cases/2026-09-17_环境隔离失守与验证方法失效.md` 开篇已预言：
> 规则早已存在却仍被违反，问题不在「不知道」，而在「长会话中缺乏当场自查」。

声明式规则 = 空架子。本脚本是它的 instrument 层。

## 设计约束（遵循 D-07：验收判据必须自证「失败态会变红」）

每条检查都必须能**被一个刻意构造的违规样本触发**，否则等于没有。
本脚本自带 `--selftest` 模式：注入违规样本，断言门禁确实报红。

## 覆盖范围（逐条对应铁律条文）

| ID | 铁律出处 | 判据 |
|---|---|---|
| R1 | §四 唯一数据根 | 源码树内不得出现数据根内容（profile/members/.env.enc） |
| R2 | §四 数据根纪律（只放数据，不放构建产物） | 数据根内不得出现 .py/.ts/.tsx 源码文件 |
| R3 | 环境隔离 | 源码 backend 目录不得残留 *.exe / build / dist |
| R4 | §四 版本号五处同步（实测六处） | 六处版本源齐平 |
| R5 | §1.1 明文凭证 | 源码不得出现明文凭证字段（token/skey 等赋值） |
| R6 | §2 停止走 /quit，不得 kill 浏览器 | 源码不得出现 kill 浏览器进程的调用 |
| R7 | §1.2 昵称源 SSOT | 昵称链路不得调用已推翻的批量查询符号 |
| **R8** | **数据契约（ADR-012）** | **委托 `audit_data_contract.py` 六项**（见下） |
| **R9** | **H-22 §6·1 建议 A / D-02 审计红线** | **委托 `audit_redline_count.py` 的 count()**（见下） |

### R9 为何存在（2026-09-26 全库审计 §6·1 建议 A）

> 「裁判脚本本身只是被摆在那里，因为没有自动 check 而失去了意义」

`audit_redline_count.py` 判据正确（THRESHOLD=20、口径合规），但从未挂进任何
自动化 —— 计数一路涨到 **30/20** 才在一次偶然的人肉执行中被发现。
**没有「谁来跑 / 挂在哪」的门禁 = 没有门禁。**

R9 是**唯一 execution point**：判据仍住在 `audit_redline_count.py`（SSOT），
本规则只做委托 + 降级 + 可见化，不重写阈判据、不加参数。
挂载点：`.git/hooks/pre-commit → check_iron_rules.py → R9`。

### R9 为何是 WARN_ONLY（阻断 vs 警告的取舍）

红线突破**不是提交内容的违规**，而是「流程节点到了」的信号 —— 本次提交本身
可能是完全干净的。若因此阻断，开发者唯二的出路是 `--no-verify` 或临时改阈值，
**两者都直接摧毁门禁**（铁律：会被绕过的门禁比没有门禁更坏）。故：
  - 记 1 条 FAIL 进 RESULTS ⇒ 每次 commit 都能看见，不可能再「静默突破」；
  - 归入 WARN_ONLY ⇒ 不阻断提交，改由流程（启动全库审计 + 功能冻结）处置。

同时 R9 **永不静默通过**：未触发时也会打印 count/threshold/remaining，
向开发者持续暴露「距红线还有几个」。

### R8 为何存在（用户 2026-09-26 指出）

> 「这个项目我至少经历过 4 次全盘审计，都没有发现数据库治理的问题」

复盘 3 份历史审计报告，维度全是**代码结构**（入口分裂 / 分层依赖 / 大组件 /
**逐条 diff**）。最后一类最致命：它是**增量审**，`schema` 从没改过 ⇒ 天然不在视野。
**「没变化的危险」永远不会被增量审计发现。** 故 R8 必须是**存量扫描**维度。

R8 六条子判据（详见 `scripts/audit_data_contract.py`）：
R8-1 写入出口收敛 / R8-2 类型注册表完整 / R8-3 text 不得承载 base64·URL /
R8-4 语义标签取自 SSOT / R8-5 未知类型降级 / **R8-6 同一语义不得多名字**。

退出码：0 = 全通过；1 = 有未通过项。
"""
from __future__ import annotations

import os
import re
import sys

# ── 路径真源（本分支）──────────────────────────────────────────────────────
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_ROOT = REPO_ROOT                      # DYAutoDM_v2
BACKEND = os.path.join(SRC_ROOT, "backend")
# 数据根：优先环境变量，回退本分支约定值（不依赖调用方 export —— 09-17 教训）
DATA_ROOT = os.environ.get("DY_APP_ROOT") or r"C:\temp\dyautodm_design"

# 禁止的数据根（主分支环境，串用即违规）
FORBIDDEN_ROOTS = (r"C:\temp\dyautodm_test",)

RESULTS: list[tuple[bool, str, str]] = []


def check(ok: bool, rule_id: str, detail: str) -> bool:
    RESULTS.append((bool(ok), rule_id, detail))
    print("  [%s] %-4s %s" % ("PASS" if ok else "FAIL", rule_id, detail))
    return bool(ok)


def walk(root: str, exts: tuple[str, ...], skip_dirs: set[str] | None = None):
    skip_dirs = skip_dirs or {"node_modules", ".git", "target", "__pycache__",
                              "dist", "build", ".venv", "venv"}
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            if fn.endswith(exts):
                out.append(os.path.join(dirpath, fn))
    return out


# ── R1: 源码树内不得混入数据根内容 ─────────────────────────────────────────
def r1_source_has_no_data() -> None:
    # 2026-09-29（L-16）：补入 browser profile 目录名 —— 它们含**真实登录凭证**
    # （sessionid/sid_guard/sid_tt/uid_tt），必须只存在于数据根（app_root），
    # 绝不落源码树（既污染源码树，又因 tauri bundle.resources 引用而可能随包外发）。
    markers = ("members", ".env.enc", "profiles",
               "vb_profile_default", "vb_profile_dm", "pw_profile_dm")
    hits = []
    for m in markers:
        p = os.path.join(SRC_ROOT, m)
        if os.path.exists(p):
            hits.append(m)
    check(not hits, "R1", f"源码树无数据根内容（命中：{hits or '无'}）")


# ── R2: 数据根只放数据，不放源码 ───────────────────────────────────────────
def r2_data_root_no_source() -> None:
    """数据根只放数据 —— 但**部署产物目录**除外。

    2026-09-25 修正：首版把 `_internal/`（PyInstaller 解压目录，sidecar
    运行必需）判为违规，命中 231 个 .py。若照此清理会**删掉部署运行时**，
    应用直接不可用。故排除部署产物目录，只查数据根**顶层**的散落脚本。
    """
    if not os.path.isdir(DATA_ROOT):
        check(True, "R2", f"数据根不存在，跳过（{DATA_ROOT}）")
        return
    # 部署产物目录（PyInstaller 解压运行时）—— 非源码，排除。
    # 2026-09-30 修正：contents 目录名已由 `_internal` 改为 **`appinternals`**
    #   （见 scripts/build_sidecar.py / check_packaging_contract.py / deploy.py，
    #   改名原因：WiX 会剥掉下划线开头的目录名）——R2 排除名单未跟上改名，
    #   把部署运行时的 356 个 .py 误判为「数据根散落源码」（假红）。
    DEPLOY_DIRS = {"_internal", "appinternals", "binaries", "resources"}
    src = []
    for f in walk(DATA_ROOT, (".py", ".ts", ".tsx")):
        rel = os.path.relpath(f, DATA_ROOT)
        top = rel.split(os.sep)[0]
        if top in DEPLOY_DIRS:
            continue
        src.append(f)
    check(not src, "R2", f"数据根顶层无散落源码（命中 {len(src)} 个"
                         f"{': ' + src[0] if src else ''}）")


# ── R3: 源码树不得残留构建产物 ─────────────────────────────────────────────
def r3_no_build_artifacts_in_src() -> None:
    hits = []
    hits += walk(BACKEND, (".exe",), skip_dirs={"__pycache__"})
    for d in ("build", "dist"):
        p = os.path.join(BACKEND, d)
        if os.path.isdir(p):
            hits.append(p)
    # %SystemDrive% 残留（历史事故项）
    p = os.path.join(BACKEND, "%SystemDrive%")
    if os.path.exists(p):
        hits.append(p)
    check(not hits, "R3", f"backend 无构建产物（命中 {len(hits)}"
                          f"{': ' + hits[0] if hits else ''}）")


# ── R4: 版本源齐平 ─────────────────────────────────────────────────────────
def r4_version_sync() -> None:
    sources = [
        ("tauri.conf.json", os.path.join(SRC_ROOT, "src-tauri", "tauri.conf.json"),
         r'"version"\s*:\s*"([0-9]+\.[0-9]+\.[0-9]+)"'),
        ("Cargo.toml", os.path.join(SRC_ROOT, "src-tauri", "Cargo.toml"),
         r'(?m)^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"'),
        ("package.json", os.path.join(SRC_ROOT, "package.json"),
         r'"version"\s*:\s*"([0-9]+\.[0-9]+\.[0-9]+)"'),
        ("frontend/package.json", os.path.join(SRC_ROOT, "frontend", "package.json"),
         r'"version"\s*:\s*"([0-9]+\.[0-9]+\.[0-9]+)"'),
        ("_build_version.py", os.path.join(BACKEND, "_build_version.py"),
         r'BUILD_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"'),
    ]
    vers = {}
    missing = []
    for name, path, pat in sources:
        if not os.path.isfile(path):
            missing.append(name)
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            m = re.search(pat, f.read())
        vers[name] = m.group(1) if m else "?"
    if missing:
        check(False, "R4", f"版本源缺失: {missing}")
        return
    uniq = set(vers.values())
    check(len(uniq) == 1, "R4", f"六处版本齐平 = {sorted(uniq)} ({vers.get('tauri.conf.json')})")


# ── R5: 源码不得含明文凭证 ─────────────────────────────────────────────────
_CRED_PAT = re.compile(
    r'(?i)\b(?:token|skey|sessionid|session_id|passwd|password|secret)\b\s*=\s*["\'][A-Za-z0-9_\-]{16,}["\']'
)


def r5_no_plaintext_credential() -> None:
    hits = []
    for f in walk(BACKEND, (".py",)):
        if f.endswith(("errcode_data.py",)):
            continue
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    if _CRED_PAT.search(line):
                        hits.append(f"{os.path.basename(f)}:{i}")
                        break
        except Exception:
            continue
    check(not hits, "R5", f"源码无明文凭证（命中 {len(hits)}"
                          f"{': ' + hits[0] if hits else ''}）")


# ── R6: 停止走 /quit，不得 kill 浏览器 ─────────────────────────────────────
_KILL_PAT = re.compile(r'(?i)\b(?:os\.system|subprocess\S*)\s*\(?[^)]*\btaskkill\b')


def r6_no_browser_kill() -> None:
    hits = []
    for f in walk(BACKEND, (".py",)):
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                txt = fh.read()
            if "taskkill" in txt and "/im" in txt.lower():
                for i, line in enumerate(txt.splitlines(), 1):
                    if "taskkill" in line.lower() and ("camoufox" in line.lower()
                                                       or "chrome" in line.lower()
                                                       or "firefox" in line.lower()):
                        hits.append(f"{os.path.basename(f)}:{i}")
                        break
        except Exception:
            continue
    check(not hits, "R6", f"无 taskkill 杀浏览器（命中 {len(hits)}"
                          f"{': ' + hits[0] if hits else ''}）")


# ── R7: 昵称源 SSOT，不得用已推翻的批量查询 ────────────────────────────────
# ══════════ R7 已删除（2026-09-25 用户裁定）══════════════════════════════
#
# 原 R7「昵称链路禁批量查询」依据的是**已作废条目**：
#   本分支已于 2026-09-14 经用户授权解禁主动昵称查询
#   （api/platform.py:372 原文："本分支按用户 2026-09-14 授权「全解除」已解禁"）。
#   用户 2026-09-25 再次确认："已解禁"。
#
# 处置：**删除规则**，不做「挂起」也不降级警告。
# 判据：把已作废的铁律机械化为门禁 = 造一个错误拦路虎，比没有门禁更坏。
#       作废条目应进知识库 `01_铁律与红线.md` §附「作废条目」留档，不进门禁。
#
# 残留符号说明（勿再当违规）：/bcc/user_info → c.bulk_user_info() 是在用链路
#   platform-page.tsx:402 → platformApi.userInfo → /bcc/user_info
# ═══════════════════════════════════════════════════════════════════════


# ── R8: 数据契约（ADR-012）—— 委托 audit_data_contract.py 六项 ──────────
# 委托而非重写：避免同一判据两套实现漂移（SSOT）。
def _load_datacontract_module():
    """加载 audit_data_contract 模块（SSOT 判据）。抽出成函数只为自检可注入。

    2026-09-27 体检修复（P1）：原实现把「定位 + 加载」内联在 r8_data_contract()，
    而 audit_data_contract 的扫描根 `_BACKEND` **硬编码真仓**，导致 selftest 的
    临时目录替换对 R8 无效 ⇒ R8 六项在自检中**从未被负控**。抽出本 seam 后，
    selftest 可用替身模块注入「R8 子项报红」形态（与 R9 的 _load_redline_module 同法）。
    """
    import importlib.util
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "audit_data_contract.py")
    if not os.path.isfile(p):
        raise FileNotFoundError(p)
    spec = importlib.util.spec_from_file_location("_adc", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def r8_data_contract():
    try:
        mod = _load_datacontract_module()
    except Exception as e:  # noqa: BLE001
        # 诚实降级：判据脚本缺失/损坏 → 不许假装通过。
        check(False, "R8", f"数据契约审计脚本不可执行 → 无法判定"
                           f"（{type(e).__name__}: {e}）")
        return
    for code, fn in mod.CHECKS:
        ok, desc, evidence = fn()
        extra = f"（{evidence[0]}）" if (evidence and not ok) else ""
        check(ok, code, f"{desc}{extra}")


# ── R9: 审计红线计数（D-02）—— 委托 audit_redline_count.py ──────────────
# 判据住在 audit_redline_count.count()（SSOT：THRESHOLD=20 / DEFAULT_SINCE）。
# 本函数只做三件事：委托、可见化、诚实降级。**不重写判据、不加参数。**
def _load_redline_module():
    """加载 audit_redline_count 模块（SSOT 判据）。抽出成函数只为自检可注入。"""
    import importlib.util
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "audit_redline_count.py")
    if not os.path.isfile(p):
        raise FileNotFoundError(p)
    spec = importlib.util.spec_from_file_location("_arc", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def r9_audit_redline():
    try:
        mod = _load_redline_module()
    except Exception as e:  # noqa: BLE001
        # 诚实降级：门禁的前提（判据脚本）缺失/损坏时，不许假装通过。
        check(False, "R9", f"审计红线计数不可执行 → 无法判定"
                           f"（{type(e).__name__}: {e}）")
        return
    try:
        r = mod.count(mod.DEFAULT_SINCE)
    except Exception as e:  # noqa: BLE001
        # git 不可用 / 仓库损坏 / since sha 失效 —— 一律诚实报「无法判定」，
        # 不让异常炸掉整个门禁，也不让它 PASS 冒充正常。
        check(False, "R9", f"红线计数失败（git 不可用？）→ 无法判定"
                           f"（{type(e).__name__}: {e}）")
        return

    n, thr, rem = r.get("count"), r.get("threshold"), r.get("remaining")
    if r.get("triggered"):
        check(False, "R9",
              f"🔴 审计红线已触发: {n}/{thr}（since {r.get('since')}）"
              f" → 应启动全库审计 + 功能冻结（本次提交本身仍允许）")
    else:
        # 可见化：未触发也要让人知道还剩几个，杜绝「没人看 = 没人知道」。
        check(True, "R9",
              f"🟢 审计红线未触发: {n}/{thr}，距红线还差 {rem} 个节点"
              f"（since {r.get('since')}）")


# ── R10: 源码树不得残留 cargo target（构建缓存已迁出树外）────────────────────
def r10_no_cargo_target_in_src() -> None:
    """铁律「源码树零构建产物」：cargo target 必须重定向到树外。

    为何需要它：`src-tauri/target/` 实测单调膨胀（`debug/incremental` 每次重编
    新增 ~460M 快照且旧份不回收；实测一次构建即 4.0G、历史峰值 42G），根因是
    「部署路径/源码路径隔离」从未覆盖**编译期缓存**。该缓存已按显式配置
    （`src-tauri/.cargo/config.toml` 的 `target-dir`）迁出树外；本门禁防止
    有人绕过脚本直接 `cargo build` / `npx tauri build` 时静默回落树内。
    """
    p = os.path.join(SRC_ROOT, "src-tauri", "target")
    if os.path.isdir(p):
        # 🔴 2026-10-03 修判据缺陷（**误报**，实测 7486 文件 / 613MB）：
        #   `target/debug/binaries` 是**目录联接（junction）** → 指向
        #   `src-tauri/binaries`（tauri 打包时 sidecar 产物的**来源**，
        #   不是构建缓存）。原实现用 os.walk 递归 ⇒ 把**产物源本身**
        #   7486 个文件全算成「源码树残留构建产物」，而该目录**一字节不占**
        #   （`ls -la` 显示 total 0，里面只有一个链接）。
        #   ⚠️ 这不只是噪音：它诱导人 `rm -rf src-tauri/target` ——
        #   而那会**跟随联接清空 src-tauri/binaries（613MB 产物源）**，
        #   正是本项目已发生过的事故类型（worktree remove --force 清空
        #   主仓 node_modules）。故判据必须**剪掉联接**。
        #
        # ⚠️ 判据用 **realpath 前缀**而非 `os.path.islink`：实测在 Windows 上
        #   该联接 `os.path.islink()` 返回 **False**（junction 不被 islink 识别），
        #   而 `os.path.realpath()` 与 `abspath()` **不同** ⇒ 后者才可靠。
        _root_real = os.path.realpath(p)
        _seen = set()
        n = 0
        for root, dirs, files in os.walk(p):
            rp = os.path.realpath(root)
            # 已进入过的地方不再重复计数（联接可能构成环）
            if rp in _seen:
                dirs[:] = []
                continue
            _seen.add(rp)
            if rp != _root_real and not rp.startswith(_root_real + os.sep):
                # 越出 target 真实边界 ⇒ 这是联接/挂载，别跟着走
                dirs[:] = []
                continue
            n += len(files)
        if n == 0:
            check(True, "R10",
                  "源码树无 cargo target 产物（仅联接/空目录，不计）")
        else:
            check(False, "R10",
                  f"源码树残留 cargo target（{n} 文件）: {p}")
    else:
        check(True, "R10", "源码树无 cargo target（构建缓存已迁出树外）")


# ── R11: 得用已废弃 profile 字面量作 user_data_dir（单 profile 铁律）────────
def r11_no_legacy_profile_literal() -> None:
    """铁律：账号浏览器 profile 一律由 `accounts.profile_dir_of(env_path)` 推导。

    `vb_profile_default` / `vb_profile_dm` / `pw_profile_dm` 是**历史遗留名**：
      - `vb_profile_dm` 全仓零业务引用（死链）；
      - `pw_profile_dm` 曾是默认形参，但实现恒被 `profile_dir_of` 覆盖 ⇒ 传它无效；
      - `vb_profile_default` 仅作默认账号的 profile **名**（解析到 app_root()，非随包资源）。
    把它们当 `user_data_dir=` 实参传入，会让「单 profile 铁律」在读者/后续改动中失真（实测
    2026-09-29：`link_resolve` 因此漏传 profile，兜底路径直接抛错不可用）。
    判据：源码中不得出现 `user_data_dir="<遗留名>"`（含 def 默认值）。
    """
    _legacy = ("vb_profile_default", "vb_profile_dm", "pw_profile_dm")
    hits = []
    for f in walk(BACKEND, (".py",)):
        if os.path.basename(f) == "check_iron_rules.py":
            continue
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    if line.lstrip().startswith("#") or "user_data_dir" not in line:
                        continue
                    for _n in _legacy:
                        if (f'user_data_dir="{_n}"' in line or f"user_data_dir='{_n}'" in line
                                or f'user_data_dir = "{_n}"' in line
                                or f"user_data_dir = '{_n}'" in line):
                            hits.append(f"{os.path.basename(f)}:{i}")
                            break
        except Exception:
            continue
    check(not hits, "R11", f"无遗留 profile 字面量作 user_data_dir（命中 {len(hits)}"
                           f"{': ' + hits[0] if hits else ''}）")


# ── R12: 凭证外发审计（数据是否离开受信边界）────────────────────
def _load_credexposure_module():
    """加载 check_credential_exposure 模块（SSOT 判据）。抽出成函数只为自检可注入。"""
    import importlib.util
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "check_credential_exposure.py")
    if not os.path.isfile(p):
        raise FileNotFoundError(p)
    spec = importlib.util.spec_from_file_location("_cce", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def r12_credential_exposure() -> None:
    """凭证外发审计（ADR-031）—— 回答「数据是否离开受信边界」。

    两档：A DISTRIBUTED（分发路径携带凭证）= 阻断；
          B STRAY（源码树散落凭证类文件/目录）= 警告。
    真源：scripts/check_credential_exposure.py。
    """
    try:
        mod = _load_credexposure_module()
    except Exception as e:  # noqa: BLE001
        check(False, "R12-A", f"无法加载凭证外发审计模块: {e}")
        return
    try:
        dist = mod.check_distributed(SRC_ROOT)
    except Exception as e:  # noqa: BLE001
        dist = [{"src": "?", "detail": f"执行异常: {e}"}]
    check(not dist, "R12-A",
          f"无分发路径携带凭证（命中 {len(dist)}"
          f"{': ' + str(dist[0].get('src', '')) if dist else ''}）")
    try:
        stray = mod.check_stray(SRC_ROOT)
    except Exception as e:  # noqa: BLE001
        stray = [{"path": f"(异常: {e})"}]
    check(not stray, "R12-B",
          f"源码树无凭证类残留（命中 {len(stray)}"
          f"{': ' + str(stray[0].get('path', '')) if stray else ''}）")


# ── R13: 用户可见文案不得含内部开发信息 ─────────────────────────────────────
# 出处：用户 2026-09-29 全站审计裁定。比「复述控件」更严重的反模式 ——
# 「把调试信息写进用户界面」：用户不需要知道 /api 路径、wp/dm 进程代号、
# M1 探针编号、内部参考项目名。它们对用户零信息量，却暴露实现细节。
# 判据范围：前端组件里会渲染给人看的说明字段（description/subtitle/hint/
# tip/help/note/placeholder/label）。空态(EmptyState)说明同属用户可见，一并覆盖。
_UI_COPY_KEY = re.compile(r"\b(description|subtitle|hint|tip|help|note|placeholder|label)=")
_UI_INTERNAL_PATTERNS = (
    (re.compile(r"/api/[a-z_]"), "内部接口路径 /api/…"),
    (re.compile(r"better[-_]douyin", re.I), "参考项目名 better-douyin"),
    (re.compile(r"M1\s*(?:能力)?探针"), "内部探针编号 M1"),
    (re.compile(r"（wp）|（dm）|\(wp\)|\(dm\)"), "内部进程代号 wp/dm"),
)


def r13_no_internal_info_in_ui_copy() -> None:
    """用户可见文案不得出现内部开发信息（接口路径 / 进程代号 / 探针编号 / 参考项目名）。"""
    fe = os.path.join(SRC_ROOT, "frontend", "src")
    hits: list[str] = []
    for f in walk(fe, (".tsx",)):
        # 排除开发用预览 harness（preview-*.tsx 只被 preview*.html 引用，不进应用产物，
        # 其中的「对标 better-douyin」等开发参照对开发者有效，不属用户可见文案）。
        if os.path.basename(f).startswith("preview"):
            continue
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    if not _UI_COPY_KEY.search(line):
                        continue
                    for pat, why in _UI_INTERNAL_PATTERNS:
                        if pat.search(line):
                            hits.append(f"{os.path.basename(f)}:{i} [{why}]")
                            break
        except Exception:  # noqa: BLE001
            continue
    check(not hits, "R13",
          f"用户可见文案无内部开发信息（命中 {len(hits)}"
          f"{': ' + hits[0] if hits else ''}）")


# ── R14: 部署数据根声明文件名一致（防静默漂移）─────────────────────────────
# 出处：2026-09-29 方案②（部署副本自描述数据根）。`vbrowser._deploy_declared_root()`
# 读文件名，`scripts/deploy.py` 写文件名 —— 任一拼错即**静默失效**（双击 exe 又落空根、
# 登录报误导性「用户名或口令错误」）。SSOT = vbrowser.py 的 `_DEPLOY_ROOT_MARKER`。
def r14_deploy_root_marker_consistent() -> None:
    vf = os.path.join(BACKEND, "vbrowser.py")
    df = os.path.join(SRC_ROOT, "scripts", "deploy.py")
    try:
        with open(vf, encoding="utf-8", errors="replace") as f:
            vt = f.read()
        with open(df, encoding="utf-8", errors="replace") as f:
            dt = f.read()
    except Exception as e:  # noqa: BLE001
        check(False, "R14", f"读取失败: {e}")
        return
    m = re.search(r'_DEPLOY_ROOT_MARKER\s*=\s*"([^"]+)"', vt)
    if not m:
        check(False, "R14", "vbrowser.py 未定义 _DEPLOY_ROOT_MARKER（SSOT 缺失）")
        return
    name = m.group(1)
    check(name in dt, "R14", f"部署数据根声明文件名与 SSOT 一致（{name}）")


# ── R15: 配置中心字段文案「简洁性」（用户 2026-09-30 定的 UI 文案铁律）──────────
# 出处：用户原话「55 个字的说明也很长啊……只需告诉他们这个选项是干嘛的就行了」。
# 判据 SSOT：scripts/check_schema_copy.py（hint≤18 / label≤18 / 无开发信息）。
# 本函数只调用，不自带阈值 —— 避免两处漂移。
def r15_schema_copy_concision() -> None:
    sub = os.path.join(SRC_ROOT, "scripts", "check_schema_copy.py")
    if not os.path.isfile(sub):
        check(False, "R15", "判据脚本缺失: scripts/check_schema_copy.py")
        return
    import subprocess
    py = sys.executable or "python"
    try:
        r = subprocess.run([py, sub], capture_output=True, text=True, timeout=30)
        out = (r.stdout or "").strip().splitlines()
        detail = next((l.strip() for l in out if "[PASS]" in l or "[FAIL]" in l), "")
        check(r.returncode == 0, "R15",
              f"配置中心字段文案简洁（hint≤18字）{('· ' + detail) if detail else ''}")
    except Exception as e:  # noqa: BLE001
        check(False, "R15", f"调用 check_schema_copy 失败: {type(e).__name__}")



RULES = [r1_source_has_no_data, r2_data_root_no_source,
         r3_no_build_artifacts_in_src, r4_version_sync,
         r5_no_plaintext_credential, r6_no_browser_kill,
         r8_data_contract, r9_audit_redline,
         r10_no_cargo_target_in_src, r11_no_legacy_profile_literal,
         r12_credential_exposure, r13_no_internal_info_in_ui_copy,
         r14_deploy_root_marker_consistent, r15_schema_copy_concision]

# ── 分级：哪些阻断提交，哪些只警告 ─────────────────────────────────────────
# 判据（2026-09-25 实测校准）：只有**会进入提交内容**的违规才阻断。
#   · R2 数据根 .py：数据根不在 git 内（不会进提交）→ 磁盘卫生问题 → 警告
#   · R3 backend/build：已被 .gitignore:53 忽略（不会进提交）→ 同上 → 警告
#   · 其余项直接影响提交内容或产品行为 → 阻断
# 理由：门禁若把"不影响提交"的问题也阻断，会被人绕过 —— 那时的门禁比没有更坏。
# ⚠ 2026-09-25：R7 暂挂起 —— 检测到 /bcc/user_info → c.bulk_user_info()
# 是**在用链路**（前端 platform-page.tsx:402 → platformApi.userInfo → 后端），
# 但用户铁律写「昵称链路绝不主动批量查（怕风控）」，
# 而 api/platform.py:372 又记「本分支 2026-09-14 已授权解禁」。
# 两条冲突，**未裁定前不得阻断**（门禁误伤比缺门禁更坏）。
# 裁定后：若维持"绝不调用"→ 从 PENDING 移除并下线该端点；
#         若确认"已解禁"   → 删除本规则。
PENDING: set[str] = set()

# 磁盘卫生类（不影响提交内容）→ 仅警告
WARN_ONLY = {"R2", "R3", "R9", "R10", "R12-B"}   # R9 理由见顶部「R9 为何是 WARN_ONLY」


def run() -> int:
    print("=" * 70)
    print("铁律机械门禁 —— 声明式规则不靠自觉")
    print("=" * 70)
    print(f"  源码根 : {SRC_ROOT}")
    print(f"  数据根 : {DATA_ROOT}")
    if DATA_ROOT in FORBIDDEN_ROOTS:
        print(f"  ⛔ 数据根命中 FORBIDDEN_ROOTS —— 环境串用")
    print("-" * 70)
    for r in RULES:
        try:
            r()
        except Exception as e:  # noqa: BLE001
            check(False, "???", f"{r.__name__} 执行异常: {type(e).__name__}: {e}")
    print("-" * 70)
    failed = [x for x in RESULTS if not x[0]]
    warned = [x for x in failed if x[1] in WARN_ONLY or x[1] in PENDING]
    blocking = [x for x in failed
                if x[1] not in WARN_ONLY and x[1] not in PENDING]
    print(f"  合计: {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，"
          f"未通过 {len(failed)}（阻断 {len(blocking)} / 警告 {len(warned)}）")
    if warned:
        print("\n警告项（不阻断提交，属磁盘卫生）：")
        for _, rid, d in warned:
            print(f"  - {rid}: {d}")
    if blocking:
        print("\n⛔ 阻断项（必须修复）：")
        for _, rid, d in blocking:
            print(f"  - {rid}: {d}")
        return 1
    if warned:
        print("\n⚠ 仅有警告项，允许提交")
    else:
        print("\n✓ 铁律门禁全部通过")
    return 0


def selftest() -> int:
    """D-07：自证「失败态会变红」—— 注入违规样本，断言门禁确实报红。"""
    import tempfile
    print("=" * 70)
    print("自检：验证门禁在违规时**真的会报红**（D-07）")
    print("=" * 70)

    global BACKEND, SRC_ROOT, DATA_ROOT
    tmp = tempfile.mkdtemp(prefix="iron_selftest_")
    fake_src = os.path.join(tmp, "src")
    fake_backend = os.path.join(fake_src, "backend")
    os.makedirs(fake_backend, exist_ok=True)

    # 故意制造违规：
    #   R1 源码树出现 members/
    os.makedirs(os.path.join(fake_src, "members"), exist_ok=True)
    #   R10 源码树出现 cargo target
    #     ⚠️ 2026-10-03：判据已改为「跳过联接 + 只数**真实文件**」
    #       （空目录/仅联接不算残留，见 r10_no_cargo_target_in_src）。
    #       故样本必须**含真实文件**，否则造的是空壳 → 门禁正确不红，
    #       自检却会误判「形同虚设」（实测踩到）。
    _tgt = os.path.join(fake_src, "src-tauri", "target")
    os.makedirs(_tgt, exist_ok=True)
    with open(os.path.join(_tgt, "_probe.bin"), "wb") as f:
        f.write(b"\x00" * 16)
    #   R11 用已废弃 profile 字面量作 user_data_dir
    with open(os.path.join(fake_backend, "legacy.py"), "w", encoding="utf-8") as f:
        f.write('launch(user_data_dir="pw_profile_dm")\n')
    #   R3 backend 出现 .exe
    with open(os.path.join(fake_backend, "evil.exe"), "wb") as f:
        f.write(b"MZ")
    #   R5 明文凭证
    with open(os.path.join(fake_backend, "cred.py"), "w", encoding="utf-8") as f:
        f.write('TOKEN = "abcdefghijklmnop123456"\n')
    #   R6 taskkill 杀浏览器
    with open(os.path.join(fake_backend, "killer.py"), "w", encoding="utf-8") as f:
        f.write('os.system("taskkill /im camoufox.exe /f")\n')
    # （R7 已于 2026-09-25 用户裁定删除，自检不再造该样本）
    #   R2 数据根出现源码
    fake_data = os.path.join(tmp, "data")
    os.makedirs(fake_data, exist_ok=True)
    with open(os.path.join(fake_data, "leak.py"), "w", encoding="utf-8") as f:
        f.write("x = 1\n")
    #   R2 反例：部署产物目录（appinternals）下的 .py **不得**被判红
    #     —— 与上面顶层 leak.py 同处一棵树，用一次 run 同时验「豁免生效」+「顶层仍红」。
    os.makedirs(os.path.join(fake_data, "appinternals"), exist_ok=True)
    with open(os.path.join(fake_data, "appinternals", "runtime.py"),
              "w", encoding="utf-8") as f:
        f.write("x = 1\n")
    #   R13 前端文案含内部开发信息（接口路径 + 进程代号）
    fake_fe = os.path.join(fake_src, "frontend", "src", "components")
    os.makedirs(fake_fe, exist_ok=True)
    with open(os.path.join(fake_fe, "leaky.tsx"), "w", encoding="utf-8") as f:
        f.write('    <Section title="x" description="只读 /api/accounts · 凭证守护（wp）" />\n')
    #   R14 声明文件名不一致（vbrowser 的 SSOT 名未出现在 deploy.py）
    os.makedirs(os.path.join(fake_src, "scripts"), exist_ok=True)
    with open(os.path.join(fake_backend, "vbrowser.py"), "w", encoding="utf-8") as f:
        f.write('_DEPLOY_ROOT_MARKER = "dyautodm_app_root.txt"\n')
    with open(os.path.join(fake_src, "scripts", "deploy.py"), "w", encoding="utf-8") as f:
        f.write("# 注入样本：故意不含声明文件名\n")

    saved = (SRC_ROOT, BACKEND, DATA_ROOT, RESULTS[:])
    SRC_ROOT, BACKEND, DATA_ROOT = fake_src, fake_backend, fake_data
    RESULTS.clear()

    # ── R9 负控：注入「红线已触发」形态，断言 R9 真的变红 ───────────────
    # R9 不依赖 SRC_ROOT/BACKEND/DATA_ROOT（它读的是 git log），所以现有
    # 「换临时目录」的造违规手段对它无效，必须单独注入。
    # 做法：monkeypatch 模块加载 seam `_load_redline_module`，返回一个替身
    # 模块，其 count() 返回 triggered=True 的字典（形态与真实 count() 一致）。
    # 选这个 seam 而不是 patch count()，是为了让**加载失败降级分支**也留在
    # 被测路径上，而不是被绕过。
    class _FakeRedlineModule:
        DEFAULT_SINCE = "deadbeef"
        THRESHOLD = 20

        @staticmethod
        def count(since):
            return {"since": since, "threshold": 20, "count": 30,
                    "remaining": 0, "triggered": True,
                    "counted": [], "skipped_sample": [], "skipped_total": 0}

    saved_loader = globals()["_load_redline_module"]
    globals()["_load_redline_module"] = lambda: _FakeRedlineModule

    # ── R8 负控（2026-09-27 补齐）：注入「某 R8 子项报红」形态 ───────────
    # R8 委托 audit_data_contract.py，其扫描根硬编码真仓，故同样需要 seam 注入。
    # 替身模块的 CHECKS 里放一个恒 False 的检查项，断言 R8-* 真的变红。
    class _FakeDCModuleFail:
        CHECKS = (
            ("R8-1", lambda: (False, "写入出口收敛（注入样本）", ["injected"])),
            ("R8-2", lambda: (True, "类型注册表完整", [])),
        )

    class _FakeDCModuleClean:
        CHECKS = (
            ("R8-1", lambda: (True, "写入出口收敛", [])),
            ("R8-2", lambda: (True, "类型注册表完整", [])),
        )

    # ── R12 负控：注入「分发路径携带凭证」形态 ───────────────────────
    class _FakeCredModule:
        @staticmethod
        def check_distributed(root):
            return [{"src": "injected:tauri.conf.json", "detail": "注入样本"}]

        @staticmethod
        def check_stray(root):
            return []

    saved_cred = globals()["_load_credexposure_module"]
    globals()["_load_credexposure_module"] = lambda: _FakeCredModule

    saved_dc = globals()["_load_datacontract_module"]
    globals()["_load_datacontract_module"] = lambda: _FakeDCModuleFail

    failed_expect = {"R1", "R2", "R3", "R5", "R6", "R9", "R10", "R11", "R12-A", "R8-1", "R13", "R14"}
    for r in RULES:
        try:
            r()
        except Exception as e:  # noqa: BLE001
            check(False, "???", f"{r.__name__} 异常: {e}")

    got_failed = {rid for ok, rid, _ in RESULTS if not ok}
    # ── R2 正控（豁免面精确）：负控 run 里数据根同时放 ① 顶层 leak.py（必须计）
    #    ② appinternals/runtime.py（豁免，不得计）⇒ R2 的「命中数」必须恰为 1，
    #    且被点的不是 appinternals。若豁免名单失效（如改名后漏加），命中数会变 2 ⇒ 报错。
    r2_details = [d for ok, rid, d in RESULTS if rid == "R2"]
    r2_exemption_ok = any(("命中 1 个" in d and "appinternals" not in d)
                          for d in r2_details)
    globals()["_load_redline_module"] = saved_loader   # 无论如何都要还原
    globals()["_load_datacontract_module"] = saved_dc
    globals()["_load_credexposure_module"] = saved_cred
    SRC_ROOT, BACKEND, DATA_ROOT, _ = saved
    RESULTS.clear()

    # ── R9 正控：注入「未触发」形态，断言 R9 真的 PASS ───────────────────
    # 只有负控的门禁是自证不了的：一个「永远 False」的判据也能过负控。
    # 双向都验才算证明 R9 判据**跟着数据走**，不是写死的红灯。
    class _CleanRedlineModule(_FakeRedlineModule):
        @staticmethod
        def count(since):
            return {"since": since, "threshold": 20, "count": 3,
                    "remaining": 17, "triggered": False,
                    "counted": [], "skipped_sample": [], "skipped_total": 0}

    saved_loader2 = globals()["_load_redline_module"]
    globals()["_load_redline_module"] = lambda: _CleanRedlineModule
    try:
        r9_audit_redline()
        r9_clean = all(ok for ok, rid, _ in RESULTS if rid == "R9")
    except Exception as e:  # noqa: BLE001
        r9_clean = False
        print(f"  R9 正控异常: {type(e).__name__}: {e}")
    finally:
        globals()["_load_redline_module"] = saved_loader2
        RESULTS.clear()

    # ── R8 正控：注入「全部 R8 子项通过」形态，断言 R8 不误报 ───────────
    # 与 R9 同理：只有负控的自证不了（恒 False 的判据也能过负控）。
    saved_dc2 = globals()["_load_datacontract_module"]
    globals()["_load_datacontract_module"] = lambda: _FakeDCModuleClean
    try:
        r8_data_contract()
        r8_clean = all(ok for ok, rid, _ in RESULTS if rid.startswith("R8"))
    except Exception as e:  # noqa: BLE001
        r8_clean = False
        print(f"  R8 正控异常: {type(e).__name__}: {e}")
    finally:
        globals()["_load_datacontract_module"] = saved_dc2
        RESULTS.clear()

    missing = failed_expect - got_failed
    ok = not missing and r9_clean and r8_clean and r2_exemption_ok
    print("-" * 70)
    print(f"  期望报红: {sorted(failed_expect)}")
    print(f"  实际报红: {sorted(got_failed)}")
    print(f"  R9 正控（未触发形态应 PASS）: {'通过' if r9_clean else '未通过'}")
    print(f"  R8 正控（子项全通过应 PASS）: {'通过' if r8_clean else '未通过'}")
    print(f"  R2 正控（appinternals 豁免 / 顶层仍红）: "
          f"{'通过' if r2_exemption_ok else '未通过'}")
    if missing:
        print(f"\n✗ 自检失败：以下规则在违规样本下**没有变红** = 形同虚设: {sorted(missing)}")
        return 1
    if not r9_clean:
        print("\n✗ 自检失败：R9 在「未触发」形态下没有 PASS = 判据写死，非数据驱动")
        return 1
    if not r8_clean:
        print("\n✗ 自检失败：R8 在「子项全通过」形态下误报 = 判据不可信")
        return 1
    if not r2_exemption_ok:
        print("\n✗ 自检失败：R2 未精确豁免部署运行时目录（appinternals）"
              "—— 豁免名单可能未跟上 contents 目录改名")
        return 1
    print("\n✓ 自检通过：所有可判定规则在违规时均会报红（非空架子）")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(run())
