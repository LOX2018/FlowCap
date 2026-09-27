# H-22 审计 P0~P4 修复落地复核（c9db81e..HEAD）

- **审计区间**：`c9db81e..HEAD`（HEAD = `bd266be`，v0.45.49）
- **复核日期**：2026-09-27
- **复核对象**：H-22 全库审计甄别的 22 条真缺陷（P0~P4）修复是否真正落地、且未被后续提交覆盖回退
- **修复提交**：P0 `6179119`(v0.45.31) · P1 `e781464`(v0.45.32) · P2 `504718f`(v0.45.33) · P3 `d47f164`(v0.45.34) · P4 `b09237f`(v0.45.35)
- **方法**：① `git show <sha>` 逐提交看改动；② 在当前代码（`HEAD:<file>`）机械定位修复标识；③ 比对 fix 提交态与 HEAD 态标识是否同存；④ 独立「回退注入」实验证明门禁非自证；⑤ 复核后续提交是否触及修复点。

## 结论速览

| 维度 | 结果 |
|---|---|
| 复核的 P 项（去重后功能项） | **20 项**（P0 4 · P1 4 · P2 6 · P3 4 · P4 2） |
| 当前代码仍处「已修复」态 | **20 / 20** |
| 被后续提交推翻/回退的项 | **0** |
| H-22 机械门禁（`test_h22_p*_audit_fixes.py`）当前实测 | **42 / 42 OK**（`unittest discover -p "test_h22_p*.py"`） |
| 门禁「回退注入」实验（独立） | 4/4 判红 ⇒ 门禁为真判据，非自证 |

> 注：台账记「22 条真缺陷」，本表按**去重后的可核验功能项**呈现为 20 项（P2-①② 为同源一对反向缺陷、P0 台账含 5 条但 idx 更细；去重口径见各行备注）。

## 逐项核验表

