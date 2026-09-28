# 维度审计：静默兜底（silent fallback）普查

- **审计日期**：2026-09-27
- **审计对象**：`C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend`
- **探测脚本**：`DYAutoDM_v2/scripts/check_silent_fallback.py`（可复跑，含 `--json` / `--baseline` / `--selftest`）
- **运行命令**：`py314 scripts/check_silent_fallback.py`
- **口径来源**：`artifacts/全库审计_H6_2026-09-22.md` §S2（历史基线 386 处，AST 口径，排除 `test_*.py`/`verify_*.py`）

> 本报告所有数字**均来自脚本实际输出，无手写估算**。分级判定基于 **AST + try 块全文**（按缩进范围取真实语句块，见脚本 `_block_text()`），不是只看 `except` 行文本。

---

## 1. 摘要

### 1.1 总数（分类计数）

| 类别 | 计数 |
|---|---:|
| **总命中** | **627** |
| ├ 🔴 L1-写库路径 | 69 |
| ├ 🔴 L1-外发路径 | 33 |
| ├ 🟠 L2-外部资源（文件 IO / 网络非外发 / subprocess / 浏览器启动） | 61 |
| └ 🟡 L3-内部逻辑（纯内存计算/解析/格式化） | 464 |

### 1.2 与历史基线 386 的对比

| 口径 | 计数 | Δ vs 386 |
|---|---:|---:|
| **H6 严格口径**（handler 体仅 `pass`/`continue`，或仅 `logger.debug`）——**独立复算** | **445** | **+59** |
| 本脚本同口径子集（`pass-only` + 低级别日志，含 `logger.info`/`print`） | 451 | +65 |
| 本脚本全集（额外含 `return None/{}/[]/False` 与 `contextlib.suppress`） | 627 | +241 |

**结论：按历史同口径复算，静默兜底为 445 处，较 386 不减反增 +59（+15.3%）。**
历史审计的定性（"不减反增、集中在 core 链路"）在 5 天后依然成立，且**新增了写库/外发等高危域的具体实例**（见 §4）。

> 口径差异说明：H6 只数 `pass/continue` 与 `logger.debug` 两类；本脚本额外把
> ①`logger.info`/`print`（sidecar 无 stdout，等同静默）②`return None/{}/[]/False`
> ③`contextlib.suppress` 纳入。故全集 627 > 同口径子集 451。**报告对比一律以 445/451 为准**。

### 1.3 兜底形式分布

| 形式 | 计数 | 危险度 |
|---|---:|---|
| `pass-only`（`pass`/`continue`/空 nop，完全吞掉） | 351 | 🔴 最高 |
| `return-empty`（`return None/{}/[]/False`） | 176 | 🟠 中高 |
| `log-only-low`（仅 `logger.debug`/`info` 或 `print`，有记录但级别低） | 98 | 🟡 中低 |
| `bare-except`（裸 `except:`，连 `KeyboardInterrupt` 都吞） | 2 | 🔴 高 |

其中 **L1 域内的 `pass-only`：写库 34 处、外发 9 处**（另有 5 处写库 `pass-only` 判为"需人工复核"，见 §5）。

---

## 2. 高危清单（L1 逐条，按路径类型排序）

> 排序：先 **外发**（风控第一高危），后 **写库**。`⚠复核` = 该处是否算高危需人工判（不拔高也不隐瞒）。

### 2.1 🔴 L1-外发路径（33 处）

