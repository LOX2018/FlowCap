# OCR 增量审查 · 分诊账目（v0.43.71 → v0.43.88）

> 引擎：open-code-review **v1.12.5 (189be5b02)** | 报告：`artifacts/audit/ocr_report_v0.43.71-88.json`（109,885 B）
> 范围：`git diff 32852b3..HEAD`（27 提交）→ 排除文档后 **50 文件 / 77 条 finding**
> 会话：`9b35c4fb-0b59-4684-9c3b-5d9ad7117902` | 模型：**deepseek-v4.1-flash**（与 Hermes 顶层一致）
> 参数：`--concurrency 4 --timeout 60` | 耗时 **10m18s** | done 50 / failed 0 | 工具调用 684 次、失败 **0**

---

## 0. 为什么必须重审（上一轮为何作废）

| 项 | 上一轮 `24d54965`（06:18） | 本轮 `9b35c4fb` |
|---|---|---|
| 参数 | 默认（`--timeout 15`、并发 8） | `--concurrency 4 --timeout 60` |
| 文件组 | **50 done / 37 failed**（`context deadline exceeded` 300001ms） | **50 done / 0 failed** |
| 工具调用 | `git grep`/`file_read` 大量 `exit status 0xc0000142`（Win DLL 初始化失败） | **684 次，失败 0** |
| 模型 | `glm-5.3-flash`（与 Hermes 顶层脱钩） | `deepseek-v4.1-flash`（Step 0 已对齐） |
| 报告 | **0 字节** | 109,885 字节 / 77 条 |

**根因**：大文件组在 15min 硬超时被整组掐死；超时后子进程池被拖垮 → 连带 DLL 初始化失败。
**放大超时 + 降并发** 后两项全消。⇒ 默认参数对本仓（单文件 2000+ 行）不适用。

---

## 1. 统计画像（按完整 path，禁 basename）

severity：**critical 1 / high 14 / medium 29 / low 33**
category：bug 41 / maintainability 18 / style 6 / test 5 / performance 4 / security 3

---

## 2. CRITICAL + HIGH —— 逐条实测判定（真 13 / 误报 1 / 半真 1）

### ✅ 真缺陷（需修，按优先级）

| # | 路径:行 | 缺陷 | 实测证据 |
|---|---|---|---|
| **A1** | `services/cenc_video.py:274,331` | `RESOLVE_URLS_JS` 对象解构 vs 调用方传数组 → **取址恒失败** | **Node 实跑**：数组→`TypeError: …reading 'map'`；对象→正常。**同坑**：`nickname_fallback.FETCH_JS:44,207`、`merged_forward.WEB_FETCH_JS:63`+`api/messages.py:1348-1349` **三处全部**如此 |
| **A2** | `services/nickname_fallback.py:213` | 失败路径不 `_mark_run` → 限流/日额可被死循环绕过 | `208-216` 两 return 均无 `_mark_run`；仅 `218`（200 后）调用 |
| **A3** | `daemon/browser_daemon.py:1740-1744` | 环境基线未按 `ok` 门控 | `env_baseline.py:10-11` 契约原文「**只在** ok=True 时记录…**绝不**在凭证存疑时写入」 |
| **A4** | `api/messages.py:612-619` + `im_video.py:297` | `ok=True, path=""` → 响应既无 `url` 也报成功（假成功） | `615-617` 仅当 `path` 非空才补 `url` → 前端拿到 `{ok:true}` 无地址 |
| **A5** | `frontend/…/message-bubble.tsx:247` | `shareM` 正则含「视频」且早退 → **`m.type==="video"` 点播分支不可达** | shareM 在 `247-271` 早退；video 分支在 `354`；后端视频文本 = `[分享视频] 视频ID x` → 必走分享卡 |
| **A6** | `frontend/…/messages-page.tsx:267` + `message-shared.tsx:93` | `RawMessage` 未声明/未透传 `video` → 封面、时长恒空 | 后端 `api/messages.py:499` 确实下发 `"video"`；`message-bubble.tsx:363-364` 读 `m.video?.poster/duration` |
| **A7** | `frontend/…/NicknameFallbackSection.tsx:28-32` | `gate` 值与后端契约不符 → 闸门原因被吞 | 后端实际产出 `daily-cap`(`94`)/`cooldown(Ns)`(`97`)/`ok`；前端只认 `interval`/`daily-limit`（**永不出现**），`\|\| enabled` 覆盖 |
| **A8** | `frontend/…/NicknameFallbackSection.tsx:66-69` | 硬编码 `limit:10` 覆盖用户配置 | 后端 `187-188` `if limit is not None: cf["max_per_run"]=…` |
| **A9** | `frontend/…/KbImportSection.tsx:169` | 切会话不清 `pairs` → **预览 A 却入库 B** | `157` 账号 select 清了，`169` 会话 select 只 `setConvId`；`doImport:99` 用当前 `convId` |
| **A10** | `frontend/…/messages-page.tsx:392-394` | 切会话不重置 `selAnchor/selEnd` → 旧 seq 泄漏到新会话导出 | 全仓 `setSelAnchor` 仅 `1047`/`655`/`1262`，`openConv` 只有 `setActive` |
| **A11** | `frontend/…/messages-page.tsx:307-315` | 跨会话跳转在新会话未见消息时被消费（`convMsgs.length` 初值 0） | `289-315` 依赖 `[jumpTo, convMsgs.length]`，两条 `setJumpTo(null)` |
| **A12** | `services/merged_forward.py:356-358` | 三元与 `or` 优先级 → 非 dict sender 丢 `nick_name` | **Python 实跑**：`{"nick_name":"妮名","sender":"u1"}` → 返回 `'未知'`（应 `'妮名'`） |
| **A13** | `test_upstream_p4.py:676` | `TestConversationSeq` 在 `__main__` **之后** → 直跑静默不执行 | `676-677` guard、`680` 类定义 |
| **A14** | `backend/database.py:248-250` | `AND` 优先于 `OR` → 回填条件语义错 | **sqlite3 实跑**：主路径（`DEFAULT 1`）不受影响，但**显式插 NULL 的路径**会把单聊 `0:1:11:22` 误判成群聊 |
| **A15** | `test_upstream_p3.py:432` · `p4:491` | 断言同义反复 / 不测生产代码 | p3 `assertGreaterEqual(...,0)` 恒真；p4 内联复刻判据自证 |
| **A16** | `test_upstream_p4.py:676` 同区 `tearDown` | `del sys.modules["database"]` 污染同进程其它用例 | `692-694` 无条件删除；`unittest discover` 共进程 |

