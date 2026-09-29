# 根治方案：构建缓存迁出源码树（源码树零构建产物）

- **日期**：2026-09-29 20:12
- **分支**：design（`dyautodm_design` 环境）
- **触发**：源码树膨胀至 42G，主因 `src-tauri/target/`（40G）；当日清理后，并发会话一次构建即重新生成 4.0G ⇒ **非一次性问题，是结构性复发**。
- **状态**：⏸ **待用户拍板**（架构级改动：需升版 + ADR + 实机验证）

---

## 一、根因（RCA，到源头而非症状）

| # | 累积源 | 机制 | 为何"每次打包就涨" |
|---|---|---|---|
| 1 | `src-tauri/target/debug/incremental/`（实测 24G / 166 个目录） | cargo 增量编译快照 | 每次重编译若 crate 指纹变化，**新建**一份 ~460M 快照，**旧的从不自动回收** ⇒ 单调增长 |
| 2 | `src-tauri/target/{debug,release}/_up_/`（各 720M） | Tauri `bundle.resources` 打包时整包拷贝资源 | 每次构建重建；含 `vb_chromium`(425M) + 3 个 chromium profile |
| 3 | `src-tauri/target/{debug,release}/deps|build/`（~8G） | cargo 编译中间产物 | 随依赖图变化累积 |
| 4 | `src-tauri/target/release/bundle/{msi,nsis}`（327M） | 安装包产物 | 每次出包保留旧包 |

**共同根因（一句话）**：cargo 的**默认 `target-dir` 位于项目树内**（`src-tauri/target`）。
所谓"部署路径与源码路径隔离"只隔离了**运行期数据**（数据根 `C:\temp\dyautodm_design`），
**没有隔离编译期缓存** —— 而后者按设计就长在源码树里。隔离没失效，是**这一层从未被纳入隔离**。

---

## 二、设计原则（对应铁律）

1. **源码树零构建产物**（CI Hygiene）：`src-tauri/target` 不得存在于源码树。
2. **单一真源（SSOT）**：产物路径当前**散落在 5 个脚本**（`deploy.py:212-213`、`package_installer.py:145/231/235`、`verify_build.py:10`），各写各的 ⇒ 迁移必漏。必须收敛到一个解析器。
3. **显式配置（用户【显式配置原则】）**：目标目录由**显式配置**决定，不依赖"探测本机状态"。
4. **环境隔离**：design / test 两环境**各自独立**构建缓存，禁互串（沿用既有分支门禁哲学）。
5. **机械门禁（防复发）**：把"源码树不得有 target"变成**会真拦**的检查（`check_iron_rules.py`），
   否则下次绕过脚本直接 `npx tauri build` 就会复发。

---

## 三、关键技术验证（已完成，非推断）

### 3.1 尖刺：`target-dir` 重定向真的生效 ✅

```
spike：临时 cargo 项目 + .cargo/config.toml [build] target-dir=<外部路径>
结果：项目内 target/ 存在? False ✅
      外部 target/debug/spike.exe 落位? True ✅
```

### 3.2 上游风险排查（Open-Source Provenance Law）⚠️ 已澄清