| 文件:行号 | 函数 | 兜底形式 | 吞掉的操作 | 危害说明 | 建议修法 |
|---|---|---|---|---:|
| `backend/vbrowser.py:350` | `check_egress_ip` | pass(continue) | `page.goto` + `page.evaluate`（真实 HTTP 出网探测） | 出网 IP 探测失败被静默跳过 → 无法判定代理是否泄漏真实 IP | `logger.warning` 记录 url + 异常；标记该次探测「未知」而非跳过 |
| `backend/daemon/browser_daemon.py:1434` | `_do` | pass-only | `page.goto("about:blank")` | 导航重置失败被吞 → 页面可能残留旧上下文，后续操作基于错误 DOM | 记 `logger.warning`；失败时显式重置 driver 状态 |
| `backend/daemon/browser_daemon.py:1614` | `_human_click` | pass-only | `page.evaluate`（注入点击坐标） | 拟人化注入失败被吞 → 点击可能退化为非拟人路径（**风控高危**） | 记 `logger.warning` 并计数；连续失败应上报告警 |
| `backend/dy_apis/login_api.py:595` | `login_grab_ticket` | pass-only | `page.evaluate`（抓 ticket） | 抓 ticket 失败被吞 → 登录票据缺失无任何线索 | 必须 `logger.warning`；上层据返回值判定登录失败 |
| `backend/dy_apis/login_api.py:612` | `login_grab_ticket` | pass-only | `page.evaluate`（同上，第二处） | 同上，双重静默 | 同上 |
| `backend/daemon/browser_daemon.py:1652` | `_detect_existing_bcc` | pass-only | 端口探测 + 会话（`_port_open`/`urlopen`） | 环境分叉检测失败被吞 → 可能双开同一 profile（`BCC-042` 环境分叉） | 记 `logger.warning`；不确定时按「疑似分叉」告警 |
| `backend/auto_dm/conversation_capture.py:300` | `_extract_thumb_data_uri` | pass-only | `image_host.upload_base64`（**上传到图床**） | 缩略图上传失败被吞 → 消息缩略图静默丢失，且**已发生外发副作用** | 记 `logger.warning`；返回 None 由调用方决定占位 |
| `backend/downloader/media_request.py:317` | `resolve_playable` | log-only-low | `requests.get`（Range 探测可播放性） | 探测失败只有 debug → 视频不可播原因不可见 | 升 `logger.warning` + 记录 status/url |
| `backend/downloader/media_request.py:319` | `resolve_playable` | pass-only | `requests.get`（同上） | 同上，双重静默 | 同上 |
| `backend/daemon/wp_recv.py:392` | `poll_once` | return-empty | `httpx.post`（拉取 `/wp_messages`） | 轮询失败返回空 → 上层以为「无新消息」，实为请求失败（**误判**） | 返回 `None` 区分「失败」与「空」；记 `logger.warning` |
| `backend/notify/events.py:71` | `emit` | log-only-low | `n.emit(...)`（事件通知外发） | 通知外发失败只有 debug → 「以为发了实际没发」 | 升 `logger.warning` + 失败计数 |
| `backend/services/reply_kb.py:310` | `find_match` | pass-only | `find_match_semantic_reply`（语义回复匹配） | 匹配失败被吞 → 可能落到错误回复分支 | 记 `logger.warning` + 返回明确「未匹配」 |
| `backend/api/accounts.py:1133` | `_quit_daemon_http` | return-empty | `urlopen` POST `/quit` | 停止 daemon 失败返回空 → 上层以为已停止 | 返回明确失败标志 + `logger.warning` |
| `backend/auto_dm/conversation_capture.py:949` | `fetch_conversation_history` | return-empty | `requests.post`（拉会话历史） | 拉取失败返回空 → 历史静默缺失 | 记 `logger.warning`；调用方区分空/失败 |
| `backend/auto_dm/image_host.py:180` | `upload` | return-empty | `_upload_tucdn`（**图床上传**） | 上传失败返回空 → 图床 URL 缺失且无线索 | 记 `logger.warning` + 向上抛或明确失败 |
| `backend/auto_dm/login_remote.py:437` | `click_by_text` | return-empty | `page.evaluate`（点击登录按钮） | 点击失败返回 False → 登录流程静默卡死 | 记 `logger.warning`；调用方判返回值 |
| `backend/daemon/bcc_login.py:48` | `_do` | return-empty | `page.evaluate`（读 localStorage 凭证） | 读凭证失败返回空 → 登录态判定错误 | `logger.warning` + 明确失败 |
| `backend/daemon/browser_daemon.py:1166` | `_ensure_nav_tab` | return-empty | `new_page` + `page.goto` | 导航页创建失败被吞 → 后续导航崩溃点漂移 | `logger.warning` + 返回失败标志 |
| `backend/daemon/browser_daemon.py:1347` | `_do` | return-empty | `page.evaluate`（读捕获事件） | 捕获失败返回空 → 认为「无事件」实为「读失败」 | 区分空/失败 |
| `backend/dy_apis/image_sender.py:448` | `build_signed_url` | return-empty | `requests.post`（`batch_build_image` **抖音 API**） | 签名 URL 构建失败返回空 → 图片发送静默失败 | **必须**`logger.warning`；禁止返回空冒充成功 |
| `backend/dy_apis/login_api.py:144` | `_bcc_alive` | return-empty | `requests.get` `/status` | 存活探测失败返回空 → 误判 BCC 已死/未死 | `logger.debug` → 至少 `logger.warning`（探测结论影响后续决策） |
| `backend/dy_apis/login_api.py:839` | `refresh_cookie_from_profile` | return-empty | `page.goto` 抖音 `/chat` | 刷新 cookie 失败返回空 → 凭证静默失效 | `logger.warning` + 凭证状态置「未知」 |
| `backend/dy_apis/login_api.py:863` | `refresh_cookie_from_profile` | return-empty | 启动浏览器（`launch_sync`） | 浏览器启动失败被吞 → 刷新流程静默中断 | `logger.warning` + 明确失败 |
| `backend/notify/channels.py:427` | `_upload_media` | return-empty | `s.post(getuploadurl)`（**上报外发**） | 媒体上传失败返回空 → 通知静默丢失 | **必须**可见；返回区分空/失败 |
| `backend/notify/cmd_parser.py:171` | `_llm_parse` | return-empty | `aiohttp`（LLM 外发请求） | 解析失败返回空 → 指令静默丢弃 | `logger.warning` |
| `backend/notify/inbound.py:221` | `_run_ilink` | return-empty | `.send(`（iLink 登录外发） | 登录回包失败返回空 → 静默重试或死循环 | `logger.warning` |
| `backend/services/ai_reply.py:633` | `_embedRemote` | return-empty | `requests.post`（embedding API） | embedding 失败返回空 → 语义检索静默降级 | `logger.warning` + 明确降级标志 |
| `backend/services/ai_reply.py:1070` | `describe_image` | return-empty | `requests.post`（chat/completions，**视觉模型**） | 图片描述失败返回空 → AI 回复内容静默缺失 | `logger.warning` |
| `backend/utils/mstoken.py:77` | `get_mstoken` | return-empty | `requests.post`（**上报 msToken**） | token 获取失败返回空 → 后续请求签名失效 | `logger.warning` |
| `backend/vbrowser.py:790` | `launch_vb_env` | return-empty | `requests.post` `/api/launchBrowser` | 启动环境失败返回空 → 误判已启动 | `logger.warning` + 明确失败 |
| `backend/vbrowser.py:809` | `is_vb_available` | return-empty | `requests.get` `/api/status` | 可用性探测失败返回空 → 误判不可用 | 区分「不可用」与「探测失败」 |
| `backend/vbrowser_window.py:179` | `_set_window_state` | return-empty | `.send(`（CDP 外发到浏览器） | 窗口状态设置失败被吞 → UI 状态静默错误 | `logger.debug` → `logger.warning` |
| `backend/vbrowser_window.py:202` | `_set_window_state_sync` | return-empty | `.send(`（同上，同步版） | 同上 | 同上 |

