# ADR-029：构建缓存迁出源码树（源码树零构建产物）

- **状态**：Accepted
- **日期**：2026-09-29
- **决策者**：LOX（用户拍板）
- **相关**：铁律 §四（环境目录物理隔离）、L-16、`src-tauri/.cargo/config.toml`、`scripts/build_paths.py`、`check_iron_rules.py` R10

---

## 1. 背景与问题

2026-09-29 实测：源码树 `C:\Users\LOX\Desktop\DYchajian` 膨胀至 **42G**，其中
`DYAutoDM_v2/src-tauri/target/` 独占 **40G**。

用户的疑问是「我明明把部署路径和源码路径隔离了，为什么还这么大」。**答案**：
隔离**没失效**，但隔离的是**运行期数据**（数据根 `C:\temp\dyautodm_design`）；
而 `target/` 是 **cargo 的编译期缓存**，cargo **按设计默认就把它放在项目树内** ——
**这一层从未被纳入隔离**。

### 膨胀机制（到源头，非症状）

| 累积源 | 机制 | 性质 |
|---|---|---|
| `target/debug/incremental/` | 每次重编译若 crate 指纹变化，cargo **新建**一份 ~460M 快照，**旧份从不自动回收** | **单调增长**（实测单次 24G / 166 份） |
| `target/{debug,release}/_up_/` | Tauri `bundle.resources` 打包时把资源**整包拷贝**（含 `vb_chromium` 425M + 3 个 profile） | 每次构建重建（各 720M） |
| `target/*/deps`、`build/` | cargo 中间产物 | 随依赖图变化累积 |

**实测证据**：当日清空 `target/` 后，另一会话**一次构建即重新生成 4.0G** —— 证明
这不是一次性问题，而是**结构性复发**：只要 target 在树内，"每次打包就涨"就必然发生。

---

## 2. 决策

**将 cargo 构建缓存显式重定向到源码树之外，并使源码树内"零构建产物"成为可机械判定的铁律。**

### D1 · 目标路径（显式、隔离）

```
design 环境 → C:\temp\dyautodm_build\design
test  环境 → C:\temp\dyautodm_build\test   （换分支时改一处即可）
```

与既有环境根同族（`C:\temp\dyautodm_*`）、树外、两环境物理隔离。

### D2 · 落地机制（双保险）

| 层 | 落点 | 作用 |
|---|---|---|
| 主 | `build_all.py::_env()` 显式设 `CARGO_TARGET_DIR` | 显式配置；覆盖全部正常构建路径 |
| 兜底 | `src-tauri/.cargo/config.toml` `[build] target-dir` | 防绕过脚本直接 `cargo build` / `npx tauri build` 时静默回落树内 |

### D3 · 单一真源（SSOT）

新增 `scripts/build_paths.py` 作为**唯一**产物路径解析入口，消除此前散落在
**5 个脚本**的硬编码（`deploy.py`、`package_installer.py`、`verify_build.py`）。
**路径字面量只写在 `.cargo/config.toml` 一处**。解析优先级：
`CARGO_TARGET_DIR` 环境变量 → `.cargo/config.toml` → 兜底树内（门禁报红）。

### D4 · 机械门禁（防复发）

`check_iron_rules.py` 新增 **R10**：源码树内存在 `src-tauri/target` 即 FAIL。
理由是声明式铁律 = 空架子（本项目已有先例：环境隔离铁律写明仍被违反）——
必须让它"会真拦"。

---

## 3. 备选方案与否决理由

| 方案 | 否决理由 |
|---|---|
| 每次构建后 `cargo clean` | 治症状不治本；且放弃增量编译加速，构建从 ~1.5min 退回全量 |
| 只在 `build_all.py` 设 env，不加 config | 绕过脚本直接构建即复发（且无门禁拦截） |
| 加 `.gitignore` 忽略 target | **无效**——target 早已被忽略（`DYAutoDM_v2/.gitignore:19`）；体积问题与是否入库无关 |
| 迁移 `src-tauri/binaries/`（sidecar 产物） | 收益低：该目录**有界不累积**（每次覆盖），且被 `tauri.conf.json` 的 `externalBin`/`resources` 引用，迁移面大 |

