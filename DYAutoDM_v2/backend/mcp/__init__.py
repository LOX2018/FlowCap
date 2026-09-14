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

## 与蓝本的关键差异（本项目的风控红线）

蓝本会为 AI 客户端暴露**主动拉取昵称/资料**类接口（`/aweme/v1/web/im/user/info/`）。
**本项目不采纳**——昵称唯一来源是 BCC 被动 hook（见 `docs/replication_plan.md` §四）。
因此本模块的工具面**只包装既有业务能力**，不新增任何主动查询路径。

## 分层

    config.py    配置与令牌世代号（唯一真源）
    registry.py  工具注册表 + READ/WRITE 分级 + 确认票据
    audit.py     脱敏审计日志（环形保留）
    server.py    本机 HTTP 传输（Bearer 校验）
    __main__.py  stdio 入口（蓝本的单入口形态）
    router.py    FastAPI 管理面（挂载到 /api/mcp）
"""