### 2.2 🔴 L1-写库路径（69 处）

> 表格较长，按「直接写库调用（`execute`/`commit`/`insert/update/delete`）」与
> 「封装写库函数」两类列出**全部** 69 处。**这一域直接对应铁律「无回执不认成功」**。

| 文件:行号 | 函数 | 兜底形式 | 吞掉的操作 | 危害说明 | 建议修法 |
|---|---|---|---|---:|
| `backend/daemon/recv_daemon.py:565` | `get_or_create` | pass-only | `INSERT OR IGNORE INTO dm_conversations` | **会话骨架落库失败无任何线索**（H6 §S2 点名同一处） | `logger.warning` 带 account/conv_id |
| `backend/daemon/recv_daemon.py:596` | `get_or_create` | pass-only | `UPDATE dm_conversations SET peer_id,peer_name` | **昵称/对端 UID 覆盖失败无线索**（H6 §S2 点名） | 同上 |
| `backend/daemon/recv_daemon.py:406` | `_enrich_nicknames_once` | pass-only | `UPDATE`（昵称富化回写） | 昵称富化静默失败 → 界面长期显示裸 uid | `logger.warning` |
| `backend/daemon/recv_daemon.py:694` | `mark_read` | pass-only | `UPDATE dm_conversations SET unread=0` | 已读标记失败被吞 → **未读数永久不消** | `logger.warning`；返回成功/失败 |
| `backend/daemon/recv_daemon.py:1509` | `_pull_conversations_api` | pass-only | `INSERT OR IGNORE INTO dm_messages` | 消息落库失败被吞 → **消息"收到"但库里没有** | `logger.warning` + 计数失败 |
| `backend/daemon/recv_daemon.py:1536` | `_pull_conversations_api` | pass-only | `INSERT OR IGNORE INTO dm_messages`（第二条） | 同上 | 同上 |
| `backend/daemon/recv_daemon.py:619` | `list_convs` | pass-only | `conn.execute(SELECT ...)` | 读库失败被吞（读路径，危害中） | `logger.debug` → `logger.warning` |
| `backend/daemon/recv_daemon.py:678` | `get_conv` | return-empty | 确保会话骨架（读/写混合） | 失败返回空 → 上层以为"无会话" | 区分空/失败 |
| `backend/daemon/wp_recv.py:102` | `_ensure_conv` | pass-only | `INSERT OR IGNORE INTO dm_conversations` | WP 通道会话骨架落库失败静默 | `logger.warning` |
| `backend/daemon/wp_recv.py:371` | `process_events` | pass-only | `conn.commit()` | **提交失败被吞 → 上面所有 INSERT/UPDATE 全部丢失且无感知**（最危险的单点） | **必须**`logger.error` + 向上抛 |
| `backend/daemon/wp_recv.py:367` | `process_events` | log-only-low | `UPDATE dm_conversations`（含 msg 落库） | 落库失败仅 debug | 升 `logger.warning` |
| `backend/daemon/wp_recv.py:89` | `_already_exists` | return-empty | `SELECT` 去重查询 | 查询失败返回空 → 误判"不存在"，可能重复落库 | 明确失败语义 |
| `backend/daemon/wp_recv.py:232` | `_maybe_record_ws` | log-only-low | `_record_`（WS 回放录制写盘） | 录制失败仅 debug → 回放样本静默缺失 | 升 `logger.warning` |
| `backend/daemon/browser_daemon.py:1227` | `wp_send_text` | pass-only | `SELECT peer_name`（**发送前置查询**） | 查对端名失败被吞 → 发送可能发错对象（**发送链高危**） | `logger.warning`；失败应中止发送 |
| `backend/services/kv_store.py:56` | `kv_set` | pass-only | `INSERT ... ON CONFLICT DO UPDATE`（KV 写） | KV 写失败被吞 → 配置/状态静默丢失 | `logger.warning`；返回成功/失败 |
| `backend/services/pro_kb.py:95` | `_kv_set` | pass-only | `INSERT INTO kv_store` | 同上（Pro 知识库） | 同上 |
| `backend/services/voice_transcribe.py:334` | `persist_transcripts` | pass-only | `UPDATE dm_messages SET extra` | 转写结果写库失败被吞 → **转写"完成"但库里没有** | `logger.warning` |
| `backend/services/voice_transcribe.py:338` | `persist_transcripts` | pass-only | `conn.commit()` | 提交失败被吞 → 全部转写丢失无感知 | `logger.error` + 抛 |
| `backend/services/nickname_fallback.py:263` | `run_fallback` | pass-only | `conn.commit()` | 提交失败被吞 → 昵称回填全部丢失 | `logger.error` |
| `backend/services/delivery_verify.py:127` | `mark_delivery_verified` | return-empty | `UPDATE`（投递校验落库） | **投递回执写库失败被吞 → 违反"无回执不认成功"** | `logger.warning` + 返回失败 |
| `backend/services/dm_dispatch.py:821` | `mark_seen` | return-empty | `UPDATE`（已见标记） | 标记失败 → 重复处理 | `logger.warning` |
| `backend/services/dm_dispatch.py:698` | `_read_row` | return-empty | `SELECT` | 读失败返回空 → 误判无数据 | 区分空/失败 |
| `backend/services/dm_dispatch.py:848` | `_work` | log-only-low | `UPDATE`（派发状态） | 状态回写失败仅 debug | 升 `logger.warning` |
| `backend/services/dm_dispatch.py:951` | `_peer_from_db` | log-only-low | `SELECT` | 读失败仅 debug | 升 `logger.warning` |
| `backend/services/dm_dispatch.py:1023` | `_is_stranger_first` | return-empty | `SELECT` | 读失败返回空 → 陌生人判定错误（**风控相关**） | 区分空/失败 |
| `backend/services/ai_reply.py:1227` | `save_lead` | return-empty | `INSERT OR IGNORE INTO ai_leads` | 线索落库失败返回空 → **高价值线索静默丢失** | `logger.warning` |
| `backend/services/ai_reply.py:1787` | `_build_history` | return-empty | `SELECT`（历史装配） | 读失败返回空 → 回复上下文缺失 | `logger.warning` |
| `backend/services/app_config.py:93` | `_save` | return-empty | `set_kv_json`（配置写） | 配置保存失败返回 False | `logger.warning`（已返回 False，尚可） |
| `backend/services/app_config.py:123` | `drop_scope` | return-empty | `DELETE FROM kv_store` | 删除失败返回空 | `logger.warning` |
| `backend/services/conv_identity.py:143` | `_infer_from_conv_pool` | return-empty | `SELECT`（身份推断） | 读失败返回空 → 身份判定错误 | 区分空/失败 |
| `backend/services/member_ctx.py:152` | `destroy_session` | pass-only | `_persist_`（会话销毁写盘/删） | 销毁失败被吞 → 凭证可能残留 | `logger.warning` |
| `backend/services/member_ctx.py:83` | `_persist_load` | return-empty | `_persist_`（读持久化） | 读失败返回 None | 区分空/失败 |
| `backend/services/probe.py:152` | `_db_query` | return-empty | `SELECT` | 读失败返回空 | 区分空/失败 |
| `backend/api/messages.py:561` | `get_conversation` | pass-only | `UPDATE dm_conversations SET unread=0` | 已读写失败被吞 → 未读不消 | `logger.warning` |
| `backend/api/messages.py:277` | `_enrich_with_db_nicknames` | log-only-low | `SELECT`（昵称富化读） | 失败仅 debug | 升 `logger.warning` |
| `backend/api/ai.py:666` | `kb_delete` | pass-only | `DELETE FROM kv_store` | 删除失败被吞 → 用户以为已删 | `logger.warning` |
| `backend/api/member.py:220` | `logout` | pass-only | `database.reset_connection()`（连接重置，含写状态） | 重置失败被吞 | `logger.warning` |
| `backend/api/accounts.py:289` | `_rpa_scan_login` | return-empty | `save_`（扫码图写盘） | 写盘失败返回空 | `logger.debug`→`warning` |
| `backend/auto_dm/accounts.py:1541` | `worker` | log-only-low | `_write_`（IM 写缓存） | 缓存写失败仅 debug | 升 `logger.warning` |
| `backend/auto_dm/accounts.py:1552` | `worker` | log-only-low | `_write_`（IM 写缓存） | 同上 | 同上 |
| `backend/auto_dm/accounts.py:1556` | `worker` | return-empty | `_write_`（凭证/环境写） | 写失败返回空 | 区分空/失败 |
| `backend/auto_dm/accounts.py:528` | `verify_account` | log-only-low | `_conn.execute(...)`（校验读） | 读失败仅 debug | 升 `logger.warning` |
| `backend/auto_dm/conversation_capture.py:2039` | `capture_all` | pass-only | `update`（捕获回写） | 捕获回写失败被吞 | `logger.warning` |
| `backend/auto_dm/origin_image_resolver.py:259` | `_heic_to_jpeg` | return-empty | `save`（图片转码写盘） | 转码失败返回空 → 图片缺失无线索 | `logger.warning` |
| `backend/builder/header.py:99` | `with_bd_readonly` | pass-only | `set_header(...)`（生成请求头，非落库） | 「写」语义命中的**误报**，实际是构造 header | 需人工复核；建议改判据 |
| `backend/daemon/bcc_audit.py:410` | `_do` | log-only-low | `record_kernel_truth`（写真值表） | BCC 真值落库失败仅 debug → 审计数据静默缺失 | 升 `logger.warning` |
| `backend/daemon/bcc_login.py:294` | `_do` | log-only-low | `record_baseline`（写基线表） | 基线落库失败仅 debug | 升 `logger.warning` |
| `backend/daemon/wp_protocol.py:250` | `_on_response` | log-only-low | `add_`（会话缓存 add） | 失败仅 debug | 升 `logger.debug`+上下文 |
| `backend/daemon/_verify_conv_identity.py:87` | `<module>` | log-only-low | `SELECT`（验证脚本读库） | 失败仅 debug | 升 `logger.warning` |
| `backend/dy_apis/login_api.py:850` | `refresh_cookie_from_profile` | pass-only | `save_credential`（**凭证写盘**） | **凭证刷新落盘失败被吞 → 凭证静默失效**（本文件同类事故已多次） | **必须**`logger.error` + 抛 |
| `backend/mcp/audit.py:79` | `trim` | return-empty | `write_text`（审计日志截断写） | 写失败返回空 | `logger.warning` |
| `backend/mcp/audit.py:88` | `clear` | pass-only | `write_text("")`（审计日志清空） | 清空失败被吞 | `logger.warning` |
| `backend/notify/gateway.py:85` | `ensure_bound` | return-empty | `save_config_file`（配置写盘） | 绑定失败返回空 | 区分空/失败 |
| `backend/notify/inbound.py:477` | `_handle_session_expired` | log-only-low | `set_kv_json`（会话过期状态写） | 状态写失败仅 debug | 升 `logger.warning` |
| `backend/notify/inbound.py:492` | `_save_sync_buf` | log-only-low | `save_config_file`（同步缓冲写盘） | 写失败仅 debug | 升 `logger.warning` |
| `backend/notify/inbound.py:550` | `_notify_qr` | log-only-low | `set_kv_json`（二维码状态写） | 同上 | 升 `logger.warning` |
| `backend/notify/notifier.py:96` | `_run` | pass-only | `put_nowait`（队列投递） | 队列满投递失败被吞 → 通知静默丢失 | `logger.warning` + 计数 |
| `backend/replay/sandbox.py:99` | `_reset_db_bindings` | pass-only | `reset_connection()` | 重置失败被吞（回放域） | `logger.debug`+上下文 |
| `backend/replay/sandbox.py:158` | `cleanup` | pass-only | `reset_connection()` | 同上 | 同上 |
| `backend/replay/sanitize_db.py:305` | `build` | pass-only | `d.execute(sql)`（脱敏库构建） | 构建失败被吞 | `logger.warning` |
| `backend/scripts/migrate_global_to_member.py:128` | `main` | log-only-low | `execute`（迁移脚本读） | 迁移中读失败仅 debug | 升 `logger.warning`（迁移必须可见） |
| `backend/services/db_transfer.py:246` | `import_db` | log-only-low | `conn.execute(sql)`（导入写库） | 导入失败仅 debug → 部分数据静默丢失 | 升 `logger.warning` + 计数 |
| `backend/database.py:260` | `_migrate_schema` | pass-only | `ALTER TABLE ADD COLUMN` | **幂等 DDL**（列已存在），属合理模式 → ⚠复核 | 保持；或注释说明 |
| `backend/database.py:267` | `_migrate_schema` | pass-only | `ALTER TABLE ADD COLUMN` | 同上 ⚠复核 | 同上 |
| `backend/database.py:291` | `_migrate_schema` | pass-only | `ALTER TABLE ADD COLUMN` | 同上 ⚠复核 | 同上 |
| `backend/database.py:310` | `_migrate_schema` | pass-only | `CREATE INDEX IF NOT EXISTS` | 幂等 DDL ⚠复核 | 同上 |
| `backend/database.py:316` | `_migrate_schema` | pass-only | `ALTER TABLE ADD COLUMN` | 同上 ⚠复核 | 同上 |
| `backend/database.py:305` | `_migrate_schema` | pass-only | `conn.execute(_ddl)`（动态 DDL） | 同族；但 `_ddl` 为变量，需人读确认幂等 | ⚠复核 |
| `backend/database.py:324` | `_migrate_schema` | pass-only | `CREATE UNIQUE INDEX IF NOT EXISTS` | 同上 ⚠复核 | 同上 |