- **上游 bug**：[tauri-apps/tauri#13654] —— 在 `~/.cargo/config.toml` 设 `target-dir` 会使
  `PathResolver.resource_dir()` 返回 `unknown path` 并 panic。
- **对本项目是否适用**：**否**（已读码确认）。
  - Rust 侧仅 `lib.rs:153` / `sidecar.rs:48` 用 `std::env::current_exe()`，**全仓无 `resource_dir()` / `PathResolver` / `BaseDirectory` 调用**。
  - Python 侧 `vbrowser.resource_root()`（`vbrowser.py:765`）以 `sys.executable` 为基准（PyInstaller 冻结态）。
  - ⇒ 两条资源定位链**均不依赖 cargo target 目录**，不受搬迁影响。
- **Tauri 版本**：`tauri 2.11.5` / `tauri-build 2.6.3`（较新，该 issue 主要在旧版）。

### 3.3 需实测确认的残余未知（诚实标注）

- `npx tauri build` 在重定向后**能否正确找到 target 产物**做 bundling（预期能，cargo 会自报路径；但**必须实机验证一次**）。
- `tauri dev`（`package.json:10`）在重定向下是否正常（dev 路径，非交付路径）。
- `npx tauri build --no-bundle`（本项目实际用法）无 bundling 步骤，风险更低。

---

## 四、方案设计

### D1 · 目标路径（显式、隔离、树外）

```
design 环境 → C:\temp\dyautodm_build\design\target
test  环境 → C:\temp\dyautodm_build\test\target
```

理由：与既有环境根（`C:\temp\dyautodm_design` / `dyautodm_test`）同族；树外；两环境物理隔离。

### D2 · 落地机制（双保险：显式环境变量 + 提交态配置）

| 层 | 落点 | 作用 |
|---|---|---|
| **主** | `build_all.py` 的 `_env()` 显式设 `CARGO_TARGET_DIR` | 显式配置；`build_all.py` 是唯一构建入口，覆盖全部正常路径 |
| **兜底** | `src-tauri/.cargo/config.toml` → `[build] target-dir = "C:/temp/dyautodm_build/design"` | 防有人绕过脚本直接 `npx tauri build` 时**回落树内**（路径用正斜杠，避免 TOML 反斜杠转义） |

> **为何两者都要**：只放 config ⇒ 与"显式配置"风格不符且 per-branch 需各写一份（易漂移）；
> 只放 env ⇒ 绕过脚本即复发。双保险 + 门禁兜底。

### D3 · SSOT：新增 `scripts/build_paths.py`

```python
def target_dir() -> Path:
    """解析构建产物根。优先级：CARGO_TARGET_DIR 环境变量 → .cargo/config.toml → 默认树内。"""
    env = os.environ.get("CARGO_TARGET_DIR")
    if env: return Path(env)
    cfg = ROOT / "src-tauri" / ".cargo" / "config.toml"   # 最小 TOML 解析（不引第三方依赖）
    ...  # 读 build.target-dir
    return ROOT / "src-tauri" / "target"   # 兜底（并在门禁中报红）

def main_exe(kind: str = "auto") -> Path: ...   # → <target>/debug|release/dyautodm-v2.exe
def bundle_msi() / bundle_nsis() / wix_dir() ...  # package_installer 用
```

**改造 5 处调用点**（全部改读 `build_paths`，删除硬编码）：
- `deploy.py:212-213`（主程序 exe 探测）
- `package_installer.py:145`（wix 源）/ `:231`（msi）/ `:235`（nsis）
- `verify_build.py:10`（exe 路径；**及其 ROOT 硬编码绝对路径**——顺带修 M-26 同类债）

### D4 · 机械门禁（防复发，SSOT 在 `check_iron_rules.py`）

新增 **R10**：**源码树内不得存在 `src-tauri/target`**（存在即 FAIL，附最后修改时间与体积）。
- 负控：手动 `cargo build`（不经重定向）复现 target ⇒ 门禁必须变红。
- 同时把 R3（源码 backend 无产物体）扩展到覆盖 `src-tauri/target`。

### D5 · 缓存回收（可选，防 `C:\temp` 无界增长）

`build_all.py` 增 `--prune-cache`：构建成功后执行 `cargo clean`（或仅删 `<target>/debug/incremental`）。
**默认关闭**（保留增量加速），由用户按需 `--prune-cache` 显式触发 —— 契合【显式配置原则】。

---

## 五、实施步骤（拍板后顺序执行）

1. `check_version_sync.py` 六处读回当前值 → `+0.01`（**升版前重读**，遵守版本串行化）
2. 新增 `scripts/build_paths.py`
3. 改 `build_all.py`（`_env()` 设 `CARGO_TARGET_DIR`；接 `--prune-cache`）
4. 新增 `src-tauri/.cargo/config.toml`
5. 改 5 处消费脚本 → 读 `build_paths`
6. 加 R10 门禁 + 负控
7. **实机验证**（见 §六）
8. 清理旧树内 `src-tauri/target`（4.0G）
9. 写 ADR（`docs/adr/ADR-0xx-build-cache-outside-source-tree.md`）
10. 归档案例（`工作记忆/cases/2026-09-29_源码树膨胀_root-cause与构建缓存隔离_v<ver>.md`）
11. 提交（Conventional Commits + Design-Intent Regression 模板）

---

## 六、验证判据（Live-Instance Verification Law）

| # | 判据 | 方法 |
|---|---|---|
| V1 | 源码树零构建产物 | 完整 `build_all.py` 跑完后，`src-tauri/target` **不存在**；`du -sh DYchajian` 不再增长 |
| V2 | 产物落位正确 | `<外部目标>/debug/dyautodm-v2.exe` 存在且**资源版本 == 期望版本** |
| V3 | 部署链不破 | `deploy.py` 能正确定位该 exe 并部署；md5 三方一致 |
| V4 | 打包链不破 | `package_installer.py` 能定位 `wix/msi/nsis`（或 `--no-bundle` 路径不受影响） |
| V5 | 资源定位不破 | Rust `current_exe()` / Python `resource_root()` 在**部署态**仍能定位 profile 与 sidecar |
| V6 | 门禁真会拦（负控） | 不经重定向直接 `cargo build` ⇒ R10 **变红**；撤除后复绿 |
| V7 | 环境隔离 | design 构建不写 test 缓存；两环境目标目录互不可见 |
| V8 | 零回归 | 项目全量测试集与基线逐条一致（失败集一字不差） |

**止损律**：若 V4 证明 `tauri build`（带 bundling）无法在重定向下工作，则**缩小范围**到
`--no-bundle` 路径（本项目实际用法）先行落地，**不叠补丁硬修 bundling**。

---

## 七、风险与回退

| 风险 | 等级 | 缓解 |
|---|---|---|
| `tauri build`（bundling）不兼容重定向 | 中 | §3.2 已排除主要上游障碍；若 V4 失败按止损律缩小范围 |
| 路径迁移遗漏调用点 ⇒ 部署取到旧产物 | 中 | D3 SSOT 收敛 + V3/V4 判据 + 门禁 |
| 首次构建全量重编（~1.5min Rust） | 低 | 一次性成本，文档说明 |
| `.cargo/config.toml` 含机器绝对路径，跨机不可移植 | 低 | 与既有 `PY314`/`CARGO_BIN` 硬编码同类（已存在）；如需可改相对路径 |
| design/test 两分支该文件内容不同 | 低 | 与既有 `deploy.py` 分支门禁同类模式；各分支各提交一份 |

**回退**：删除 `.cargo/config.toml` + 还原 `_env()` ⇒ 立即回到默认树内 target（单文件回退，无数据风险）。

---

## 八、不做什么（边界）

- ❌ 不动 `src-tauri/binaries/`（PyInstaller sidecar 产物，~600M，**有界不累积**；且被 `tauri.conf.json`
  的 `externalBin`/`resources` 引用，迁移面大、收益低）。
- ❌ 不动源码树内 3 个浏览器 profile（属 **L-16**，另案，含登录态）。
- ❌ 不引入第三方依赖/工具。
- ❌ 不改 `[profile.release]` 优化配置（与本次无关）。