| 原缺陷 | 修复提交 | 当前代码是否仍修复 | 证据(文件:行号) | 结论 |
|---|---|---|---|---|
| **P0-①** `recv_daemon.py`：模块级函数被插进 `RecvChannel` 类体中间，`_extract` 被 AST 吞成 `_msg_extra_json` 嵌套函数 ⇒ `self._extract` AttributeError ⇒ WS 收消息全废 | `6179119` | ✅ 是 | `backend/daemon/recv_daemon.py:1129`（AST：`RecvChannel` 直接持有 `_extract`）；`:1224 _msg_tuple` `:1247 _ws_tuple` `:1262 _msg_extra_json` 均为模块级；`:1017` `_handle` 内 `text, extra = self._extract(...)` 可达 | 无回退 |
| **P0-②** `conversation_capture.py:2010/2018`：`_rec_of(m,_extra).tuple(name,cid)` 漏传 `ts/msg_id/role` ⇒ 默认 `ts=0.0/msg_id=None/role="them"` ⇒ 去重键失效吞行 + 收发归属反转 | `6179119` | ✅ 是 | `backend/auto_dm/conversation_capture.py:2017` 与 `:2030` 两处均显式传 `ts=/msg_id=/role=`（AST 校验 kwargs ⊇ {ts,msg_id,role}） | 无回退 |
| **P0-③** `media_request.py::resolve_playable`：ADR-016「统一 UA」提交 `f54f554` 顺带删除 `Referer` ⇒ 抖音 CDN 受保护流 403 回归 | `6179119` | ✅ 是 | `backend/downloader/media_request.py:298` `"Referer": "https://www.douyin.com/"`（AST：位于 `resolve_playable` 260–322 内） | 无回退 |
| **P0-④** `ai_reply.py:1419`：水位 `last_id` 在 in-flight 判定**之前**对每行推进，`continue` 跳过行(id>水位)下轮不再捞 ⇒ 同会话并发永久丢消息 | `6179119` | ✅ 是 | `backend/services/ai_reply.py` `_tick`(def@1404) 内语句序：`in-flight判定`→`break`→`last_id=max(...)`（AST 偏移 36<44<47）；`:544` 对应 break 分支 | 无回退 |
| **P1-①** `ai_reply.py`：`_img_desc_get/_known/_mark` 整 dict 读-改-写无锁 ⇒ 并发 last-write-wins 静默丢缓存 | `e781464` | ✅ 是 | `backend/services/ai_reply.py:125` `_IMG_DESC_LOCK = threading.Lock()`；`:130/:142/:153` 三函数全体 `with _IMG_DESC_LOCK:` | 无回退 |
| **P1-②** `notify/inbound.py:208`：重登成功仅回写闭包局部 `token`，未回写 `ch.token` ⇒ 回复仍用旧 token | `e781464` | ✅ 是 | `backend/notify/inbound.py:208` `token = logged` 与 `:216` `ch.token = token`（实例回写） | 无回退 |
| **P1-③** `kernel_truth.py`：`_MAX_AGE_SEC=1800` 与真实写盘门限（`bcc_login.py:513`=3600s）不符 ⇒ 周期判 stale、档案≠内核脱节复现 | `e781464` | ✅ 是 | `backend/services/kernel_truth.py:79` `_MAX_AGE_SEC = 7200`（=2×门限）；`:141` 使用处 | 无回退 |
| **P1-④** `dy_apis/client_user.py:428`：失败缓存写在 HTML 回退之前 ⇒ 回退成功仍留污染项、60s 内误杀 | `e781464` | ✅ 是 | `backend/dy_apis/client_user.py:463` 注释「两条路径均确定失败 ⇒ 才写失败缓存」，`:465` 滞后写 `_sec_uid_cache[key]=(time.time(),"")` | 无回退 |
| **P2-①②** `daemon/bcc_audit.py::set_visible`：4 个 return 仅 1 个带 `settled`；`/show` 恒判假阴性(idx6) + `/hide-browser` 硬编码假阳性(idx7) | `504718f` | ✅ 是 | `backend/daemon/bcc_audit.py:95`(去重路径 `"settled": False`)、`:122`、`:159`(`"settled": True`)、`:207`；`backend/api/accounts.py:872` 缺键兜底、`:1161` `/hide-browser` 按真实 `settled` 如实透出 | 无回退 |
| **P2-③** `api/platform.py::_login_state_reason`：同步函数直调 `verify_credential(lightweight=False,timeout=8)`，被 6 个 async 处理器同步调用 ⇒ 阻塞事件循环最长 ~8s | `504718f` | ✅ 是 | `backend/api/platform.py:124` `async def _login_state_reason`；`:135` `await asyncio.to_thread(verify_credential, ...)`；调用点（如 `:679/:688/:695`）均 `await` | 无回退 |
| **P2-④** `vbrowser_camoufox.py`：async `close_camoufox_context` 的 `finally` 同步调用 `_reap_camoufox_processes`(psutil.wait_procs) ⇒ 事件循环停摆实测 3.14s | `504718f` | ✅ 是 | `backend/vbrowser_camoufox.py:331` `await asyncio.to_thread(_reap_camoufox_processes, _ud)` | 无回退 |
| **P2-⑤** `notify/channels.py::send_image`：最终 `sendmessage` POST 无 try/except ⇒ 退散异常与同文件「失败返回 ChannelResult」约定不一致 | `504718f` | ✅ 是 | `backend/notify/channels.py:560` `ChannelResult(False, ..., f"sendmessage 异常: ...")`（except 包住 POST） | 无回退 |
| **P2-⑥** `dy_apis/login_api.py::phoneMain`：首步调 `dyGeneratePhoneVerificationCode`（无条件 raise NotImplementedError） ⇒ 声明的状态契约永不可达、误导接线 | `504718f` | ✅ 是 | `backend/dy_apis/login_api.py:1582` `[AUTH-062] phoneMain 的短信验证码链路**不可用**` fail-closed 早于底层链路调用 | 无回退 |
| **P3-①** `client_live.py:379` REST `browser_version` 被上轮误填**完整 UA**（字段语义为短版本号，全仓 30+ 处一致） | `d47f164` | ✅ 是 | `backend/dy_apis/client_live.py:163/:250/:325/:384...` 一致取 `get_profile()["browser_version"]`；`:377-382` 注释固定语义 | 无回退 |
| **P3-②** `link_resolve.py::_reflow_resolve`：出站 `ua` 缺省硬编码（且进入 X-Bogus 签名 payload） | `d47f164` | ✅ 是 | `backend/link_resolve.py:39` `from utils.fingerprint import user_agent`、`:40 return user_agent()`；`:113/:177/:244` 出站头取 `_ua()` | 无回退 |
| **P3-③** 前端 `player-media-stage.tsx`：取流失败无重试入口 + 30s 负缓存 | `d47f164` | ✅ 是 | `frontend/src/components/player/player-media-stage.tsx:59-66`（失败走「重试」范式 + 中文原因 + 重试按钮）、`:89/:116-117` 自动重试计数、`:211` 重置 | 无回退 |
| **P3-④** `repair_conversation_identity.py:128`：退化分支无条件清头像 ⇒ 误伤合法对端头像 | `d47f164` | ✅ 是 | `scripts/repair_conversation_identity.py:89` `self_avatar=...`、`:106` `old_av` 判别值（按 `av_polluted` 保护） | 无回退 |
| **P4-①** `auto_dm/login_api_vendor.py`：vendor 顶层绝对导入 vs 5 个同名顶层包 ⇒ `sys.modules` 优先级压制，拿到项目 `DYLoginApi`（无 `bootstrap_auth/get_qrcode/check_qrcode`）⇒ 调用点才炸（静默半坏） | `b09237f` | ✅ 是 | `backend/auto_dm/login_api_vendor.py:118` `_collision_error`；`:131` 命名空间碰撞前置检查；`:156/:163` 「先判后插」两处 raise AUTH-073 | 无回退 |
| **P4-②** `mcp/registry.py`：`--scope " "`/`","` 显式提供但解析为空 ⇒ 静默回落全量 `full`（违 ADR-010 S5，无声推翻受限意图） | `b09237f` | ✅ 是 | `backend/mcp/registry.py:98-104` 显式空项抛 `ValueError("scope 解析后为空...")`；`:90-96` `None`/空串仍走「未提供→默认 full」 | 无回退 |