---

## 3. 中低危清单（L2 / L3，按文件聚合，不逐条铺开）

### 3.1 🟠 L2-外部资源（61 处）—— 文件 + 条数

| 文件 | 条数 | 代表行号 |
|---|---:|---:|
| `backend/daemon/ws_link.py` | 8 | 见脚本 `--json` |
| `backend/dy_apis/login_api.py` | 6 | |
| `backend/vbrowser.py` | 4 | 996 |
| `backend/auto_dm/accounts.py` | 4 | |
| `backend/database.py` | 3 | |
| `backend/auto_dm/login_remote.py` | 3 | 996 |
| `backend/services/media_proxy.py` | 3 | |
| `backend/services/member_ctx.py` | 3 | |
| `backend/main.py` | 2 | |
| `backend/core/live_hook.py` | 2 | |
| `backend/daemon/bcc_routes.py` | 2 | |
| `backend/daemon/browser_daemon.py` | 2 | |
| `backend/daemon/recv_daemon.py` | 2 | |
| `backend/downloader/tasks.py` | 2 | |
| 其余 31 个文件 | 各 1 | |

> 含浏览器 `close()`/`stop()` 清理类（已并入 L2 并标「需人工复核」，合计 42 处待复核的大部分在此域）。

### 3.2 🟡 L3-内部逻辑（464 处）—— 文件 Top 20

