# 案例归档 · H-25（二）媒体 `text`/`extra` 契约收敛（v0.45.2 → v0.45.3）

> 归档日期：2026-09-25　关联：ADR-011　提交：`a10ac1f`　来源：H-25 张老师库脏数据治理的「串码」残留（治标→治本）

---

## 1. 调查链（Investigation Chain）

### 1.1 设计意图（Design Intent）
- 模块：`auto_dm/conversation_capture.py` 的 `_extract_media_text`（图片落库收口点）。
- 设计契约：`dm_messages.text` 承载**展示语义标签**；二进制载荷（缩略图）走 `extra`。
- 期望行为：AI 读侧 `_build_history` 注入的 `text` 应**不含 base64 / 不含图片 URL**。

### 1.2 当前状态（Observed Deviation）
- 实测基线（生产库 `members/m17db0f8209156f26`，账号「四川工伤张老师」）：
  - `text LIKE '[图片] data:image%'` = **25 条 / 102,913 字符**（单条最长 6577）；
  - `text LIKE '[图片] http%'` = **12 条**（两行形态：`[图片] <thumb>\n[原图] <origin>`）。
- 传播：`_build_history` 仅对 `msg_type=='27'` 走图片描述分支 ⇒ text 型 base64 **原样注入** AI prompt（实测 ≈34k tokens / 全库）；ChatLab 导出 `content` 字段直接写 `text` 原文 ⇒ 噪音。

### 1.3 执行链追踪（Call-Graph Unwind）
```
conversation_capture._extract_media_text  (唯一生产者，写侧)
   └─ inline_pic base64 内联进返回串 → dm_messages.text
bcc_routes._recv_init / recv_daemon._extract (t==27, WS 实时)
   └─ 同样内联 base64 / URL
ai_reply._build_history
   └─ text 原样拼入 history（msg_type!='27' 不走图片分支）
api/messages._conversation_detail / ChatLab 导出
   └─ text 原样下发 / 写出
```

### 1.4 根因分析（RCA）
- **直接原因**：`_extract_media_text` 把缩略图 base64 **内联进 `text`**（展示语义字段承载二进制载荷）→ 违反 SoC / Canonical Contract Law。
- **深层原因**：2026-08-31 决策（图均 2.9KB、零请求、库容 +13%）把「图进 text」当**前端渲染契约**固化，但此后消费方从「前端」扩展为「AI prompt / 导出」三个需求相反的消费者，字段职责冲突却未被重新收敛。
- **迭代止损判定**：H-25（一）只做了「AI 读侧剥离 + init 路径修复」= 治标；写侧仍内联 base64 ⇒ 本批按架构级收口（方案 C）。

---

## 2. 关键实测取证（Evidence）

| # | 证据 | 结论 |
|---|---|---|
| E1 | 25 行 base64 行、12 行 URL 行，`extra` 全部带 `skey`+`origin_url` | 解密能力从未丢失，`text` 内字节**非唯一数据源** ⇒ 删除有补偿 |
| E2 | HTTP 直抓 `origin_url` 返回加密体（magic `9f3d410b`，27~63KB） | `[图片] <url>` 形态**既污染 prompt 又无展示价值** |
| E3 | `origin_image_resolver._ttl_seconds` 默认 30 天会 `unlink` | 删 base64 **有真丢图风险** ⇒ 删前须补偿 |
| E4 | `/conversation` 已返回 `image_url`（`extra` 解密派生）；前端已有 `image_url` 优先渲染分支 | 补偿路径**已存在**，无需新建取图链路 |
| E5 | 全项目构造 `data:image/webp;base64,` 的唯一生产者 = `_extract_media_text:291` | 收口点是**单点、干净** |

---

## 3. 决策（ADR-011）

**方案 C（采纳）**：base64 就地搬到 `extra.thumb`，`text` 纯化。
- 零网络 / 零重编码 / 完全无损；AI prompt + 导出立刻干净；前端改动最小。
- **为什么接受「库容不变」**：2026-08-31 的 +13% 是当时权衡并接受的决策（代码注释含实测依据）；本 ADR 解决**职责混淆**，非库容。彻底回收属方案 B，单独立项（H-27）。

---

## 4. 实施清单（已交付）

