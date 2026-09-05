# DYchajian 项目 Git 提交历史

> 由 Hermes 于 2026-08-25 自动导出
> 仓库根: C:/Users/LOX/Desktop/DYchajian (含 DYAutoDM_v2 子项目)

## 提交记录 (oneline, 最近 25 条)

d7307b3 chore: 鏁寸悊 V2 宸ョ▼缁撴瀯骞跺綊妗ｅ伐浣滆蹇哷n - 鍒犻櫎 V1 娈嬬暀鑴氭湰(_copy_main.bat)涓庤皟璇曟帴鍙?debug_chat_api.py) - 绉婚櫎娴嬭瘯鎴浘銆佽繍琛屾棩蹇楀強杩愯鏈熸暟鎹簱鏂囦欢(涓嶅叆搴? - 鍚庣: 娴忚鍣ㄥ畧鎶よ繘绋嬨€佺櫥褰?绉佷俊閾捐矾銆佺洿鎾В鏋愰€傞厤閲嶆瀯 - 鍓嶇/妗岄潰澹? sidecar 璋冪敤銆佽处鍙烽〉銆佺増鏈彿鍚屾銆乀auri 閰嶇疆涓?Rust 鍚姩澹虫洿鏂癭n- 鏂板 宸ヤ綔璁板繂/ 鍏瘒缁撴瀯鍖栨枃妗?涓?鍙樻洿璇存槑.md
562a7c0 ﻿清理 V2 陈旧记忆文件
d270988 ﻿清理 V1 (DY_Spider_base)：彻底删除已废弃版本
5909107 fix(V2): 私信根因实测-cookie过期+数据库基准不一致+浏览器批量查昵称+任务状态启动收尾 0.28.5
007a4d1 fix(V2): CDP实测发现get_user_info可用作昵称解析主路径, 头像提取avatar_small 0.28.4
3ef5b95 feat(V2): _resolve_names线程池并行+优先get_im_user_info+头像支持 0.28.3
8b1727f fix(V2): _pull_conversations_api因WS同步帧占据收件箱而未调用, 改用_api_pulled标记强制拉取 0.28.2
b24edd1 fix(V2): _pull_conversations_api SQL INSERT 8个?→7个, 修复私信中心500错误 0.28.1
c27fe50 fix(V2): database.py accounts.json迁移_ROOT未定义改用app_root() 0.28.0
1d2591b feat(V2): get_message_by_init全量会话+sec_uid就近匹配+get_user_info昵称解析 0.27.0
f6a5cdc docs: 私信中心根因排查记录——get_message_by_init(cmd 2043)才是全量会话源 0.26.0
1325bc6 feat(V2): JSON→SQLite全面迁移(config+accounts+tasks+dm_history+kv_store+分页) 0.26.0
bbfdc2f fix(V2): 历史任务STOPPING悬空收尾+老数据迁移修复 0.25.0
c34c230 fix(V2): 私信会话列表 IM API 直接拉取兜底，守护启动即有会话 0.24.0
723210f fix(V2): 私信拉取补 account 参数修复422 + 启动闪屏过渡 0.23.0
a0f52e0 perf(V2): 共享数据常驻轮询切页秒开+账号校验预热+私信守护自动恢复+解析快速路径 0.22.0
77764d0 fix(V2): 任务容器回读+词库随机+发送结果反馈+任务中心停止存量+查阅模式数据源 0.21.0
dee0f2c ﻿fix(V2): 消除日志删除/开启私信/私信中心延迟 + 设置页改列表子导航
064962e feat(accounts): 启动自检静默 + 引擎校验简短化 + 上次运行记录真实化
4ca888b ﻿fix(前端): 私信中心显示"无已授权账号" — messages.tsx 账号列表解包错误
c7c33f8 ﻿﻿fix: 私信页面会话列表为空 — messages.py 真实转发 recv_daemon
4c2eeac ﻿﻿fix(live): 直播监听页停止改为软停止，保留存量私信发完
b9d16a6 ﻿修复存量私信调度层漏发（日志说发完但实质未发）
4304598 fix(v2): 修复直播监听实时评论统计列表为空
8fff24b 打包 0.13.0：修复 build.ps1 版本替换污染并完整 tauri build

## 完整提交详情 (最近 8 条)

### d7307b3 — chore: 鏁寸悊 V2 宸ョ▼缁撴瀯骞跺綊妗ｅ伐浣滆蹇哷n - 鍒犻櫎 V1 娈嬬暀鑴氭湰(_copy_main.bat)涓庤皟璇曟帴鍙?debug_chat_api.py) - 绉婚櫎娴嬭瘯鎴浘銆佽繍琛屾棩蹇楀強杩愯鏈熸暟鎹簱鏂囦欢(涓嶅叆搴? - 鍚庣: 娴忚鍣ㄥ畧鎶よ繘绋嬨€佺櫥褰?绉佷俊閾捐矾銆佺洿鎾В鏋愰€傞厤閲嶆瀯 - 鍓嶇/妗岄潰澹? sidecar 璋冪敤銆佽处鍙烽〉銆佺増鏈彿鍚屾銆乀auri 閰嶇疆涓?Rust 鍚姩澹虫洿鏂癭n- 鏂板 宸ヤ綔璁板繂/ 鍏瘒缁撴瀯鍖栨枃妗?涓?鍙樻洿璇存槑.md



### 562a7c0 — ﻿清理 V2 陈旧记忆文件

删除与当前架构（0.29.0）不符的旧记忆/调试文档：
- 根目录 项目工作记忆导出_20260820.md（V1+V2 混合，V1 已删、V2 状态过时）
- DYAutoDM_v2/_workmemory_20260818.md、_workmemory_20260819.md、_workmem_accounts.md
  （覆盖 0.16.0→0.28.5 旧链路，与当前 git 改动状态冲突）
- DYAutoDM_v2/docs/bcc_progress.md 等调试残留文档
- V2 根目录临时调试脚本/日志（_probe_*.py、build_*.log、_git*.txt、_copy_main.bat）

MEMORY.md 重写为当前架构权威基线（0.29.0），声明旧系统记忆冲突条目作废。
后续以实际磁盘代码 + MEMORY.md 为准，不据陈旧记忆执行。


### d270988 — ﻿清理 V1 (DY_Spider_base)：彻底删除已废弃版本

- 物理删除 DY_Spider_base/ 整个目录（代码 + 缓存 + 工作记忆 md）
- git rm --cached 取消跟踪 V1 全部 88 个文件
- root .gitignore 追加 DY_Spider_base/ 防止重新误跟踪

V1 已无价值，项目仅保留 V2 (DYAutoDM_v2) 作为唯一活跃代码库。
遵守规范：V1 永远冻结且已彻底清除，后续所有开发仅针对 V2。


### 5909107 — fix(V2): 私信根因实测-cookie过期+数据库基准不一致+浏览器批量查昵称+任务状态启动收尾 0.28.5



### 007a4d1 — fix(V2): CDP实测发现get_user_info可用作昵称解析主路径, 头像提取avatar_small 0.28.4



### 3ef5b95 — feat(V2): _resolve_names线程池并行+优先get_im_user_info+头像支持 0.28.3



### 8b1727f — fix(V2): _pull_conversations_api因WS同步帧占据收件箱而未调用, 改用_api_pulled标记强制拉取 0.28.2



### b24edd1 — fix(V2): _pull_conversations_api SQL INSERT 8个?→7个, 修复私信中心500错误 0.28.1

## 当前工作区状态 (未提交改动, 注意: 这些不在 git 历史中)

M DYAutoDM_v2/.gitignore
 M DYAutoDM_v2/backend/core/auto_dm.py
 M DYAutoDM_v2/backend/database.py
 M DYAutoDM_v2/backend/main.py
 M DYAutoDM_v2/backend/tasks_history.py
 M DYAutoDM_v2/frontend/src/pages/live.tsx
 M DYAutoDM_v2/frontend/src/pages/settings.tsx
 M DYAutoDM_v2/package-lock.json
?? DYAutoDM_v2/项目说明.md
?? "Hermes + LLM Wiki + Obsidian 三工具联动构建个人知识库.md"
?? LLM-Wiki/
?? "Logseq mcp说明.md"
?? _export_git.py
?? _git_history_export.md
?? _push_user_profile.py
?? data/dyautodm.db
?? data/dyautodm.db-shm
?? data/dyautodm.db-wal