| 文件 | 条数 | 文件 | 条数 |
|---|---:|---|---:|
| `backend/services/ai_reply.py` | 20 | `backend/services/probe.py` | 10 |
| `backend/daemon/recv_daemon.py` | 18 | `backend/utils/tls_policy.py` | 10 |
| `backend/core/auto_dm.py` | 17 | `backend/core/live_hook.py` | 9 |
| `backend/auto_dm/im_protobuf.py` | 16 | `backend/vbrowser_camoufox.py` | 8 |
| `backend/daemon/browser_daemon.py` | 16 | `backend/api/accounts.py` | 8 |
| `backend/auto_dm/origin_image_resolver.py` | 15 | `backend/dy_apis/client_user.py` | 8 |
| `backend/daemon/bcc_login.py` | 15 | `backend/mcp/tools_debug.py` | 8 |
| `backend/auto_dm/conversation_capture.py` | 13 | `backend/services/uid_probe.py` | 8 |
| `backend/auto_dm/accounts.py` | 12 | 其余 | 各 <8 |
| `backend/vbrowser.py` | 11 | | |
| `backend/api/messages.py` | 11 | | |
| `backend/services/im_video.py` | 11 | | |

> L3 为纯内存/解析/格式化，兜底危害低，属历史审计「允许至少 `logger.debug`」的域，**不阻断**。