1. **写侧收口** `conversation_capture.py`：`_thumb_semantic_label()` / `_extract_thumb_data_uri()` / `_extract_media_text` 三种图片形态一律返 `[图片]`。
2. **三条写路径**落 `extra.thumb`：`parse_init_protobuf` 首包 / `_parse_301_messages` 补全 / `capture_all`（补写判据纳入 thumb）。
3. **WS 实时路径** `recv_daemon._extract`（t==27）：URL/base64 不进 text，thumb 进 extra。
4. **顺带修真缺陷**：`recv_daemon` init 同步路径硬编码 `extra="{}"`（两条 INSERT）→ 图片 skey/origin_url/thumb 全丢；抽 `_msg_extra_json()` 统一产出。
5. **读侧派生** `api/messages.py`：`thumb_url = ex.get("thumb")` 下发。
6. **AI 读侧** `ai_reply.py`：`_sanitize_history_text` 由「补丁」降级为**契约级存量兜底**（写侧已收口）。
7. **前端** 3 文件：`Msg`/`RawMessage` 增 `thumb_url`；气泡用 `thumb_url` 补 `media.thumb`；映射透传。
8. **存量迁移** `scripts/migrate_image_text_contract.py`：dry-run 默认 / `--apply` 才写 / 写前自动备份 / 逐条判定仅改 text+extra / 幂等。
9. **机械门禁** `test_h25b_image_text_contract.py`：10 项（G1~G8，含负控）。

---

## 5. 实机验证（Live Verification）

| 项 | 结果 |
|---|---|
| 存量迁移 | 37 行（25 字节迁移 + 12 纯化）；残留 text 型 base64/URL = **0 / 0** |
| 备份 | `dyautodm.db.bak.h25mig.20260925_211359` / `..._211535` |
| 无损性 | 25/25 `thumb` 合法 data URI 且 base64 可解码（非截断） |
| AI 读侧 | 真实调 `_build_history` 扫 **175 会话** → base64 泄漏 **0** |
| 前端读侧 | `extra.thumb → thumb_url` 可派生 **25/25** |
| 契约门禁 | `test_h25b` **10/10**（含负控：注入旧 base64 内联 → `test_g2b` 变红） |
| 前端编译 | `tsc --noEmit` EXIT=0 |
| 全量回归 | 895 项；**9 项既有基线失败**（`camoufox`/`patchright` 未装 + replay 数差 2），经 `git stash` 证明确非本批引入 |

---

## 6. 知识资产

- ADR：`DYAutoDM_v2/docs/adr/ADR-011-media-text-extra-contract.md`
- 迁移脚本：`DYAutoDM_v2/scripts/migrate_image_text_contract.py`
- 门禁：`DYAutoDM_v2/backend/test_h25b_image_text_contract.py`

---

## 7. 遗留（Honest Caveats）

- **其他（诚实标注）**：
  - 库容未回收：字节从 `text` 移到 `extra.thumb`，总量不变 ⇒ H-27（方案 B 单独立项）。
  - `_sanitize_history_text` 保留为存量兜底；待历史行全部符合契约后可删。
  - `[表情包] <url>` 形态未纳入：该 URL 是公开 CDN（可直接渲染），与图片加密体不同类，属另一条链路。
  - 待部署：源码层已改，须重建 sidecar 才在运行实例生效（用户择机）。

---

## 8. 补充：两处自身缺陷修复（提交 `9f76ed9`）

| # | 缺陷 | 根因 | 修法 |
|---|---|---|---|
| 1 | 升版只同步 **5 处**版本源，漏 **`src-tauri/Cargo.lock`**（第六处） | 我的升版脚本漏了 Cargo.lock | 字节级替换（唯一匹配）→ 0.45.3；`test_debug_mcp::test_six_sources_aligned` 转绿（**门禁正确拦下**） |
| 2 | `test_h25b` 在全量下 g7/g8 **skip** | 实测：全量跑时 `test_config_isolation` 把 `DY_APP_ROOT` 改写到 `%TEMP%/dyautodm_cfgtest_root` 且未在本类 `setUpClass` 时还原 ⇒ 只读环境变量选到空库（`picked=''`） | 候选根 = 环境变量 ∪ 项目设计根常量，按账号名定位 ⇒ **顺序无关** |

**验证**：全量 **886 passed + 0 skipped**（此前 883 + 2 skipped）；9 failed 全为既有基线（`camoufox`/`patchright` 未装 + replay 数差 2，`git stash` 证实非本批引入）。

## 9. 🔴 我自己的误判与撤回（写入教训）

**误判**：我一度登记「`test_h25_dirty_data_guards.py` 的 setUp 向真实生产库 INSERT（违反 tests/ 隔离铁律）」（台账 H-28）。

**实测推翻**：跑该测试前后比对真实库 `(size, mtime, md5)` = `(1544192, 1790329134, e197962a…)` **前后逐字节相同**。该测试实际用 `tempfile.mkdtemp()` 临时库（L120-121），**从未直连真实库** ⇒ 指控不成立。

**教训**：我当时凭「grep 到 INSERT」+「全量下 g8 失败」两个**代理迹象**就下了铁律违规指控 —— 违反本项目「结论须有硬证据 / 推断须实测验证」铁律。正确顺序是：**先跑前后 md5 比对（直接指标），再下结论**。真因（g8 skip）是**我自己** `test_h25b` 的选库时序脆弱性，与 dirty 测试无关。

台账 H-28 已就地更正为「❌ 误判，已实测撤回」。
