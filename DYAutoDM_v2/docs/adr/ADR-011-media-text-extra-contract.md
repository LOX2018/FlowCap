# ADR-011：媒体消息「统一落库契约」—— `text` 与 `extra` 职责分离

| 项 | 值 |
|---|---|
| **状态** | ✅ 已采纳并实施（2026-09-25，v0.45.2 → v0.45.3） |
| **决策者** | 用户拍板方案 C（2026-09-25）；agent 取证与实施 |
| **影响面** | `auto_dm/conversation_capture.py`、`daemon/recv_daemon.py`、`services/ai_reply.py`、`api/messages.py`、`frontend/src/components/messages/*`、新增迁移脚本与门禁 |
| **关联** | ADR-008（AI 上下文与并发）、ADR-009（WP 协议层）、台账 H-25 |

---

## 1. 背景：一个字段，三个需求互相冲突

`dm_messages.text` 当初按「给前端渲染」设计，把小图缩略图以内联 base64 写入
（`conversation_capture._extract_media_text`，2026-08-31 实测决策：图均 2.9KB、
零请求、库容 +13%，优于图床 0.35~2.5s 下载。**该决策在当时是正确的**）。

但随着消费方增多，`text` 被三个需求相反的消费者共用：

| 消费者 | 需要的形态 | base64 的影响 |
|---|---|---|
| 前端气泡 | 可渲染缩略图（离线、瞬时） | ✅ 正是为此写的 |
| AI prompt（`_build_history`） | 纯语义 | ❌ 毒药：实测 25 条 / 103k 字符 ≈ 34k tokens |
| ChatLab 导出 / 审计 | 纯语义 | ❌ 噪音：`content` 字段直接写 `text` 原文 |

**这是典型的 SoC 违反**：`text` 是「展示语义」字段，却被当成**二进制载荷载体**。

## 2. 决定性取证（实测，非推断）

| # | 证据 | 结论 |
|---|---|---|
| E1 | 25 条 base64 行 **25/25**、12 条 URL 行 **12/12**，`extra` 全部带 `skey`+`origin_url` | 解密能力从未丢失，`text` 里的字节**不是唯一数据源** |
| E2 | 直接 HTTP 抓 `origin_url` 返回 **加密体**（magic `9f3d410b`，非图片魔数，27~63KB） | `[图片] <url>` 形态**既污染 prompt 又无展示价值**（浏览器不可解码） |
| E3 | 原图本地缓存 **TTL 默认 30 天会 `unlink`**（`origin_image_resolver._ttl_seconds`） | **删 base64 有真丢图风险** ⇒ 删之前必须补偿 |
| E4 | 后端详情接口**已返回** `image_url`（`extra` 解密派生），前端**已有** `image_url` 优先渲染分支 | 补偿路径**已存在**，无需新建取图链路 |
| E5 | 全项目构造 `data:image/webp;base64,` 的**唯一生产者**是 `_extract_media_text:291` | 收口点是单点、干净 |

## 3. 决策：统一落库契约

```
dm_messages.text   = 纯语义标签        "[图片]" / "[表情包]"
dm_messages.extra  = {skey, origin_url, thumb, ...}
                       ↑ 前两项原有；thumb 为本次新增（承载缩略图字节）
/conversation      = 后端从 extra 派生下发 image_url（解密原图）/ thumb_url（缩略图）
```

### 3.1 备选方案与取舍（用户拍板）

| 方案 | 做法 | 收益 | 代价 | 结论 |
|---|---|---|---|---|
| **A** | 只改新数据 | 改动最小 | 历史 25 条仍带 base64，**债仍在**（治标） | ❌ 否决 |
| **B** | 缩略图落本地文件 + `text` 纯化 | 额外回收 ~250KB/张 库容（本账号约 9MB） | 改动面约 C 的 3 倍（新增文件生命周期 + TTL + 取图端点） | ⏸ 单独立项 |
| **C** | **base64 就地搬到 `extra.thumb`**，`text` 纯化 | **零网络、零重编码、完全无损**；prompt/导出立刻干净；前端改动最小 | DB 体积不变（字节仍在行内，只换字段） | ✅ **采纳** |

