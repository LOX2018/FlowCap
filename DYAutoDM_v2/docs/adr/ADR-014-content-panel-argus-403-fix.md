# ADR-014 内容板块残缺根因（Argus 403 secsdk 签名断链）修复

- 状态: **已采纳（Accepted）**
- 日期: 2026-09-26
- 版本: v0.45.7 → v0.45.8
- 作者: LOX（Hermes 协同）

## 一、背景（Context）

用户报告前端"内容板块"功能不全、无法正常使用。上游情报（基座 `DY_Spider_base` +
`utils/secsdk_web_sign.py`）+ 项目自带 MCP 探针定位：

- 5 个内容列表端点（`/aweme/v1/web/aweme/{favorite,post}`、`/aweme/v1/web/{aweme,mix}/listcollection/`
  `/aweme/v1/web/collects/list/`）全部在 secsdk webSign **保护清单**内；
- 基座 `signed_url()` 实现完整（带 uifid + `x-secsdk-web-signature`），但**调用点未接线**——
  原实现把 `params.get()` 交给 requests `params=`，缺签名 ⇒ 被 Argus 网关恒返 **HTTP 403**
  （46 字节 `Blocked by ArgusSecurityPlugin Uifid Not Found`），前端只收到空；
- `/liked`、`/favorite` 取不到本人 `sec_uid` 时抛 **502**（错误语义，用户无法区分功能坏/登录态）；
- `/action/follow` 因基座**无 commit/follow 写方法**恒 **501**，前端也无调用入口 ⇒ 假入口。

## 二、决策（Decision）

1. **修复而非去除**：5 个列表端点改走 `params.signed_url()`（与既有 `get_user_work_info`
   同范式），补齐 C-02 签名接线缺口。实机验证 403 → 200。
2. **错误语义修正**：`/liked`、`/favorite` 取不到本人 `sec_uid` 时，由 502 改为
   **200 + `unavailable=True` + `reason`**（与"平台侧空响应"同一降级范式），前端空态展示
   真实原因，让用户可行动（如"稍后重试"）。
3. **无法修复则去除**：`POST /action/follow` 端点 + 前端 `follow()` 定义一并删除
   （基座无写方法、前端无调用 ⇒ 永远失败的假入口）。将来基座补齐 `commit/follow/user/`
   链路时，端点与前端入口**同步加回**（勿只加一半）。
4. **健壮性**：`get_my_sec_uid` 加进程内缓存（TTL 600s）+ 指数退避重试，避免瞬时
   `status_code=8` 直接判死、避免每次调用都打真实账号（风控常识）。

## 三、后果（Consequences）

- **正面**：内容板块点赞/收藏/作品/合集/收藏夹真实可拉取（200）；关注入口不再暴露永失败假入口；
  错误态可区分、可行动。
- **负面/遗留**：
  - `listcollection` 实测返 974KB HTML（接口形态待单独解析适配），已打通（200）但前端解析待补；
  - `tab/feed`、`aweme/detail` 两处 secsdk 调用点仍未接线，登记于 `.known-gaps.json`
    （风险低：上游策略收紧时静默 403，非崩溃）；
  - `profile/self` 高频访问后 `status_code=8` 仍未根治（服务端临时降权，非代码问题；
    缓存+重试已缓解其冲击）。

## 四、合规（Conformance）

- CANONICAL CONTRACT（C-02 secsdk 签名接线）：G2 门禁从"新增 5 处违规"回到"仅 2 处未修"。
- 版本同步：6 处版本源齐平 0.45.8。
- 测试：131 上游契约测试全过；TSC 无错。