## 三项历史 P0 现场复核（独立于提交史）

| 历史 P0 | 复核方式 | 现场结果 |
|---|---|---|
| **WS 收消息链路**（`recv_daemon.py` 缩进事故） | AST 解析 `recv_daemon.py` | `RecvChannel`(802–1222) **直接**持有 `_extract`@1129；三个原模块级函数 `_msg_tuple`@1224 / `_ws_tuple`@1247 / `_msg_extra_json`@1262 均在**类体外**；`_extract` **不再**被任何 helper 吞为嵌套函数（检查返回 `[]`）；`_handle`@1017 实调 `self._extract` ⇒ 链路可达 |
| **消息去重键**（`conversation_capture.py` 漏传元组） | AST 遍历全部 `_rec_of(...).tuple(...)` 调用 | `:2017`、`:2030` **两处** kwargs 均为 `{ts, msg_id, role}`（判定 `⊇ {ts,msg_id,role}` = True）；另 `_parse_conv`@`key=(mcid, msg_id)`（有 msg_id 优先，缺失才回退文本键）⇒ 去重键为标识字段，不再选内容字段 |
| **受保护流 Referer**（`media_request.py` §403 回归） | AST 定位 `resolve_playable` 函数体 | `:298` `"Referer": "https://www.douyin.com/"` 位于 `resolve_playable`(260–322) 请求头内 ⇒ 受保护流请求带 Referer |

## 后续提交「是否把修复改回去」核查