### ❌ 误报（实测证伪，**不改**）

| 路径:行 | OCR 主张 | 实测结论 |
|---|---|---|
| `services/chatlab_export.py:83` | 三元两臂相同 → 死代码 | 两臂**确实**都返 `_TYPE_SHARE`，但**语义正确**：分享视频本就是 share 类型（`_TYPE_SHARE`），仅代码冗余 → 归为 style |
| `services/app_config_schema.py:76` | 缺 `min/max` → 可写 `interval=0` 绕过风控 | **误报**：`nickname_fallback._cfg():75` `max(60, int(...))` 已在**读取侧 clamp**，风控不可绕过 |
| `services/env_audit.py:87` | `nativeChecks` 死占位 | 实为 `nativeChecks.getParameter` **对象**（OCR 截断成前缀），非死代码 |
| `services/chat_render_png.py:38` | `MAX_SPAN` 导入未用 | 实为**文档引用**（第 24 行 docstring 明写），OCR 未区分注释与代码 |
| `components/kb/KbImportSection.tsx:212` | 勾选装饰性 → 数据完整性风险 | **半真**：`204-207` UI 已明写「入库按整会话执行，勾选用于核对内容」→ 契约已声明，非静默；建议**只读化**复选框 |

---

## 3. MEDIUM 高危项（已甄别）

| 路径:行 | 判定 |
|---|---|
| `services/chatlab_export.py:242` | ⚠️ **真**：`dest_dir` 取自请求体 (`ChatlabExportReq`)，`Path(dest_dir).mkdir(parents=True)` 无 containment 检查（读取侧 `_safe_export_file` 有）。**但**：本机桌面应用 + 会员门禁，风险面＝已认证用户自写本机目录 |
| `services/db_transfer.py:272-280` | ⚠️ **真**：`include_secrets=True` 明文凭证转储 + `unlink` 失败被 `except: pass` 吞 → 残留泄漏。**须**：改为内存传递或用后零写 |
| `services/im_video.py:46,94-109` | ⚠️ **真**：`_HTTP_CACHE` 模块级 dict，**只判 TTL 不淘汰**（`_evict_if_needed` 只清磁盘 `d`），`_MAX_VIDEO_BYTES=200MB` → 长跑无界驻留 |
| `services/chat_render.py:146` | ⚠️ **真**：`by_date` 调 `fetch_range(...)` 未传 seq 界 → 受默认 `limit=MAX_SPAN(2000)` 截断 → **>2000 条的会话按日期渲染缺数据** |
| `api/messages.py:1358-1359` | ⚠️ **真**：`_download(u, max_bytes)` 忽略 `max_bytes`，硬传 `60`(超时) 给 `_http_get` → 流式限长契约未生效 |
| `services/nickname_fallback.py:101` | ⚠️ **真**：限流三全局无锁 + `asyncio.to_thread` 并发 → check-then-act 竞态 |
| `services/cenc_video.py:107` | ⚠️ **真**：`[default_size]*sample_count` 无上限（`sample_count` 取文件值，可 ~4.29e9）→ 恶意 stsz 可 OOM |

## 4. LOW：dead code 机械核实（脚本统计外部引用数）

**✅ 确证为死代码**：`cenc_video._CONTAINERS` · `tls_policy.client_ssl_context`（无调用方，连带 `ssl`/`lru_cache`）· `authed-media.clearAuthedMediaCache`/`authedMediaStats` · `env_audit.exp_os/exp_brand/exp_ver` · `merged_forward.render_text` 的 `text=""` · `db_transfer.get_db` 导入 · `cenc_video._iv_size_for` 的 `senc_content` 形参
**❌ 误报**：`MAX_SPAN`（文档引用）· `nativeChecks`（对象非前缀）

---

## 5. 建议处置顺序（待授权）

1. **P0 能力不可用/风控面**：A1（**3 处同坑同修**）· A2 · A3
2. **P1 功能不可达**：A5（视频点播）· A6（封面时长）
3. **P2 数据正确性**：A9 · A10 · A11 · A14 · A4
4. **P3 测试失效**：A13 · A15 · A16
5. **P4 清理**：dead code 11 处 + style

> 每条修复：① 复现「修复前」② 修复后实测 ③ 版本 +0.01（四处同步）④ 归档 `工作记忆/cases/`
