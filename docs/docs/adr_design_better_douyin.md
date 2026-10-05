# ADR: 引入 better-douyin 架构逆向情报作为设计参照

- **分支**：`design/better-douyin`
- **版本**：v0.43.12
- **日期**：2026-09-14
- **状态**：Accepted

## 背景

微信软文推荐了开源项目 `anYuJia/better-douyin`（339★）。经只读探测确认：
该仓库是 **Open Shell（UI 壳 + mock）**，真实平台连接器、签名、加密、下载解析全部闭源，
完整能力仅以二进制形式通过 GitHub Releases 分发（v1.1.18）。

按用户指令，本分支作为**全新架构试验田**，允许突破本项目既有铁律，
目标是**从该发行版反编译/提取完整实现思路**，作为本项目架构对照与借鉴来源。

## 决策

1. 建立独立分支 `design/better-douyin`，不污染 `v2-refactor`。
2. 对官方 Release 做**只读二进制情报分析**，产出 `docs/arch_better_douyin.md`。
3. **逆向产物（二进制、提取物）不进入仓库**，仓库只存文字级情报。
4. 情报仅作技术参照；**明确标注不采纳项**（尤以昵称主动拉取触碰风控红线）。

## 关键发现

| 发现 | 内容 |
|---|---|
| 双进制架构 | 主壳 `better-douyin`(14.6MB) + 独立后端 `douyin-dl`(8MB)，**同为 Rust** |
| 私有工程名 | 二进制残留 `/Users/runner/work/better-douyin-private/...`，证实壳/核分离 |
| 模块树 | 从 rustc panic 路径还原 `api/`(16) + `commands/`(18) + `downloader/`(11) |
| IM 通道 | `wss://frontier-im.douyin.com/ws/v2`，pbbp2 + protobuf + permessage-deflate |
| 风控层 | TicketGuard：`bd-ticket-guard-ree-public-key` + `ts_sign` + EC 分支 |
| MCP | 独立二进制 + 仅 stdio 入口（"Other automation entrypoints intentionally not exposed"） |

## 对本项目的实际影响（实测核对，非推测）

- **签名/风控层**：本项目 `backend/utils/bd_ticket.py` 已完整实现 TicketGuard
  （NIST P-256 + SHA-256 + DER），参数面 **6/6 覆盖**，自测 `verify PASS`。
  → **本项目该层不落后，反而更可验证**（better-douyin 二进制仅暴露参数名）。
- **可借鉴点**：IM 会话世代号（`account_generation`/`listener_epoch`）、
  empty-frame 三分判别、媒体代理层收敛、MCP 权限模型、下载流水线切分。
- **不采纳**：`/aweme/v1/web/im/user/info/` 主动拉昵称（风控红线）、
  自算 a_bogus、注入式 signer probe。

## 修正记录

初稿曾判定本项目存在"TicketGuard 缺口"，经实机核对**该判断错误并已撤回**。
教训：架构对照必须在两侧都做实测，不可仅凭单侧证据下结论。

## 后果

- 收益：获得一份可直接对照的完整架构与协议情报，含抖音侧新增风控层参数面。
- 成本：无代码改动，仅新增一份文档。
- 风险：闻见情报具时效性（2026-09 快照），抖音侧变更后需重取。
