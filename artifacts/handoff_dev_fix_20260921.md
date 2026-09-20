# 交接卡 · DYAutoDM_v2 dev.ps1 两缺陷修复（2026-09-21）

## 1. 已完成（逐条带证据）

- **缺陷 A：关闭不干净 → 二次启动卡死** —— 已修复并实机验证
  - 改动：`C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\dev.ps1`（md5 `6787e202995e3a79d3150875c2f78595`）
  - 新增 `Stop-DyAll` 函数（供 `-Stop` 与启动 `finally` 共用）
  - 实机结果：`[OK] 3 个守护已 /quit 优雅退出` / `[OK] 已清理 3 个残留进程` / `[OK] 端口已释放`
  - 残留核对：进程 **0**（含 camoufox）、8000/1420/11231 **全空闲**
  - 根因（已取证）：dev 模式下 Rust `#[cfg(not(debug_assertions))]` 跳过 start_backend ⇒
    Rust 台账不知道后端存在；窗口关闭时 `taskkill /F` 强杀后端 ⇒ Python `atexit`/`lifespan`
    清理被跳过 ⇒ 后端自 spawn 的 BCC/recv（`daemon_launcher._spawn_sidecar`）成孤儿。

- **缺陷 B：日志中文乱码** —— 根因已确证、修法已验证，**产物待重建**
  - 最小复现（`C:\Users\LOX\AppData\Local\Temp\enc_repro\`）证据：
    | 场景 | sys.stderr.encoding | 输出字节 |
    |---|---|---|
    | 源码态 | utf-8 | UTF-8 |
    | PyInstaller 冻结 | **gbk** | **GBK ✗** |
    | 冻结 + PYTHONUTF8=1 | 仍 gbk | **仍 GBK（变量无效）** |
    | 冻结 + reconfigure(utf-8) | — | **UTF-8 ✓** |
  - 结论：PyInstaller bootloader 把 stdio 钉死为区域编码，环境变量在冻结态无效。
  - 已加代码修复：`backend/main.py` 顶部（loguru sink 之前）对 stdout/stderr
    `reconfigure(encoding="utf-8", errors="replace")`；最小复现已验证产出 UTF-8。
  - **副产品**：`run_*.log` 文件一直是干净的（loguru 文件 sink 自带 `encoding="utf-8"`），
    乱码只在**控制台/dev 转发**这一段 —— 这是定位的关键分化证据。

## 2. 未完成 / 待续（含卡点）

- **重打 backend sidecar** —— 卡在：**另一会话（v0.44.15）有未提交 WIP**
  （`utils/ab_pure.py`、`utils/dy_util.py`、`dy_apis/client_live.py`、`builder/params.py`），
  重建会把其未入库代码打进产物 → 违反可追溯铁律。
  - 下一步：等其提交（或用户裁决）后在干净工作区执行
    `python scripts/build_all.py`（或只打 backend），随即部署 + 实机验证乱码消失。
- **dev.ps1 的编码三件套可回退** —— 已证明对冻结产物无效（无用但无害），
  保留原因是源码态（`npm run backend:dev`）仍受益。

## 3. 新会话开场白（直接粘贴）

```
继续 DYAutoDM_v2 的 dev 启动器修复（dev.ps1 关闭不干净 + 日志中文乱码）。

【目标】关窗口后自动收干净全部 dev 进程（防二次启动卡死）；dev/控制台日志中文不乱码。
【已完成】① dev.ps1 新增 Stop-DyAll（-Stop 与 finally 共用）——实机验证端口全释放、
          残留 0；② 乱码根因确证=PyInstaller 冻结态 stdio 被钉死为 GBK（PYTHONUTF8 无效），
          已在 backend/main.py 加 reconfigure(utf-8)，最小复现验证有效。
【待办】重打 backend sidecar（python scripts/build_all.py）→ 部署 → 实机验证 dev 控制台
        无 U+FFFD 替换字符（判据：转发日志里 '\xef\xbf\xbd' 计数为 0）。
【约束】① 源码目录不得产生 data/accounts（dev.ps1 已有污染门禁）；
        ② 版本五处同步（package.json/frontend/package.json/tauri.conf.json/Cargo.toml/_build_version.py）；
        ③ 绝不强杀浏览器（BCC/recv 走 POST /quit）；④ .ps1 必须 UTF-8 BOM + CRLF。
【环境】源码树 C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2；数据根 C:\temp\dyautodm_design；
        解释器 C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe。
【先做】确认工作区无并发写者的未提交改动（git status），再重打 backend sidecar。
```

## 附：并发写者快照（取证留档）

- 快照时间：2026-09-21（本会话）
- 写者状态：20s 连拍 md5 **完全一致** ⇒ 已停手（非活跃）
- 我的提交均在其祖先链上：`81b5cfa` / `59341b8` / `7500edd` / `dc4c12a` ✓
- 其未提交改动（我不覆盖）：
  `DYAutoDM_v2/backend/utils/ab_pure.py`、`backend/utils/dy_util.py`、
  `backend/dy_apis/client_live.py`、`backend/builder/params.py`、
  `backend/_build_version.py`、`package.json`、`frontend/package.json`、
  `src-tauri/{Cargo.toml,Cargo.lock,tauri.conf.json}`