---

## 4. 上游兼容性取证（Open-Source Provenance Law）

- **上游风险**：[tauri-apps/tauri#13654] —— 在 `~/.cargo/config.toml` 设 `target-dir`
  会使 `PathResolver.resource_dir()` 返回 `unknown path` 并 panic。
- **是否适用本项目**：**否**（已读码确认）。Rust 侧仅 `lib.rs:153` / `sidecar.rs:48`
  用 `std::env::current_exe()`，**全仓零** `resource_dir()` / `PathResolver` /
  `BaseDirectory` 调用；Python 侧 `vbrowser.resource_root()` 以 `sys.executable` 为基准。
  ⇒ 两条资源定位链均**不依赖** cargo target 目录。
- **尖刺验证**：临时 cargo 项目 + `target-dir` 配置 ⇒ 项目内 `target` 未生成、
  产物落外部 ✅（机制确认，非推断）。

---

## 5. 验证判据（Live-Instance Verification）

| # | 判据 | 结果 |
|---|---|---|
| V1 | 完整构建后源码树 `src-tauri/target` **不存在** | 见 §6 |
| V2 | 产物落 `<外部目标>/debug/dyautodm-v2.exe` 且资源版本 == 期望版本 | 见 §6 |
| V3 | `deploy.py` 能正确定位该 exe（不破部署链） | 见 §6 |
| V4 | `package_installer.py` 能定位 wix/msi/nsis（不破打包链） | 见 §6 |
| V5 | 资源定位（`current_exe()` / `resource_root()`）在部署态不破 | 见 §6 |
| V6 | 门禁 R10 负控：源码树出现 target ⇒ 变红 | ✅ `--selftest` 已过 |
| V7 | 环境隔离：design 构建不写 test 缓存 | 见 §6 |

---

## 6. 验证结果（2026-09-29 实机，全过）

| # | 判据 | 结果 |
|---|---|---|
| V1 | 完整构建后源码树 `src-tauri/target` **不存在** | ✅ 全程未生成（构建 3m26s） |
| V2 | 产物落 `<外部目标>/debug/dyautodm-v2.exe`，资源版本 == 期望 | ✅ `C:\temp\dyautodm_build\design\debug\dyautodm-v2.exe` = **0.45.97** |
| V3 | `deploy.py` 能正确定位该 exe | ✅ `--dry-run` 校验 1/2/3 全过 |
| V4 | `package_installer.py` 路径解析（wix/msi/nsis） | ✅ 经 `build_paths` 正确解析 |
| V5 | `resource_root()` / `current_exe()` 部署态不破 | ✅ 基于 `sys.executable`，与 target 无关 |
| V6 | 门禁 R10 负控：源码树出现 target ⇒ 变红 | ✅ `--selftest` 注入样本精确报红 |
| V7 | 环境隔离（design 不写 test） | ✅ 仅 `design` 目录 |

构建日志原文佐证：
```
Finished `dev` profile [unoptimized + debuginfo] target(s) in 3m 26s
Built application at: C:\temp\dyautodm_build\design\debug\dyautodm-v2.exe
```

**成效**：源码树 `dyautodm` 稳态体积 **42G → 1.4G**。
**门禁读数**：`check_iron_rules.py` → R10 PASS、合计 14 项仅 1 警告（R3，非本次引入）。

---

## 7. 影响与回退

- **影响**：源码树稳态体积从 42G 降至 ~1.5G；首次构建需全量重编（一次性）。
- **回退**：删除 `src-tauri/.cargo/config.toml` + 还原 `_env()` ⇒ 立即回到默认行为（单文件，无数据风险）。

## 8. 遗留与边界

- 源码树内 3 个浏览器 profile（~300M，含登录态、被 `tauri.conf.json` 引用）**不在本次范围**，另立 **L-16**。
- 缓存上限管理：`build_all.py --prune-cache` 提供**显式**回收（默认关闭，契合【显式配置原则】）；
  未做自动按大小/时间回收（避免隐式行为）。
