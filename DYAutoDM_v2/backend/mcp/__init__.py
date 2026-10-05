# -*- coding: utf-8 -*-
"""MCP 层 —— 本机 HTTP + stdio 双入口，把既有业务能力暴露给 AI 客户端。

## 设计来源（对标 better-douyin `src-tauri/src/mcp.rs` + `src/bin/douyin-dl.rs`）

蓝本经二进制情报提取出的设计要点（详见 `docs/arch_better_douyin.md` §5）：

| 蓝本要素 | 本模块落点 |
|---|---|
| 只监听本机（127.0.0.1） | `server.py` 仅 bind 回环地址 |
| Bearer Token，轮换后旧票立即失效 | `config.py` 的 token 世代号（`token_epoch`） |
| `allow_write_actions` 默认只读 | `registry.py` 工具按 READ/WRITE 分级 |
| `require_confirmation` 写操作双闸 | `registry.py` 确认票据（单次有效） |
| 审计只记工具名/字段摘要/耗时/错误码（脱敏） | `audit.py` |
| 独立进程 + 单一 stdio 入口 | `__main__.py`（`python -m backend.mcp serve`） |
| 端口占用自动向后探测 | `server.py` |

## 与蓝本的关键差异（2026-09-25 订正 —— 原文「不采纳」已过期）

本节原写「本项目不采纳主动拉取昵称/资料类接口，工具面只包装既有业务能力」。
**该表述在本分支已不成立**：2026-09-14 用户授权「全解除」
（留痕 `docs/BRANCH_RULES_design_better_douyin.md`，含「已充分告知」记录），
`tools.py` 实际已注册平台读取类工具（`search_user` / `user_info_batch` 等）。

**现行策略**：授权是**权限上限**，施工仍以**保守**为主（用户 2026-09-25 裁定）。
故 ADR-010 引入 `scope` 维度：默认 `full` = 既有全量；`debug` 面只含调试工具，
主动查询类工具在该面**物理不可达**。昵称的**风控红线**（不主动批量查询）依然有效。

## 分层

    config.py    配置与令牌世代号（唯一真源）
    registry.py  工具注册表 + READ/WRITE 分级 + 确认票据
    audit.py     脱敏审计日志（环形保留）
    server.py    本机 HTTP 传输（Bearer 校验）
    __main__.py  stdio 入口（蓝本的单入口形态）
    tools_debug.py  debug 工具族（ADR-010；scopes=("debug",)）

FastAPI 管理面不在本目录 —— 见 `backend/api/mcp.py`（挂载到 `/api/mcp`）。
"""
