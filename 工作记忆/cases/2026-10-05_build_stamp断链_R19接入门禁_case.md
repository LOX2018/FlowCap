# T-GATE / build_stamp：R19 接入门禁 + rust 陈旧未解

- **卡面**：`build_stamp` 来源戳断链（Codex 定 BLOCKER）
- **交付 sha**：`f5205c3f05a6877ed3732c4c0daa67dc4def4d3c`（2026-10-05 08:01:50）
- **改动**：`DYAutoDM_v2/scripts/check_iron_rules.py` +121 / −4（显式 pathspec，无夹带）
- **送审对象**：Codex。复审结论出来前本会话不再向群里报。
- **状态**：接线已完成并入库；**BLOCKER 未解除**（rust 陈旧仍在，见 §3）

---

## 1. 前提更正：`vite.config.ts` 断链从未成立

BLOCKER 原口径指「`vite.config.ts` 未纳入来源戳覆盖」。实测否证：

```bash
git show 1c755f9:DYAutoDM_v2/scripts/build_stamp.py | grep vite.config.ts
#   ("frontend", "vite.config.ts")
```

`1c755f9` 属 **v0.46.58**，远早于本次讨论的 `d775944`。
配套三步也已落地：`tsconfig.json` 无 references、`tsconfig.node.json` 无 composite 且 `noEmit:true`、旧 emit 的 `vite.config.js/.d.ts` 已删。

⇒ 这条不成立，第三次复现确认。改动不在这里。

## 2. 真实断链：commit 时刻无人消费 stamp

```bash
grep build_stamp .git/hooks/pre-commit      → 0 次
grep build_stamp check_iron_rules.py        → 0 处   # f5205c3 之前
```

`build_stamp` 只在**构建时**被消费：`scripts/build_all.py:181`、`scripts/build_sidecar.py:871`。
源码改过、戳判陈旧，**commit 门禁照样放行** —— 这才是断链。R19 接这头。

### 2.1 为何 `WARN_ONLY`，不阻断

依据 `check_iron_rules.py` 内既有分层判据：「只有**会进入提交内容**的违规才阻断」。

| 判据 | 结论 |
|---|---|
| `frontend/dist`、`*.exe`、`artifacts/.build_stamp.json` 均 gitignored | 不进提交内容 ⇒ 与 R2 / R3 同一分层 |
| RUST_GLOBS 136 文件里 **132 属 `frontend/`**（97%） | fail-closed = 改任意一个 `.tsx` 都需一次完整 `tauri build` 才能 commit |
| R17 判 `dist` 可 fail-closed | `vite build` ≈ 5s，可当场解锁 |
| R19 判 `exe` 不可 fail-closed | 解锁需一次完整 `tauri build`，不可当场做 |

### 2.2 用 `check()` 而非 `verify()`

- `check()`：只比**源码戳**（`compute()` vs `read_record()['stamp']`）
- `verify()`：源码戳 **+ exe md5** 双判

commit 时刻只需确认「源码未偏离上次构建」，无需校验产物 md5 ⇒ 取 `check()`。
产物 md5 判定留给 `verify_build.py` 一类构建期门禁，避免双判。

### 2.3 判据 SSOT 与诚实降级

R19 只做**委托 + 降级 + 可见化**，不重写戳口径。口径唯一来源 `build_stamp.py` 的 `__main__`：

```
[<kind>] 判定       = ✅ 一致          /   ❌ <reason>
```

解析不到 `✅`/`❌` 判定行（脚本输出改形）⇒ **直接报红要求人工看**，不猜判定。

## 3. 未解除项：rust 陈旧是真的（不是误判）

```
[rust]    当前源码戳 = 7194ee95e79cbeebbe…（136 文件）
[rust]    构建记录   = da2c0130cc6b6636…
[rust]    判定       = ❌ 陈旧
[sidecar] 判定       = ✅ 一致
```

03:10 那次构建之后三笔提交，全部落在 RUST_GLOBS 覆盖范围内：

```
2dea64b  06:23  frontend/src/App.tsx
d775944  06:57  frontend/tsconfig.json + frontend/tsconfig.node.json
bb043bc  07:02  frontend/src/components/kb/reply-kb.tsx
             frontend/src/components/logs/logs-page.tsx
```

**刷新需一次完整 `tauri build`。** 本次未触发构建、未改 `build_stamp.json`
（mtime 仍 `2026-10-05 03:10`）—— 不在本轮授权内。

⇒ 接线是已交付的；解陈旧不是。BLOCKER 卡面上这两件事不是一回事。

### 3.1 更正：exe 在磁盘上

本会话早前报告「`dyautodm-v2.exe` 不在磁盘」是**误报**，已更正：

```
C:\temp\dyautodm_build\design\debug\dyautodm-v2.exe
-rwxr-xr-x 21,986,304 B   mtime 2026-10-05 03:10

src-tauri/.cargo/config.toml  →  target-dir = "C:/temp/dyautodm_build/design"
```

target-dir 已重定向到树外（2026-09-29 架构决策：源码树零构建产物）。
误报起因是查了树内 fallback 路径 `src-tauri/target/{debug,release}/`。

`build_paths.exe_path()` 解析到树外正确路径，该 exe **存在**。

## 4. 交付验证

```
python scripts/check_iron_rules.py
  合计: 25 项，通过 23，未通过 2（阻断 0 / 警告 2）
  ⚠ 仅有警告项，允许提交

python scripts/check_iron_rules.py --selftest
  exit 0，8/8 正控通过
```

R19 三态注入（否则「恒 False 的解析」也能通过负控）：

| 注入态 | 预期 | 结果 |
|---|---|---|
| 部分陈旧（sidecar ✅ / rust ❌） | FAIL | ✓ |
| 全一致 | PASS | ✓ |
| 输出改形（无判定行） | FAIL（诚实降级） | ✓ |

负控由真仓实跑直接给出，非注入。

R4 六源齐平 `0.47.11`，未递增：门禁脚本不在 R4 认定的 6 个版本源内
（`tauri.conf.json` / `Cargo.toml` / `package.json` ×2 / `_build_version.py`），
`f195bbd` 加 R17+R18 同口径未递增。

`check_iron_rules.py` 本身 0 条 SyntaxWarning。门禁运行时出现的 4 条来自
R19 以子进程调用的 `build_stamp.py`，与本次改动无关。

## 5. 送审时需 Codex 判的两件事

BLOCKER 结案口径在此分叉，请显式认定判哪一件：

1. **接线是否够** —— commit 时刻现在会读 stamp 了。若够，接线即交付。
2. **陈旧是否必须清零** —— 需授权一次完整 `tauri build`。

另两项自陈，不在我的处置权内，请独立认定：
- 审计前提由被审方在提交正文里改写（`vite.config.ts` 断链不成立）
- 降级方式（`WARN_ONLY`）由被审方选定，非审计方指定

---

## 附：本会话流程性更正（与本次代码无关，记账用）

- 误跑 `git stash` 未 `pop`，工作区被清空；`stash@{0}` 内为 Qoder 名下
  `工作记忆/00_交接卡待办台账.md` +102 行改动，已 `stash pop` 原样恢复。
  台账全程未提交、未夹带、未修改。
- 早前误报「点名三样」：`--ease-out` / `--z-chrome` 为 Codex 07:45 自留项，
  不归 PM 名下派单。
- 早前误报「exe 不在磁盘」，见 §3.1。
