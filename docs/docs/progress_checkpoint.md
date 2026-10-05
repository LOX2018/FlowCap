# 进度暂存点 —— design/better-douyin 分支

> **暂存时间**：2026-09-14 14:30
> **分支**：`design/better-douyin`
> **版本**：v0.43.14
> **用途**：前端重设计前的检查点，便于回退与对照。

---

## 一、本分支已完成（4 个提交）

| 提交 | 内容 | 版本 |
|---|---|---|
| `cd858da` | better-douyin 架构逆向情报（3 份 docs） | 0.43.12 |
| `b297d11` | **MCP 服务层落地** + sidecar 依赖共享 | 0.43.13 |
| `f58976f` | **两层指纹单源化**（消除 HTTP150/内核148 矛盾） | 0.43.14 |
| `71fe30f` | 知识库落档（单源化最终形态 + 内核开关实测） | — |

### 1. 功能：MCP 服务层（`backend/mcp/`）

对标 better-douyin `mcp.rs` + `src/bin/douyin-dl.rs`。

```
backend/mcp/__init__.py   模块说明（设计来源/差异/分层）
backend/mcp/config.py     配置 + 令牌世代号（轮换即失效）
backend/mcp/registry.py   工具注册表 + READ/WRITE 分级 + 一次性确认票据
backend/mcp/audit.py      脱敏审计（只记工具名/字段长度/耗时/错误码）
backend/mcp/tools.py      工具集（只读优先，全本地库，零平台请求）
backend/mcp/server.py     本机 HTTP（仅 127.0.0.1 + Bearer）
backend/mcp/__main__.py   stdio 入口（对齐蓝本单入口设计）
backend/api/mcp.py        管理面路由（挂 /api/mcp）
```

**实测**：`verify_mcp.py` **26 项全绿**（含真进程 stdio 端到端）。

### 2. 性能：sidecar 依赖共享

三份 `_internal` 实测内容完全相同（6514 文件 / 246.3MB，交集=并集，独有 0）
→ 收敛为一份共享目录 + 三 exe 平铺。

| | 前 | 后 |
|---|---|---|
| 部署体积 | 846 MB | **318 MB** |

**实测**：布局 E2E 解析命中 3/3、真启动 3/3 PASS；打包时自动执行（省 492.6MB）。

### 3. 指纹：两层单源化

`backend/utils/fingerprint.py` 成为唯一真源：

```
fingerprint_profile(account)   真源（按账号派生，版本读内核真实版本）
   ├── launch_args(account)    → 内核开关（6 项实测生效）
   └── viewport_for(account)   → Playwright viewport（决定 screen）
kernel_version()               读内核 exe 文件版本，**内核升版自动跟随**
get_profile(account)           兼容入口，委托真源
```

`backend/vbrowser.py` 接线：`_launch_args_with_proxy` 并入档案开关；
三处 `launch_persistent_context` 注入 viewport。

**实测三轮全绿**：11/11、10/10、入口 6 项。

---

## 二、独立部署目录

```
C:\temp\flowcap_design\
├── _internal/                   本分支构建产物（共享依赖）
├── flowcap-backend-*.exe       本分支构建产物（0.43.14）
├── flowcap-browser-daemon-*.exe
├── flowcap-recv-daemon-*.exe
├── vb_chromium → junction       零拷贝复用（省 426MB）
├── accounts/                    **留空**（不共用主分支凭证）
├── data/  logs/
```

**实测**：`/api/version` → `{"backend": "0.43.14", "frozen": true}` ✅

⚠️ **本目录无主程序 exe**（只放了 3 个 sidecar），跑完整桌面端需 `npx tauri build --no-bundle`。

---

## 三、主分支环境状态（**未动**）

`C:\temp\flowcap_test` 保持旧版：
- 主程序 `FlowCap_0.43.11.exe`
- 三个独立 sidecar 目录（各带 `_internal`，合计 851MB，旧形态）

---

## 四、未处理项

| # | 项 | 说明 |
|---|---|---|
| 1 | 主分支部署目录转共享形态 | 可省约 528MB，需用户确认（那是主分支环境） |
| 2 | `flowcap_design` 补主程序 exe | 需 `npx tauri build --no-bundle` |
| 3 | Temp 清理 | `%LOCALAPPDATA%\Temp\bdy/`、`bdy.zip`、`bdy-src/`、`D:\bdy_reverse\` |
| 4 | 真机验证指纹隔离 | 需在 `flowcap_design` 扫码登录账号 |
| 5 | D 方案（189 处接线） | **已决定不做**（边际收益递减 / 回归风险高） |

---

## 五、用户未提交改动（**本分支一路保留，从未触碰**）

```
 M FlowCap/backend/daemon/browser_daemon.py
 M FlowCap/scripts/verify_nickname_link.py
?? FlowCap/docs/upstream_baseline.json
```

---

## 六、工作区洁净度

- 运行进程：**0**
- 版本四处同步：package.json / frontend/package.json / tauri.conf.json / Cargo.toml = **0.43.14**
- 知识库：`工作记忆/02_指纹浏览器与凭证.md` 已回写 + **Obsidian 已同步**（哈希一致，旧版备份 `.bak.20260914_141134`）

---

*下一个动作：前端重设计（对标 better-douyin 的布局/设计令牌/组件体系）。*