---

## 4. Top 5 最危险位置（附真实代码片段）

### ① `backend/daemon/wp_recv.py:371` —— `conn.commit()` 失败被吞（**最危险单点**）

```python
    try:
        conn.commit()
    except Exception:
        pass
    if n_new:
        logger.info(f"[wp_recv][{account}] WP 通道新增 {n_new} 条消息")
```

**为什么最危险**：上方循环已执行了 N 条 `INSERT OR IGNORE INTO dm_messages`
与 `UPDATE dm_conversations`（见 `wp_recv.py:357-370`）。`commit()` 是这些写入
**唯一落地时点**，一旦失败被 `pass` 吞掉，**上方的所有写入全部回滚，但代码仍
继续 `logger.info("新增 N 条消息")`** —— 日志报「成功」，库里一条没有。
这正是铁律「无回执不认成功」被违反的教科书案例。

### ② `backend/daemon/recv_daemon.py:565,596` —— 会话骨架落库静默（H6 §S2 点名同处）

```python
                    conn.execute(
                        "INSERT OR IGNORE INTO dm_conversations(account,conv_id,peer_id,"
                        "peer_name,short_id,last_ts,unread) VALUES(?,?,?,?,?,?,?,?)",
                        (self.name, conv_id, peer_id, peer_name, None, 0, 0),
                    )
                    conn.commit()
                except Exception:
                    pass
            elif peer_id and not c.peer_id:
                ...
                    conn.execute(
                        "UPDATE dm_conversations SET peer_id=?,"
                        "peer_name=COALESCE(...) WHERE account=? AND conv_id=?",
                        (peer_id, _write_name, _write_name, self.name, conv_id),
                    )
                    conn.commit()
                except Exception:
                    pass
```