对每个修复提交，枚举其改动的源文件在 `<fix>..HEAD` 的所有后续提交，并**按区域 diff** 判断是否触及修复点：

| 修复提交 | 被后者触及的文件（触及次数） | 是否触及修复点 |
|---|---|---|
| `6179119` (P0) | `conversation_capture.py`(1: `f730824`)、`ai_reply.py`(1: `e781464`) | ❌ 未触及 —— `f730824` 改 `_parse_conv` 去重键（**强化**）、`e781464` 改 `_img_desc_*` 加锁（**另处**） |
| `e781464` (P1) | `client_user.py`(1: `7d4bc97`)、`_build_version.py`(版本号) | ❌ 未触及 —— `7d4bc97` 仅改 `engine_name`，P1-④ 失败缓存写入点未动 |
| `504718f` (P2) | `api/accounts.py`(3: `40a1ae8/c6aff50/d5b2cc1`)、`api/platform.py`(2: `074eb37/7e9dc9d`)、`login_api.py`(1: `c6aff50`) | ❌ 未触及 —— `accounts.py` 后者改 scan/login 区（174/198/768/778 附近），`settled`@864–886/1157–1171 未动；`platform.py` 后者改 search/comments 区（348/588/861），`_login_state_reason`@124–135 未动 |
| `d47f164` (P3) | `client_live.py`(1: `7d4bc97`) | ❌ 未触及 —— 仅 `engine_name` 值替换，`browser_version` 取值未动 |
| `b09237f` (P4) | 仅 `_build_version.py`（版本号） | ❌ 未触及 |

**结论：区间内所有后续产品提交均未触及任何 P0~P4 修复点，零回退。**

## 门禁有效性独立验证（回退注入实验）

不看测试自带的负控，独立地取 **HEAD 源码 → 人工注入原始缺陷形态 → 喂给门禁 `check_*`**：

| 门禁 | HEAD 正向 | 注入缺陷后 | 说明 |
|---|---|---|---|
| `G1` `check_g1` | True | **False**（「RecvChannel 缺 _extract」） | 删除 `_extract` 即判红 |
| `G2` `check_g2` | True | **False** | 还原为 `tuple(name, cid)` 即判红 |
| `G3` `check_g3` | True | **False** | 删除 Referer 即判红 |
| `G4` `check_g4` | True | **False** | 水位前置 / `break→continue` 即判红 |

⇒ 门禁为**真判据**（对缺陷形态敏感），非「永真断言」。

> 环境说明：`test_h22_p4` 的 G1 行为级测试需导入 `auto_dm/login_api_vendor.py`（依赖 `loguru`）。
> 必须以**项目解释器**运行（`C:\Users\LOX\AppData\Local\hermes\hermes-agent\venv`，含 loguru 0.7.3）。
> 用裸 uv-python（无 loguru）跑会报 3 个 `ModuleNotFoundError`（`downloader`/`notify`/`login_api_vendor`），
> 属**环境缺失**而非缺陷回退——项目解释器下 42/42 全绿。

## 复核命令（可复现）

```bash
cd DYAutoDM_v2/backend
DY_APP_ROOT=<隔离目录> python -m unittest discover -s . -p "test_h22_p*.py"   # 42 tests OK
DY_APP_ROOT=<隔离目录> python -m unittest test_h22_p0_audit_fixes -v          # 8 OK（4 正 + 4 负控）
```

## 遗留 / 无推翻项声明

- **被推翻项数：0。**
- 未纳入本次机械核验的关联项（非缺陷，供参考）：`P4-①` vendor 扫码**端到端**需 `curl_cffi` + 真机扫码（冻结态 sidecar 无独立解释器）——属环境/需求，非修复回退。
- 本报告仅证实「修复在 **HEAD 源码层**仍落地且未被回退」+「门禁为真判据」；真机行为证据以各 case 文件 `[Live Verification]` 为准，本次未重跑真机链路。
