# 案例归档：移除 vb_chromium，Camoufox 成为唯一内核（v0.44.12）

**日期**：2026-09-20
**版本**：v0.44.12（design/better-douyin）
**触发**：用户报「又唤醒了 VB 指纹浏览器，把他给我卸载了！」→ 拍板「彻底删除 VB 指纹，将原 VB 的业务都转移到 camoufox 上」
**性质**：内核收敛（配置层早已完成，本次补齐**校验层**）+ 环境残留清理

---

## 一、表面现象 vs 真因（关键：现象指向的不是根因）

**现象**：我在**测试环境** `C:\temp\dyautodm_test` 跑 `dev.ps1` 后，弹出的是
`ungoogled-chromium`（VB）窗口，而配置早已是 `DY_BROWSER_KERNEL = "camoufox"`。

**真因（决定性证据，非推断）**：测试环境里**残留 09-14 的 onedir sidecar 目录**：

```
C:\temp\dyautodm_test\
  dyautodm-browser-daemon-x86_64-pc-windows-msvc\   ← onedir，09-14 12:09（0.43.x，早于 Camoufox）
  dyautodm-backend-x86_64-pc-windows-msvc\          ← 09-14 12:06
  dyautodm-recv-daemon-x86_64-pc-windows-msvc\      ← 09-14 12:11
```

`_resolve_sidecar_binary` 的搜索顺序是 **① `<root>/<name>-<triple>/`（onedir 目录）优先**
→ 于是从测试环境启动时，拉起的是这份**旧二进制**。

**判据（直接读产物归档，不 grep exe）**——`list_archive_modules.py` + PYZ 字符串常量扫描：

| 判据 | 测试环境那份（09-14） | 当前构建（15:09） |
|---|---|---|
| 模块数 | **3221** | 3619 |
| `vbrowser_camoufox` 模块 | **无** | 有 |
| `vbrowser` 含 `BCC-058`（Camoufox 分派） | **无** | 有 |
| `config` 含 `DY_BROWSER_KERNEL` | **无** | 有（反序列化常量 = `'camoufox'`） |

⇒ 旧产物根本不含 Camoufox，只认 `vb_chromium` ⇒ 唤醒 VB。
**这正是 `dyautodm-development` 里已记录的坑**：「sidecar 子目录会**优先**于平铺 exe 被加载
——旧子目录遮蔽新产物，门禁全过但跑旧版」。

---

## 二、真正的缺口：配置层已切，**校验层没切**

迁移其实早已完成（v0.44.2 起）：

| 证据 | 内容 |
|---|---|
| `auto_dm/config.py:60` | `DY_BROWSER_KERNEL = "camoufox"`（**配置真源**，刻意不用环境变量，因 GUI 启动不继承 shell env） |
| `vbrowser.py:872-885` | 分派 Camoufox；失败抛 `BCC-058`，**禁止静默回退 Chromium** |
| 设计环境日志 14:27→16:02 | `[vbrowser] 内核=Camoufox（Firefox，C++ 层指纹注入，无 JS 注入）` ✓ |
| `browser_daemon` | Camoufox 模式**跳过 JS 注入**、窗口检测按内核分派、生命周期适配 |
| `profile/<acc>/_camoufox/` | 活跃写入至 16:02 ✓ |

**但 `should_use_vb()` 仍是 Chromium 语义**：`VB_MODE="exe"` 时**强制要求
`vb_chromium/.../chrome.exe` 存在**，否则 `raise RuntimeError`。
而 **8 条启动路径**都先调它（`browser_daemon._launch` / `login_api×5` /
`link_resolve` / `web_probe`）⇒ **直接删掉 vb_chromium 会让 BCC/扫码/探测全部起不来**。

> ⇒ 「删除内核」不是文件操作，是**架构改动**：必须把「内核可用」这一不变式
> 从 launch 层补齐到 **校验层**。

---

## 三、改动清单（v0.44.12）