**为什么危险**：会话骨架是**所有会话操作的基座**。落库失败被吞后，内存里
`self.convs` 有该会话、库里没有 → 重启后会话消失、昵称永久丢失，且
**无任何日志**。2026-09-22 H6 已点名此处，5 天后**原样未改**。

### ③ `backend/dy_apis/login_api.py:850` —— 凭证刷新落盘失败被吞（**凭证链高危**）

```python
    try:
        DYLoginApi().save_credential(auth, env_path)
    except Exception:
        pass
```

**为什么危险**：本项目多次因凭证问题返工（见 `daemon/bcc_login.py` 同族）。
凭证刷新**写盘失败被完全吞掉**，上层不知道凭证没保存 → 下次启动用旧凭证
静默失效，排查成本极高。且此处是**写文件（凭证）**路径，属「写库路径」高危域。

### ④ `backend/daemon/browser_daemon.py:1227` —— 发送前查对端名失败被吞（**发送链高危**）

```python
        peer_name = None
        try:
            from database import get_db
            _conn = get_db()
            _row = _conn.execute(
                "SELECT peer_name FROM dm_conversations WHERE account=? AND conv_id=?",
                (self.account, conv_id)).fetchone()
            if _row and _row[0]:
                peer_name = str(_row[0])
        except Exception:
            pass
        if not peer_name:
            # conv_id 兜底：0:1:<uid_a>:<uid_b> 取非自身 uid 段当昵称占位
            parts = str(conv_id).split(":")
            if len(parts) == 4:
                my = str(getattr(self, "_last_uid", "") or "")
                peer_name = parts[3] if parts[2] == my else parts[2]
            else:
                return {"ok": False, "error": f"无法确定会话对象（conv_id={conv_id[:30]}）"}
```

**为什么危险**：这是 `wp_send_text`（**私信发送**）的前置步骤。查库失败被吞后
回退到「用 conv_id 拆段猜昵称」——若 `conv_id` 形态与假设不符（如群聊），
**可能把消息发给错误的对端**。对抗性爬虫项目里「发错人」比「发失败」更严重。

### ⑤ `backend/services/delivery_verify.py:127` —— 投递校验落库失败返回空（**违反"无回执不认成功"**）

```python
    try:
        from database import get_db
        conn = get_db()
        conn.execute(...)     # 写「投递已验证」回执
    except Exception:
        return None
```

**为什么危险**：本文件是**投递回执**的落库点。回执写库失败返回 `None`，调用方
若把 `None` 当作「无回执」以外的语义处理，会导致**「已校验」状态丢失**，
直接破坏铁律「无回执不认成功」的状态机。

---

## 5. 待人工复核清单（42 处）

> 判定不了的**如实列出，不拔高也不隐瞒**。脚本对以下情形打 `needs_review=True`
> （不入 F1/F2 阻断，但 `F5` 显式计数，绝不静默归入低危）：
> - **幂等 DDL/迁移**（`ALTER TABLE` / `CREATE INDEX IF NOT EXISTS`）：重复执行失败是**预期**行为，`pass` 属**合理**模式；
> - **浏览器清理**（`close()`/`stop()`）：分不清是「清理失败」（无害）还是「被吞的发送」（高危）；
> - 语义疑似（如 `builder/header.py:99` 的 `set_header` 是构造请求头，非落库）。

按文件分布（Top）：

| 文件 | 待复核条数 |
|---|---:|
| `backend/database.py` | 8（幂等 DDL 迁移族） |
| `backend/daemon/ws_link.py` | 6（清理类） |
| `backend/dy_apis/login_api.py` | 6 |
| `backend/vbrowser.py` | 3（996 `browser.close`） |
| `backend/auto_dm/login_remote.py` | 3（996 清理） |
| `backend/auto_dm/accounts.py` | 2 |
| `backend/core/live_hook.py` | 2 |
| `backend/daemon/bcc_routes.py` | 2 |
| 其余 9 文件 | 各 1（`camoufox_capture.py:192`、`link_resolve.py`、`web_probe.py`、`core/auto_dm.py` 等） |

**复核判据（建议）**：
1. 若 `try` 块**只含幂等 DDL** → 豁免（可加白名单注释）。
2. 若 `try` 块含 `close/stop` 且**不含任何 send/post/写库** → 低危（保持，可降为 `logger.debug`）。
3. 若 `try` 块**同时含 cleanup 与 send/写库** → 按 L1 处置。
4. 若语义疑似（如 `set_header`）→ 人工确认后修正词表（脚本 `WRITE_FUNC_WORDS`）。

---

## 6. 建议整改优先级 + 验收判据

### 优先级 P0（本周，风控/回执相关）

1. **`wp_recv.py:371` / `voice_transcribe.py:338` / `nickname_fallback.py:263` 的 `conn.commit()`**：
   改为 `logger.error` + 向上抛（或返回失败标志）。**提交失败必须中止流程**。
