# 并发所有权契约 · 2026-09-30 · 直播 AI 回复发送功能性障碍修复

> 建立时间：2026-09-30（本会话）
> 基线版本：v0.45.120（六处齐平）
> 目标版本：v0.45.121（+0.01，本批修复完成后升版）

## 本会话负责（OWNED，仅本会话写入）

| 文件 | 批次 |
|---|---|
| `DYAutoDM_v2/backend/services/delivery_verify.py` | P0-D 投递证据时间锚 |
| `DYAutoDM_v2/backend/models/task.py` | P0-C 尝试文案字段 |
| `DYAutoDM_v2/backend/core/dispatch.py` | P0-C 尝试文案 / P2-B 意向门 |
| `DYAutoDM_v2/backend/api/tasks.py` | P0-D/E 下发字段 |
| `DYAutoDM_v2/backend/services/ai_reply.py` | P2-B 意向门配置 + judge_intent |
| `DYAutoDM_v2/backend/services/ai_agent.py` | P2-B 意向门配置解析 |
| `DYAutoDM_v2/backend/core/auto_dm.py` | P2-B 意向门接线 |
| `DYAutoDM_v2/frontend/src/components/live/live-shared.tsx` | P0/P1 呈现契约 |
| `DYAutoDM_v2/frontend/src/components/live/live-page.tsx` | P0-A/C/E 表格 |
| `DYAutoDM_v2/frontend/src/components/live/LiveReviewMode.tsx` | P0-A/C 表格 |
| `DYAutoDM_v2/frontend/src/components/settings/AgentSection.tsx` | P2-B 意向门 UI |
| `DYAutoDM_v2/frontend/src/api/client.ts` | P2-B Agent 摘要类型 |
| `DYAutoDM_v2/backend/_build_version.py` | 版本号（最后一步） |

## 本会话额外触及（新文件 / 必要的既有守卫更新）

| 文件 | 原因 |
|---|---|
| `DYAutoDM_v2/backend/test_live_ai_send_defects.py`（**新建**） | 本批门禁（19 项，含 6 条负控） |
| `DYAutoDM_v2/backend/test_live_lead_contract.py`（**仅改 1 处断言**） | `test_g7b` 的**代理指标**（字面 `DM_PREVIEW_CHARS`）已被本次「收敛进共享助手 `dmPreviewText`」superseded ⇒ 按契约变更更新为断言新助手的使用（常量仍由 G7 守住）。**非放宽**，判据更严 |

## 本会话**不碰**（另一会话在役，越界即静默覆盖）

- `DYAutoDM_v2/backend/test_abogus_host_guards.py`
- `DYAutoDM_v2/backend/test_config_tag.py`
- `DYAutoDM_v2/backend/test_dm_dispatch_config.py`
- `DYAutoDM_v2/backend/test_m20_search_transport.py`
- `DYAutoDM_v2/backend/test_replay_conversation_read.py`
- `DYAutoDM_v2/backend/test_send_pacing_and_kind.py`
- `DYAutoDM_v2/backend/_build_version.py` 的**升版时序**（提交前重读后再 +0.01）

## 新建本会话文件（唯一输出路径，不与他人共享）

- `DYAutoDM_v2/backend/test_live_ai_send_defects.py`（本批门禁，含负控）
- `工作记忆/cases/2026-09-30_直播AI发送功能性障碍四连修_case_v0.45.121.md`
- `DYAutoDM_v2/artifacts/ADR-032-live-intent-gate-agent-layer.md`

## 取证基线（开工前实测，md5）

```
261353994c1d0c328cb2347c1539321b  live-page.tsx
f35d168bda48044bd022d1de467fdee2  live-shared.tsx
0f5463d54ef0f43a1bf1cde54305e6eb  core/dispatch.py
f023a1a72baa69995dc787ae638127df  services/dm_dispatch.py
d9cce7f0d950e97c3203ebc464d27c2b  api/tasks.py
13b6aa3da28ef2c1a2ede1e183dd2d3a  core/auto_dm.py
```

## 收尾判据

1. 上述 OWNED 文件 `git diff` 全部可归因到本会话；
2. 另一会话的 7 个 test 文件**逐字节未被本会话触碰**；
3. 提交前重读版本号，若已被占则在其新值上再 +0.01。
