# 取证包 · 内容页推荐流/站内通知噪音（2026-09-30，v0.45.124）

案例：`工作记忆/cases/2026-09-30_推荐流空视频与站内通知噪音_根因修复_v0.45.124.md`
提交：`fc6b51e`（修复）· `4e2dc2c`（归档）

## 用法
全部脚本设 `DY_APP_ROOT=C:\temp\dyautodm_design` 后运行（只读取证，除 verify 外皆为探针）：

| 文件 | 作用 |
|---|---|
| `**/_probe_content_raw*.py` | 直连上游打印 `/tab/feed/`、`/notice/` 的**原始响应**（裁剪前形态） |
| `**/_probe_notice_blank*.py` | 定位「空文案」通知的原始字段结构 |
| `verify_content_feed_notice_20260930.py` | **实机验证**（in-process 调改动后的 route handler，真实上游） |

## 关键实测事实（复现判据）
- `refresh_index` 是**换一批旋钮**：ri=1..12 → 58 条、**0 重复**；单次 2~6 条与 count 无关。
- `aweme_list` 混入 **`aweme_type=101` 直播推荐卡**（带 `cell_room`、无 `video`）。
- `notice_list_v2` 无顶层 `content`；文案随 type 分派：33→`follow.from_user.nickname`、
  31→`comment.comment.text`、41→`digg.aweme.desc`（实测 n=47：新粉丝 24/点赞 13/评论 10）。