**为何接受「库容不变」**：2026-08-31 的 +13% 是**当时明确权衡并接受的**决策
（代码注释含实测依据）。推翻它需独立论证；而本 ADR 要解决的是**职责混淆**，
不是库容。方案 C 用最小改动同时满足三条硬约束（prompt 纯净 / 不丢图 / 前端可渲染）。

### 3.2 前端消费顺序（保持 image_url 最高优先级）

```
m.image_url（解密真原图）  >  m.thumb_url（契约内缩略图）  >  parseMedia(text)（存量兼容）
```

`text` 现在只含 `[图片]`，`parseMedia` 保留**仅为兼容存量行**。

## 4. 实施清单

1. **写侧收口**（`conversation_capture.py`）：新增 `_thumb_semantic_label()`、
   `_extract_thumb_data_uri()`；`_extract_media_text` 三种图片形态一律返回 `[图片]`。
2. **三条写路径**落 `extra.thumb`：`parse_init_protobuf` 首包 / `_parse_301_messages` 补全 /
   `capture_all`（含补写判据纳入 `thumb`）。
3. **WS 实时路径**（`recv_daemon._extract` t==27）：URL/base64 都不进 `text`，thumb 进 `extra`。
4. **顺带修真缺陷**：`recv_daemon` init 同步路径原先把 `extra` **硬编码为 `"{}"`**
   （两条 INSERT）⇒ 该路径图片的 `skey/origin_url/thumb` 全部丢失。抽出
   `_msg_extra_json()` 统一产出（键位与 `capture_all` 同构）—— 否则方案 C 在此路径失效。
5. **读侧派生**（`api/messages.py`）：`thumb_url = ex.get("thumb")` 下发。
6. **AI 读侧**（`ai_reply.py`）：`_sanitize_history_text` 由「读侧补丁」改为
   **契约级存量兜底**（写侧已收口，本函数只处理历史残留行）。
7. **前端**（3 文件）：`Msg`/`RawMessage` 增 `thumb_url`；气泡用它补 `media.thumb`；
   映射透传。

## 5. 存量迁移（`scripts/migrate_image_text_contract.py`）

- 默认 **dry-run**，`--apply` 才写库；写前**自动备份**（含 `-wal`/`-shm`）。
- **逐条判定**，只重写 `text` / `extra` 两列，绝不 `LIKE` 模糊删（遵项目铁律）。
- 幂等：已符合契约的行跳过。
- **两类形态区别处置**（实测驱动）：
  - `[图片] data:image/...` → `text` 纯化 **+ 字节迁入 `extra.thumb`**（无损）
  - `[图片] <url>`（**两行形态**：`[图片] <thumb>\n[原图] <origin>`）→ **仅 `text` 纯化**。
    实测该 URL 与 `extra.origin_url` **逐字相同**且是加密体；塞进 `thumb` 会让前端
    当可渲染图去加载（必失败）。展示已由 `image_url` 覆盖。

## 6. 验证（实机，真实代码 + 真实生产库）

| 项 | 结果 |
|---|---|
| 存量迁移 | 37 行（25 字节迁移 + 12 纯化）；残留 text 型 base64/URL = **0 / 0** |
| 备份 | `dyautodm.db.bak.h25mig.20260925_211359` / `..._211535` |
| 无损性 | 25/25 `thumb` 为合法 data URI 且 base64 可解码（非截断） |
| AI 读侧 | 真实调 `_build_history` 扫 **175 会话** → base64 泄漏 **0** |
| 前端读侧 | `extra.thumb → thumb_url` 可派生 **25/25** |
| 契约门禁 | `test_h25b_image_text_contract.py` **10/10**（含负控） |
| 负控 | 注入旧 base64 内联 → `test_g2b` **变红**；恢复 → 10/10 绿 |
| 前端编译 | `tsc --noEmit` EXIT=0 |

## 7. 遗留（诚实标注）

- **库容未回收**：字节从 `text` 移到 `extra.thumb`，总量不变。彻底回收需方案 B（单独立项）。
- **`_sanitize_history_text` 保留**为存量兜底；待历史行全部符合契约后可删。
- **`[表情包] <url>` 形态未纳入**：该 URL 是**公开 CDN**（实测可直接渲染），
  与图片加密体不同类，属另一条链路，本 ADR 不动。
- 需**重新打包**才在运行实例生效（用户择机）。