| # | 文件 | 改动 |
|---|---|---|
| 1 | `backend/vbrowser.py` | `should_use_vb()` 新增 **Camoufox 分支**：`camoufox_enabled(cfg)` 为真时校验 `camoufox.pkgman.installed_verstr()`，返回 `(True, "camoufox")`；不可用抛 **BCC-070**；Chromium 分支保留为历史回退 |
| 2 | `backend/utils/fingerprint.py` | `_chrome_exe_path()` 优先解析 Camoufox 真实启动路径（`camoufox.pkgman.launch_path()`）→ `kernel_version()` 自动跟随内核（15 个**真实 Firefox 版本**），杜绝「HTTP 说 X / 浏览器说 Y」 |
| 3 | `src-tauri/tauri.conf.json` | 打包资源移除 `../vb_chromium` |
| 4 | `backend/errcode_data.py` | 新增 **BCC-070**（ERRCODES + CODE_DESIGN 六段契约），字节级插入（该文件混合行尾，patch 会整文件归一） |
| 5 | `scripts/build_sidecar.py` | 补 `--hidden-import camoufox.pkgman`（函数体内导入，静态分析扫不到） |
| 6 | 五处版本 | `0.44.11 → 0.44.12`（`package.json` / `frontend/package.json` / `tauri.conf.json` / `Cargo.toml` / `_build_version.py`） |
| 7 | 删除 | 三份 vb_chromium：源码树母本 425M + 测试环境副本 426M + 设计环境 junction |

---

## 四、验收证据

### 4.1 源码态（`scripts/diag/verify_camoufox_only.py`）→ **ALL_PASS 5/5**

| 判据 | 结果 |
|---|---|
| V1 `should_use_vb(cfg)` | `(True, 'camoufox')` |
| **V2 把 vb_chromium 目录隐藏后仍通过** | PASS（证明校验**不再依赖** Chromium 内核） |
| V3 `fingerprint._chrome_exe_path()` / `kernel_version()` | Camoufox 真实路径 / `152.0.4-beta.30` |
| V4 `errcode.lookup("BCC-070")` | design/contract/deviation/chain/root/verify 六段齐全 |
| **V5 对照：切回 chromium 且内核不存在 → 必须抛错** | PASS（证明**校验没被架空**，不是删了了事） |

### 4.2 环境清理

- 测试环境删除 **3×282M 旧 sidecar 目录 + 09-14 旧主程序**（这是「VB 复活」的元凶）
- 保留项核对：`accounts/ data/ members/ logs/ scripts/` **全部仍在** ✓
- 取证留档：`artifacts/vb_chromium_removal_evidence.md`（含母本 chrome.exe md5）

### 4.3 实机（部署后）

- 启动日志应为 `[vbrowser] 内核=Camoufox`（**且 vb_chromium 已不存在**）
- `curl :8000/api/version` → `0.44.12`

---

## 五、顺带记录：两个易复发的坑

1. **「测试环境=部署环境」不适用免打包 dev**：`C:\temp\dyautodm_test` 只有 exe/sidecar、
   无源码树，`tauri dev` 无法在那里运行。放进那里的 `dev.ps1` 只能是**转发器**
   （调用源码树脚本 + 把 `DY_APP_ROOT` 指向测试目录）。
2. **同一份 `patch` 在不同行尾状态下的行为不同**：对**纯 LF**（git blob）文件检出成
   CRLF 的情形，`patch` 整文件重写是安全的（git autocrlf 归一化后 diff 干净）；
   对**混合行尾**文件（如 `errcode_data.py`）则必须**字节级插入**。
   **判据永远是**：`git diff --numstat` vs `git diff --ignore-all-space --numstat`
   ——两者接近才说明没有 EOL 污染。

---

## 六、遗留

1. **`四川工伤张老师` 无 `_camoufox` profile**（从未在 Camoufox 下启动）⇒ 该账号要用
   Camoufox 时需**重新扫码登录一次**（`尚进工伤小助理` 已有 `_camoufox`，不受影响）。
2. `vbrowser.py` 的 Chromium 分支代码**保留**（历史回退，不再有内核文件）；
   若确认长期不用，可后续单独立项清理。
3. Camoufox 下的 WP 私信通道仍走 Playwright 原生 WebSocket 事件（JS 注入已禁用），
   与本次改动无关。
