# -*- coding: utf-8 -*-
"""构建/部署范围门禁 —— 按**实际改动范围**决定要不要重建 sidecar。

## 为什么需要它（用户 2026-09-19 定调）

用户原话：「全量打包部署太慢了，版本校验加一个前置，当前端/后端的修改涉及联动时
再检验版本。这样加速测试进程」。

现状痛点：`build_sidecar.py`（3 份 PyInstaller）+ `tauri build` 全量约 12 分钟，
但**纯前端改动**（tsx/css）根本不需要重打 sidecar —— 那是 ~10 分钟纯浪费。
本项目已有先例判据（`flowcap-dev-guards` 铁律六）：
「先看 `git diff --stat` 有没有 backend/ 或任何 sidecar entry 的改动；
只有 frontend/ → 不升版本号，前端产物重跑后重部署即可」。

## 设计契约（不变式）

1. **判据取自「自上次构建以来的实际改动」，不取记忆**：改动集 =
   `git diff --name-only <基线sha> HEAD`（**已提交但尚未构建**的改动）
   ∪ 工作区 `git diff`（未提交）
   ∪ 暂存区 `git diff --cached`
   ∪ 未跟踪文件（`git ls-files --others --exclude-standard --full-name`，逐路径归类）。
   ⚠️ L-9 修复要点：旧实现**只看工作区/暂存/未跟踪**，看不到已提交(HEAD)的改动 ⇒
   提交后端文件后跑 `--dry-run` 报「后端/sidecar: 0」并计划跳过 sidecar 重建 = **漏打包**。
   基线 = 上次构建成功时的 HEAD，持久化于 `artifacts/.last_built_sha`。
2. **安全方向优先**：拿不准就**重建**（宁可慢，不可漏打）。只有「确认后端与 sidecar
   入口零改动」才允许跳过 sidecar。**基线缺失/失效时安全回退为全量重建**（无基线 ⇒
   无法证明「已提交改动里没有后端」，一律按需要重建处理）。
3. **`--fast` 是断言不是绕过**：调用方声明「只有前端变了」，脚本会**复核**；
   若发现后端改动（或基线缺失无法复核）则**拒绝执行**（exit 非 0），绝不允许静默漏打包。
4. **联动才校验版本**：一次交付若**同时**触及前端与后端（联动），则要求
   五处版本号齐平（调 `check_version_sync.py`）；只动一侧时不强制版本递增，
   避免纯前端修正被版本号门禁卡住。

## 基线（`artifacts/.last_built_sha`）的写入口径

基线必须代表「**已构建成功的那个 HEAD**」，因此**只允许在构建成功之后推进**。
本脚本是**范围门禁/规划器**（`--dry-run` 只算范围、非 dry-run 也只打印计划，
不自己执行构建），所以基线推进做成**显式动作** `--mark-built`：

    构建流程 = ① 跑本门禁算出范围 → ② 按计划执行构建 → ③ **构建成功后**跑 `--mark-built`

> ⚠️ **不要**在构建前推进基线：若构建失败而基线已前进，下次门禁会把失败的那批改动
> 当作「已构建」⇒ 静默漏打包（正是本门禁要防的那类事故）。拿不准就别写基线，
> 无基线会安全回退全量，只慢不漏。

## 用法

```bash
python scripts/build_and_deploy.py --dry-run   # 只算范围并打印计划（不构建）
python scripts/build_and_deploy.py             # 自动：按改动范围决定是否重建 sidecar
python scripts/build_and_deploy.py --fast      # 声明「仅前端」→ 跳 sidecar（会被复核）
python scripts/build_and_deploy.py --full      # 强制全量（重建 sidecar）
python scripts/build_and_deploy.py --mark-built  # 构建成功后：把当前 HEAD 记入基线
```

退出码：0 = 计划可执行；2 = `--fast` 与实测范围冲突（拒绝）；3 = 版本齐平门禁失败；
4 = `--mark-built` 写基线失败（拿不到 HEAD / 写盘失败）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 构建基线：上次构建成功时的 HEAD。构建成功后由 `--mark-built` 推进。
BASELINE_PATH = os.path.join(ROOT, "artifacts", ".last_built_sha")

# ── 改动归类：这些路径的改动意味着 **sidecar 需要重建** ──
# 2026-10-06 去壳：路径前缀随目录平铺同步（原 `FlowCap/` 前缀）。
# 🔴 这些前缀必须与**仓库根的目录结构**保持一致：失配不会报错，
#    只会让归类永远判定「无需重建」⇒ sidecar 静默用陈旧产物。
SIDECAR_ENTRY_PREFIXES = (
    "backend/",
    "daemon/",
    "src/qoder2api/",          # 非本项目，占位保持通用
)
BACKEND_SIGNAL = (
    "backend/",
    "daemon/",
    "scripts/build_sidecar.py",
    "scripts/_rebuild_",
)
FRONTEND_SIGNAL = (
    "frontend/src/",
    "frontend/*.html",
    "frontend/package.json",
    "frontend/vite.config.ts",
)
# Tauri 壳（改它需要重编 Rust，但**不需要**重打 sidecar）
SHELL_SIGNAL = (
    "src-tauri/",
)
VERSION_FILES = (
    "package.json",
    "frontend/package.json",
    "src-tauri/tauri.conf.json",
    "src-tauri/Cargo.toml",
    "backend/_build_version.py",
)


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _git(args: list[str]) -> subprocess.CompletedProcess:
    """只读 git 调用（cwd=ROOT），绝不写仓库。"""
    return _run(["git", *args])


def _lines(cp: subprocess.CompletedProcess) -> list[str]:
    return [ln.strip().replace("\\", "/")
            for ln in (cp.stdout or "").splitlines() if ln.strip()]


def current_head() -> str | None:
    """当前 HEAD 的完整 sha；非 git 仓库/无提交时返回 None。"""
    cp = _git(["rev-parse", "HEAD"])
    if cp.returncode != 0:
        return None
    sha = cp.stdout.strip()
    return sha or None


def read_baseline() -> str | None:
    """读基线 sha（文件不存在/不可读 → None）。不做 git 校验。"""
    try:
        with open(BASELINE_PATH, encoding="utf-8", errors="replace") as f:
            sha = f.read().strip()
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError:
        return None
    return sha or None


def baseline_is_valid(sha: str | None) -> bool:
    """基线 sha 必须能解析成一个提交；否则视为「无基线」。（只读）"""
    if not sha:
        return False
    cp = _git(["rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"])
    return cp.returncode == 0 and bool(cp.stdout.strip())


def write_baseline(sha: str | None = None) -> str | None:
    """把「已构建成功」的 HEAD 记入基线文件。sha=None 时取当前 HEAD。

    返回落盘的 sha；取不到 HEAD 或写盘失败返回 None（调用方据此判失败）。
    """
    sha = sha or current_head()
    if not sha:
        return None
    try:
        os.makedirs(os.path.dirname(BASELINE_PATH), exist_ok=True)
        with open(BASELINE_PATH, "w", encoding="utf-8", newline="\n") as f:
            f.write(sha + "\n")
    except OSError:
        return None
    return sha


def changed_files(base_sha: str | None = None) -> tuple[list[str], bool]:
    """改动集 = 基线..HEAD ∪ 工作区 ∪ 暂存 ∪ 未跟踪（相对仓库根，统一 `/`）。

    返回 (files, baseline_known)：
      - files：去重保序的路径清单（已排除门禁自身的基线文件）。
      - baseline_known：基线是否**可比对**（base_sha 有效且 diff 成功）。
        为 False 时表示「无有效基线 ⇒ 无法证明已提交改动里没有后端」，
        调用方必须按**安全方向**回退为全量重建。
    """
    out: list[str] = []
    baseline_known = False

    # ① 已提交但尚未构建的改动：基线..HEAD（L-9 缺口修复）
    if base_sha:
        cp = _git(["diff", "--name-only", base_sha, "HEAD"])
        if cp.returncode == 0:
            out += _lines(cp)
            baseline_known = True
        else:
            # 基线不可比对（被 gc / 非本仓提交 / 损坏）→ 安全回退
            baseline_known = False

    # ② 工作区未提交改动
    out += _lines(_git(["diff", "--name-only"]))
    # ③ 暂存区
    out += _lines(_git(["diff", "--name-only", "--cached"]))
    # ④ 未跟踪新文件（--full-name ⇒ 相对仓库根，与 classify 的 FlowCap/ 前缀一致）
    out += _lines(_git(["ls-files", "--others", "--exclude-standard", "--full-name"]))

    # 去重保序 + 排除门禁自身的基线文件（它落在 artifacts/ 下，不该算作「改动」）
    baseline_rel = "artifacts/.last_built_sha"
    seen, uniq = set(), []
    for f in out:
        f = f.replace("\\", "/")
        if f.endswith("/" + baseline_rel) or f == baseline_rel:
            continue
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq, baseline_known


def classify(files: list[str]) -> dict:
    def hit(prefixes: tuple[str, ...]) -> list[str]:
        r = []
        for f in files:
            for p in prefixes:
                if p.endswith("*"):
                    d, _, pat = p.rpartition("/")
                    if f.startswith(d + "/") and f.endswith(pat.lstrip("*")):
                        r.append(f)
                        break
                elif f.startswith(p):
                    r.append(f)
                    break
        return r

    backend = hit(BACKEND_SIGNAL)
    frontend = hit(FRONTEND_SIGNAL)
    shell = hit(SHELL_SIGNAL)
    version = [f for f in files if f in VERSION_FILES]
    return {
        "all": files,
        "backend": backend,
        "frontend": frontend,
        "shell": shell,
        "version": version,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="构建/部署范围门禁")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--fast", action="store_true", help="声明仅前端改动（跳过 sidecar 重建）")
    g.add_argument("--frontend-only", action="store_true",
                   help="显式声明「本次只交付前端」：即使树里有后端改动（属他人/未完成）"
                        "也跳过 sidecar 重建，只重编 exe。会打印被忽略的后端文件清单。")
    g.add_argument("--full", action="store_true", help="强制全量（重建 sidecar）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不构建")
    ap.add_argument("--mark-built", action="store_true",
                    help="构建**成功之后**执行：把当前 HEAD 记入基线 artifacts/.last_built_sha")
    args = ap.parse_args()

    # ── 显式基线推进：只允许在构建成功之后调用 ──
    if args.mark_built:
        head = current_head()
        if head is None:
            print("✗ --mark-built：拿不到当前 HEAD（非 git 仓库 / 无提交），拒绝写基线。")
            return 4
        if args.dry_run:
            print(f"(--dry-run：不写基线；将记录 {head[:12]} → {BASELINE_PATH})")
            return 0
        written = write_baseline(head)
        if not written:
            print(f"✗ --mark-built：写基线失败（{BASELINE_PATH}）")
            return 4
        print(f"✓ 已记录构建基线：{written[:12]} → {BASELINE_PATH}")
        return 0

    head = current_head()
    base_sha = read_baseline()
    base_ok = baseline_is_valid(base_sha)
    files, _ = changed_files(base_sha if base_ok else None)
    c = classify(files)

    print("=" * 68)
    print("构建范围门禁 · build_and_deploy")
    print("=" * 68)
    if base_ok:
        print(f"改动基线: {base_sha[:12]} → HEAD {(head or '?')[:12]}"
              f"  （自上次构建以来的已提交改动已计入 + 工作区/暂存/未跟踪）")
    else:
        why = ("基线文件不存在（尚未构建过）" if not base_sha
               else f"基线 `{base_sha[:12]}` 无法解析为提交（被 gc / 非本仓）")
        print(f"改动基线: 无 —— {why}")
        print("  ⚠ 安全回退：无法证明「已提交改动里没有后端」⇒ 一律按**需要重建**处理")
    print(f"改动文件数: {len(c['all'])}")
    for k, label in (("backend", "后端/sidecar"), ("frontend", "前端"),
                     ("shell", "Tauri 壳"), ("version", "版本号文件")):
        print(f"  · {label}: {len(c[k])}" + (f"  -> {', '.join(c[k][:4])}"
                                            + (" …" if len(c[k]) > 4 else "") if c[k] else ""))

    needs_sidecar = bool(c["backend"])
    if not base_ok:
        # 安全方向优先：无基线 ⇒ 视为全量
        needs_sidecar = True
    # 联动判据：一次交付同时触及前端与后端（或改了两侧的版本文件）
    linked = bool(c["frontend"]) and bool(c["backend"])

    if args.fast and needs_sidecar:
        print("\n✗ 拒绝：--fast 声明「仅前端」，但与实测范围冲突：")
        if not base_ok:
            print("    无有效构建基线 —— 无法证明「已提交改动里没有后端/sidecar 改动」。")
            print("    若有此改动被跳过即漏打包。请改用全量（去掉 --fast），"
                  "或先构建一次后用 --mark-built 建立基线。")
        else:
            for f in c["backend"][:8]:
                print("    - " + f)
            print("  安全方向优先：请改用全量（去掉 --fast），否则会漏打包 sidecar。")
        return 2

    if args.full:
        needs_sidecar = True

    if args.frontend_only:
        # 显式「只交付前端」：忽略后端改动（属他人/未完成的工作），但必须**大声报告**，
        # 且要如实说明部署后果：主程序与「已构建的 sidecar」一起被复制 ——
        # 若同版本 sidecar 已被别的构建覆盖过，则实际会把那些后端改动带上线。
        needs_sidecar = False
        if not base_ok:
            print("\n⚠️  --frontend-only：当前**无有效构建基线** ⇒ 无法证明「自上次构建以来的"
                  "已提交改动」里没有后端/sidecar 改动。请确认你在忽略的不是已提交的后端改动。")
        if c["backend"]:
            print("\n⚠️  --frontend-only：以下后端改动将被**忽略**（不重建 sidecar）：")
            for f in c["backend"]:
                print("    - " + f)
            print("  注意：deploy.py 会把**当前已构建的 sidecar** 一起复制到部署目录。")
            print("        若该 sidecar 恰好包含这些未完成改动，它们会随本次部署上线 ——")
            print("        并发工作线存在时，请先与其确认，或用 --full 重建一份干净的 sidecar。")

    print("\n判定：")
    print(f"  · 需要重建 sidecar : {'是' if needs_sidecar else '否'}")
    print(f"  · 前后端联动       : {'是' if linked else '否'}")

    if linked:
        cp = _run([sys.executable, "scripts/check_version_sync.py"])
        ok = cp.returncode == 0
        # 无参数时脚本可能只打印用法；以退出码为准，取尾部信息辅助判断
        tail = "\n".join((cp.stdout or cp.stderr).strip().splitlines()[-2:])
        print(f"  · 版本齐平门禁     : {'通过' if ok else '失败'}\n      {tail}")
        if not ok:
            print("  ⚠ 联动改动要求版本齐平；若刚改了版本请补齐五处（见 skill "
                  "flowcap-version-sync），或确认无需升版本。")
    else:
        print("  · 版本齐平门禁     : 跳过（未联动，纯单侧改动）")

    steps = []
    if needs_sidecar:
        steps.append("1) build_sidecar.py --onedir   (~10min, 3×PyInstaller)")
    steps.append(("2)" if needs_sidecar else "1)") + " tauri build --no-bundle        (~2min)")
    steps.append(("3)" if needs_sidecar else "2)") + " deploy.py                       (~10s)")
    print("\n计划步骤：")
    for s in steps:
        print("  " + s)
    if not needs_sidecar:
        print("\n  ⚡ 快速通道：跳过 sidecar 重建（沿用现有 binaries），预计 ~2 分钟")
    else:
        print("\n  全量构建，预计 ~12 分钟")

    if args.dry_run:
        print("\n(--dry-run：未执行任何构建)")
        return 0
    if not base_ok:
        print("\n  提示：构建成功后请执行 `--mark-built` 建立/推进基线，"
              "否则下次仍会安全回退全量。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