2. **`login_api.py:850` 凭证写盘**：`logger.error` + 抛；凭证未保存不得报成功。
3. **发送链 `browser_daemon.py:1227`**：查库失败必须中止发送，禁止「猜对端」兜底。
4. **`delivery_verify.py:127` 投递回执**：区分「无回执」与「写回执失败」，不得返回 `None` 混语义。
5. **删除全部 `bare-except`（2 处）**：裸 `except:` 连 `KeyboardInterrupt` 都吞，显式列异常类型。

### 优先级 P1（本月，写库/外发 `pass-only` 清零）

6. 把 **L1-写库 34 处 + L1-外发 9 处** 的 `pass-only` 全部改为**至少 `logger.warning` 带上下文**（account/conv_id/url）。
7. `dy_apis/image_sender.py:448`（抖音 API）、`notify/channels.py:427`（上报）必须**可见**。

### 优先级 P2（本季，长尾收敛）

8. L2/L3 的 `pass-only` 由 `pass` 升为 `logger.debug`（满足历史审计「至少 debug」的要求）。
9. 复核 §5 的 42 处，按判据 1-4 分类豁免或整改。

### 验收判据（机械可验）

| # | 判据 | 验收方式 |
|---|---|---|
| A1 | `check_silent_fallback.py --selftest` 退出码 0（分级判据非写死） | 已在本次审计实测通过 |
| A2 | `check_silent_fallback.py` 的 **F1 = 0 且 F2 = 0**（L1 pass-only 清零） | 重跑脚本 |
| A3 | `--baseline write` → 整改 → `--baseline check` 显示 **L1 新增 = 0** 且 L1 总数下降 | 基线文件 `artifacts/audit_2026-09-27/silent_fallback_baseline.json` |
| A4 | 同口径子集（`comparable_to_386`）**≤ 386**（回到历史基线以下） | 重跑脚本读 `summary.comparable_to_386` |
| A5 | `bare-except` 计数 = 0 | 重跑脚本读 `by_form["bare-except"]` |

---

## 7. 探测脚本源码

- **落盘路径**：
  - `C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\scripts\check_silent_fallback.py`（主名，与本项目 `check_*.py` 家族一致）
  - 同上内容的副本：`...\scripts\普查脚本_silent_fallback.py`
- **用法**：
  ```bash
  py314 scripts/check_silent_fallback.py                 # 人读 + 退出码
  py314 scripts/check_silent_fallback.py --json          # 机器可读（基线对比/防新增）
  py314 scripts/check_silent_fallback.py --baseline write # 写当前 L1 基线
  py314 scripts/check_silent_fallback.py --baseline check # 对比基线（新增 L1 则退出码非 0）
  py314 scripts/check_silent_fallback.py --selftest       # 自证「违规会报红」
  ```
- **设计要点**（详见脚本顶部 docstring）：
  1. 沿用 H6 的「handler 体」判据保证 386 可比；
  2. **读 try 块全文**（AST 行号区间）判定路径类型，而非只看 except 行；
  3. `pass-only` / `log-only-low` / `return-empty` / `bare-except` / `contextlib.suppress` 分形式统计；
  4. `--baseline` 防新增；`--selftest` 双向自证（负控命中 + 正控零命中）。

**脚本 SHA256（前 16 位）**：`d860f03cdfed1559`
**基线文件**：`artifacts/audit_2026-09-27/silent_fallback_baseline.json`（L1 键 102 个）

---

## 附录：可复现命令与关键输出

```bash
$ py314 scripts/check_silent_fallback.py --selftest
  负控1 写库 pass-only 应命中 L1-写库: ✓
  负控2 外发 pass-only 应命中 L1-外发: ✓
  正控  干净代码应零命中: ✓
✓ 自检通过：分级判据随代码内容变化，非写死

$ py314 scripts/check_silent_fallback.py
  基数   : 命中 627 处 (历史基线 386，Δ +241)
           其中与 H6 386 同口径子集 = 451 处 (Δ +65)
  [FAIL] F1   L1-写库路径 pass-only 静默兜底 = 29 处（另 5 处需人工复核，不阻断）
  [FAIL] F2   L1-外发路径 pass-only 静默兜底 = 9 处
  [PASS] F3   已写基线 / L1 新增 = 0 处
  [PASS] F4   分级口径: L1-写库 69 / L1-外发 33 / L2 61 / L3 464；pass-only: 写库 34 / 外发 9；bare: 写库 0 / 外发 0
  [PASS] F5   待人工复核 = 42 处
  ⛔ 阻断项（必须修复）：F1、F2
```

**独立口径复算**（不依赖本脚本，纯 AST，H6 原始方法学）：

```bash
$ py314 - <<'PY'
# 复算：handler 体仅 pass/continue，或仅 logger.debug
pass/continue-only: 351
debug-only       : 94
H6-comparable TOTAL: 445
vs 386 -> 59
PY
```

---
*报告生成：2026-09-27 ｜ 脚本：`DYAutoDM_v2/scripts/check_silent_fallback.py` ｜ 所有数字来自实际运行输出*
