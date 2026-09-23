# E 线核查卷宗 · E_upstream_write_pending.md

- 被审报告：`C:/Users/LOX/Desktop/DYchajian/artifacts/fix_20260923/E_upstream_write_pending.md`（实测 **616 行**，与任务书一致）
- 仓库：`C:\Users\LOX\Desktop\DYchajian`　分支：`design/better-douyin`
- 实测 HEAD：`b455192`　版本：`0.44.53`（四处版本源实测一致）——与报告自述一致
- 审查纪律：**只读仓库**；未 git add/commit/checkout/rm；未修改仓库任何文件；**未跑测试**（仅对磁盘文件做 sha256/grep/AST 级只读核对）；唯一写入为本文件
- 核查时间：2026-09-23　终端：bash(MSYS)

> 任务书背景提示：本线报告头部（行 3–4）明写「本轮**明确不真发**，端到端实机验证一律标注 `pending`，未执行、未通过」⇒ 其结论**天然不完整**。本卷宗按此前提核定「结论上限」。

---

## 1.【自述改动文件清单】

报告 §六（行 573–602）自述：

### 1.1 修改（2 个，自述为「本任务独占文件」）

| # | 报告自述文件 | 改动要点 | 工作区实测 |
|---|---|---|---|
| 1 | `DYAutoDM_v2/backend/dy_apis/client_live.py` | `sendMsgInRoom` 对齐上游 `251075e`：Origin→`live_url`、`+**kwargs`、referer/`web_rid`/`enter_from`/`type`、7 可选 query、`str()` 化 | ` M`，`git diff --numstat` = **39 +/ 7 −**；内容实测与自述一致（见 §5） |
| 2 | `DYAutoDM_v2/backend/dy_apis/client_comments.py` | `publish_comment` 对齐上游 `df52357`：celltime 去随机默认 0、`text_extra`→JSON.stringify、`+reply_to_reply_id`、`+one_level_comment_rank`/`paste_edit_method` | ` M`，`git diff --numstat` = **48 +/ 10 −**；内容实测与自述一致（见 §5） |

### 1.2 新增（1 个）

| 文件 | 报告自述 | 工作区实测 |
|---|---|---|
| `DYAutoDM_v2/backend/test_upstream_write_align_t1_t2.py` | 40 用例（T1/T2 契约 + 签名一致性 + 负控 + 源码门禁 + 上游逐字对账） | **未跟踪 `??`**，文件存在，**817 行**，`grep -c "def test_"` = **40** ✓ |

⇒ `files_claimed = 3`（2 改 + 1 增）。

### 1.3 「明确未触碰」清单（行 595–602，逐条实测）

| 报告自述未触碰 | 实测 |
|---|---|
| 6 处版本源（`_build_version.py`/`package.json`/`frontend/package.json`/`Cargo.toml`/`Cargo.lock`/`tauri.conf.json`） | `_build_version.py`、两份 `package.json`、`tauri.conf.json`、`Cargo.toml` 实测均 = `0.44.53`，**未被 E 线改动** ✓ |
| `工作记忆/` 任何文件 | 报 ` M 工作记忆/00_交接卡待办台账.md`（6+/4−）——该改动与 E 线无关联（D 线 T3-a/F-8 自述触及；见 D_verify §1.1）。E 线报告未将其列入自己清单，**归因需父会话确认**（见 §6·R9） |
| `artifacts/UP_*` | 未见 E 线报告列入；`?? artifacts/UP_L1_…` 等为未跟踪文件，非本线产出 |
| 未 `git add`/`commit`/`checkout`/`stash`/`clean` | `git diff --cached --name-only` 实测**仅** `_ext_repos/DouYin_Spider_git` 一项（D 线 F-7 所为），E 线三文件均**未进暂存区** ✓ |

> 报告另称负控用「`git show b455192:<path>` 读旧文本临时写回 + 原样还原」，并声明未用任何 git 写命令。工作区两文件当前 sha256 与报告「改动后」值一致（§5），无法从静态侧证明中间态是否发生过回退，但**终态正确**。

---

## 2.【自述测试数字】（每个 `Ran N`/`OK`/`FAILED` 逐条，标注 pending）

| 行号 | 原句（逐字） | 性质 | pending? |
|---|---|---|---|
| 405 | `Ran 40 tests in 2.048s` | E 线新增模块 `test_upstream_write_align_t1_t2` 隔离运行 | 测试本身**无 skip**；但**端到端真发为 pending**（行 212/383/495–496） |
| 407 | `OK` | 同上（40 全绿） | 同上 |
| 430 | `→ \`Ran 69 tests … OK\`。` | 无回归旁证：复跑既有守卫 `test_abogus_host_guards test_no_dup_dict_keys test_no_loguru_printf_style test_upstream_p1 test_upstream_p5` | 无 pending。**报告自述，未独立复现** |
| 444 | `回退成旧写法 → 跑测试 \| \`1\` \| **RED — \`FAILED (failures=15, errors=8)\`**` | 负控（旧写法必须变红） | 无 pending。**报告自述，未独立复现** |
| 445 | `还原 → 再跑测试 \| \`0\` \| **GREEN — \`OK\`（40 tests）**` | 负控还原后复绿 | 无 pending。**报告自述，未独立复现** |

**测试项计数核对（静态可核）：** 报告 §三 分组表（行 410–420）自述

| 组 | 报告条数 | 磁盘实测 `def test_` |
|---|---|---|
| `TestT1SendMsgInRoom` | 8 | **9** |
| `TestT2PublishComment` | 9 | **10** |
| `TestT2SignatureConsistency` | 4 | 4 |
| `TestNegativeControl` | 5 | 5 |
| `TestSourceGates` | 7 | 7 |
| `TestUpstreamParity` | 5 | 5 |
| **合计** | **报告写 40** | **实测 40** |

⇒ 总条数 **40 正确**，但报告分组表**自身相加 = 38 ≠ 40**（T1 少记 1、T2 少记 1）。数字瑕疵，见 §6·R10。

**待审报告 `Ran N`/`OK`/`FAILED` 全部来源**：`grep -nE "Ran [0-9]+|OK\b|FAILED|pending"` 实测命中 405/407/430/444/445（见上）+ 行 4/212/383/487/495/496/615 的 `pending` 标注。`pending` 出现 **6 次**。

**关键：pending 与测试的耦合**——`test_e2e_flags_still_false`（测试文件行 764–767）断言
`E2E_LIVE_DELIVERY_VERIFIED is False` 与 `E2E_COMMENT_PUBLISH_VERIFIED is False`
（定义于测试文件行 62–63，实测均 `False`）。**即：该测试主动把「端到端未验证」钉成常量** —— 40 个用例**没有一个是端到端投递验证**，全部止于「请求构造层」。

> 报告 §三（行 428–430）自述曾复跑 69 个既有守卫——本卷宗**未跑测试**（铁律），该 69 数字**未独立复现**，仅记自述。

---

## 3.【唯一标识符】（逐条实测）

| 标识符 | 报告值 | 实测 | 判定 |
|---|---|---|---|
| HEAD | `b455192` | `git rev-parse --short HEAD` = `b455192` | ✓ |
| 分支 | `design/better-douyin` | `git branch --show-current` = `design/better-douyin` | ✓ |
| 版本 | `0.44.53` | 4 处版本源均 `0.44.53` | ✓ |
| 上游文件 sha256 | `5b92eff588184a60bf7b4d313559dd08bcaacb3320bcb30710ca782da39a62d7` | `sha256sum _ext_repos/DouYin_Spider_git/dy_apis/douyin_api.py` = **完全一致** | ✓ |
| 上游目录 `DouYin_Spider-master` sha(12) | `788701051ff9` | `sha256sum` 前 12 位 = `788701051ff9` | ✓ |
| 上游 HEAD | `4479ea784bf3e63e75fcbe4ca985f84678d46b27`（Merge PR #91） | `git -C … log --oneline -3` 首行 = `4479ea7 Merge pull request #91 …` | ✓ |
| T1 来源 commit | `251075ec31af4eeba86d74dc34bbd61c669a9c94` | `log --oneline -3` 含 `251075e feat: align live room comment sending` | ✓ |
| T2 来源 commit | `df52357d569d3c9a7d691dc895c13fc1aecd2620` | `log --oneline -3` 含 `df52357 fix: align work comment publishing` | ✓ |
| 上游 `douyin_api.py` 行数 | 报告表格写 2772 | `wc -l` = **2771** | ✗ 差 1 行（尾行无换行计法差异，见 §6·R10） |
| 上游旧快照行数 | 报告写 2047 | `wc -l` = **2046** | ✗ 差 1 行（同上） |
| `client_live.py` sha256（改动后） | `f8b1459015d535a048f410fa1beeb616cdabb2532ad48c45f51ad072db0e4b82` | `sha256sum` = **完全一致** | ✓ |
| `client_comments.py` sha256（改动后） | `af72aebff78e912c09bf1c2e7936e6e5d97320deecb6386efa8a07629f279365` | `sha256sum` = **完全一致** | ✓ |
| 新增测试文件 sha256 | `38a14291785aa226c2896b910f79e1e06b8bdf1782a196f660720208c0ccab8a` | `sha256sum` = **完全一致** | ✓ |
| 报告自述行号引用 | `client_live.py:598-628`（pre-fix）、`client_comments.py:236-298`（pre-fix） | 与磁盘现文（改后行号偏移）**方向一致** | ✓ |

⇒ **辨识符核对：报告给出的 3 个 sha256（2 码文件 + 1 测试文件）与 2 个上游 sha，实测全部逐字吻合**；两处行数各差 1（`wc -l` 与「行数」口径差异，非事实性错误）。

---

## 4.【诚实标注】（逐条抄录 pending / 未验证项）

**头部总声明（行 3–4）**
> 「**本轮性质**：**代码对齐 + 离线可验证证据**。用户本轮**明确不真发**，故 **端到端实机验证一律标注 `pending`，未执行、未通过**。本文件不声称任何「已跑通」。」

**T1/T2 端到端未执行（行 212、383）**
> 「### ⑥ 实机端到端验证 —— **未执行（pending）**」
> 「> **本轮未发任何弹幕。** 不声称「已投递」，也不声称「已修复线上行为」。」
> 「> **本轮未发任何评论。** 不声称「已投递」。」

**§5.1 状态表（行 491–501，逐条）**
| 项 | 报告状态 |
|---|---|
| 代码对齐（T1 / T2） | ✅ 已落盘 |
| 离线请求构造层测试 | ✅ 40/40 绿 |
| **真实发送弹幕（T1）** | ❌ **未执行 —— pending 用户下次真实场景** |
| **真实发布评论（T2）** | ❌ **未执行 —— pending 用户下次真实场景** |
| 服务端是否接受新参数（`enter_from=link_share`、`celltime=0`、`text_extra='[]'`） | ❌ **未知**（本轮无实测数据，**不得**当作已验证） |
| `dtrait` 能力缺口（评论发布可能被风控拦成 200+空 body） | ⚠️ **未解决**（本项目无 dtrait 来源；`safe_json` 会把空响应降级为 `{}` ⇒ 上层可能**假成功**） |

> 「> **不得声称「已端到端验证」。** 本轮结论上限是：「**请求构造与上游逐字段一致，且离线可证明旧写法确实不一致**」。」（行 500–501）

**§一③ 空操作自证（行 196–201）**——对 `with_bd(..., origin=live_url)` 的**不夸大**声明：
> 本项目 `builder/header.py:20-22` 的 `with_bd` **签名收 `origin`，但函数体从不使用它**……⇒ 这句是「形态对齐」，**不得**据此宣称「证书已按直播域生成」。

**§二⑤ 不移植硬门禁（行 370–381）**
> 「⇒ **照抄会让 `publish_comment` 每次必抛**（静态可证），故**不移植**；本轮用测试把它**钉死为缺席**（`test_no_unintended_hard_gates`）」

**§5.3 同链路既有缺陷（范围外，本次未改，行 559–569，逐条）**
1. `splice_url` 对 `/` 未按上游编码（`utils/dy_util.py:182-188` 用 `quote(str(value))` 保留 `/`）⇒ 正文含 `/` 时签名输入 ≠ 上线字节。**本次未改**。
2. 评论发布缺 4 项上游基线前置（UP-L1 A2-10）：`parse_aweme_id`、`uifid`、`_comment_uid`→query `uid`、`round_trip_time=50`。本项目**全无**。**本轮未做**。
3. `check_risk_response` 未移植：写接口被风控拦成「200 + 空 body」时 `safe_json` 降级为 `{}`，上层可能读到**假成功**。**本轮未做**。

**§六 未触碰（行 595–602）**
> 「**未改版本源**……升版 `0.44.54` 属任务包 §一·3 的**独立步骤**……**不在本任务授权内**。」
> 「未改 `工作记忆/` 任何文件；未改 `artifacts/UP_*`。未 `git add` / `commit` / `checkout` / `stash` / `clean`。」

**§5.2 调用语义留待实机确认（行 533–536）**
> 「你项目里 `web_rid`（`core/auto_dm.py:487-508` 从 live_url 解析）与 `room_id` 是否等价，**这一条仍需你实机确认后再写死**（UP-L1 已标 `[待验]`）。」

---

## 5.【交叉判据】（只读 grep / sha256 实测，附命令 + 命中数）

| 核验项 | 命令 | 实测结果 | 判定 |
|---|---|---|---|
| **T1：`sendMsgInRoom` 是否已改 Origin→`live_url`** | `grep -n "Origin" dy_apis/client_live.py` + `sed -n '600,662p'` | `630: headers.set_header("Origin", DouyinAPI.live_url)`；`631: headers.with_bd(api, auth, origin=DouyinAPI.live_url)`；`grep` 全文件**已无** `headers.set_header("Origin", DouyinAPI.douyin_url)`（源码门禁测试亦钉 `assertNotIn`） | **✓ 成立** |
| T1 附核 | `sed -n '640,662p'` | `enter_from` 默认 `'link_share'`（行 640）、`room_id` 已 `str()`（行 649）、`type` = `str(kwargs.get('type','0'))`（行 651）、7 项可选参数 for 循环（行 652–656）、`verify=tls_verify()` 保留（行 659）、末尾 `return safe_json(res)`（行 660） | **✓ 与自述一致** |
| **T2：`publish_comment` 是否去随机 celltime** | `grep -n "random" dy_apis/client_comments.py` | **唯一命中 = 行 254 的注释文本**（解释旧写法），**无 `import random`、无 `randint` 调用**；`grep -n "^import\|^from" \| grep -i random` 无输出 | **✓ 成立** |
| T2 附核（celltime→0 / text_extra JSON / reply_to_reply_id） | `grep -n "celltime\|text_extra\|reply_to_reply_id"` + `sed -n '305,340p'` | `319/320: kwargs.get('comment_send_celltime', 0)` / `(…video_celltime, 0)`；`314–316: reply_to_reply_id` 可传/空串不发；`327–330: text_extra = kwargs.get('text_extra', [])` + `isinstance(str)` 分支 + `json.dumps(..., ensure_ascii=False, separators=(',', ':'))`；`334: requests.post(..., data=data, ...)` | **✓ 与自述一致** |
| **测试文件是否存在 + 用例数** | `wc -l` + `grep -c "def test_"` + AST 分组统计 | 文件**存在**（未跟踪），**817 行**，**40 个 `def test_`**，6 个 TestCase 类：T1=9 / T2=10 / SigCons=4 / NegCtrl=5 / SourceGates=7 / Parity=5 | **✓ 存在；总数 40 自洽**（分组表 38≠40，见 §2/§6·R10） |
| 负控唯一标识符存在性 | `grep -c "def <name>"` × 10 | `test_full_contract`(2)、`test_t1_live_origin_in_source`(1)、`test_t1_optional_key_tuple_matches_upstream`(1)、`test_upstream_enter_from_default_matches_ours`(1)、`test_referer_default_and_override`(1)、`test_web_rid_only_affects_referer`(1)、`test_celltime_default_zero`(1)、`test_text_extra_default_is_json_string`(1)、`test_t2_no_random_celltime_in_source`(1)、`test_upstream_celltime_and_text_extra_literals_match_ours`(1) | **✓ 全部在场** |
| **sha256（报告给的 2 个码文件）** | `sha256sum`（见 §3） | `client_live.py` = `f8b1459015d5…4b82` ✓；`client_comments.py` = `af72aebff78e…9365` ✓ | **✓ 一致** |
| sha256（测试文件） | `sha256sum` | `38a14291785a…ab8a` ✓ | **✓ 一致** |
| 上游依据 sha256 | `sha256sum _ext_repos/DouYin_Spider_git/dy_apis/douyin_api.py` | `5b92eff58818…a62d7` ✓ | **✓ 一致** |
| §四「旁证：仅 `sendMsgInRoom` 与 `diggLiveRoom` 落主站」 | `grep -n 'douyin_url' dy_apis/client_live.py` | `571: headers.set_header("origin", DouyinAPI.douyin_url)`（**diggLiveRoom 仍在主站，未修**）；`sendMsgInRoom` 已不在其列 | **✓ 旁证成立**（diggLiveRoom 残留 = R8） |
| §5.3·3 `check_risk_response` 全仓 0 命中 | `grep -rn "check_risk_response" --include=*.py DYAutoDM_v2/` | **0 命中**（仅报告文本与测试 docstring 提及） | **✓ 成立** |
| §5.3·1 `splice_url` 用 `quote(str(value))` | `sed -n '175,195p' utils/dy_util.py` | `splice_url_str += key + '=' + urllib.parse.quote(str(value)) + '&'`（**确未 `safe=''`**） | **✓ 成立** |
| §5.3·2 4 项前置缺失 | `grep -n "parse_aweme_id\|uifid\|_comment_uid" dy_apis/client_comments.py` | `client_comments.py` 内 **0 命中**（`uifid` 在 `builder/params.py` / `image_sender.py` 等他处存在，非评论域） | **✓ 成立** |
| §一③ `with_bd` origin 是否空操作 | `sed -n '20,90p' builder/header.py` | 签名 `def with_bd(self, api, auth, aid=6383, origin='...', …)`；函数体**仅**在 `hasattr(auth,"session_dtrait_header")` 分支（行 65–66）把 `origin` 传给 `session_dtrait_header`；主证书路径 `generate_bd_ticket_client_data(api, auth.ticket, auth.ts_sign, auth.private_key)`（行 53）**不收 origin** | **⚠️ 报告结论「空操作」在当前 auth 下成立但表述不够精确**（见 §6·R11） |
| §二⑤ `DouyinAuth` 无 dtrait/ticket 能力 | `hasattr(DouyinAuth, …)` 实测 | `['ticket_matches_session','dtrait_blob','dtrait_profile','session_dtrait','session_dtrait_header']` → **`[]`（全 False）** | **✓ 成立** |

---

## 6.【疑点】

**R1 · T1 端到端真发 pending（行 212、495）** — 未发任何弹幕；「服务端是否接受 `enter_from=link_share`」为**未知**。结论上限仅到「请求构造层对齐」。

**R2 · T2 端到端真发 pending（行 383、496）** — 未发任何评论；同上。

**R3 · 服务端接受度未知（行 497）** — 报告自陈「本轮无实测数据，**不得**当作已验证」。

**R4 · `dtrait` 能力缺口未解决 → 假成功风险（行 498）** — 本项目无 dtrait 来源，`safe_json` 会把风控空 body 降级为 `{}`，上层可能读到**假成功**。报告已如实标注，风险**仍开放**。

**R5 · `splice_url` 对 `/` 未编码（行 561-564）** — 正文含 `/` 时签名输入 ≠ 上线字节，**本次未改**（跨链路，`utils/dy_util.py` 不在独占文件内）。**仍开放**。

**R6 · 评论发布缺 4 项上游前置（行 565-567）** — `parse_aweme_id` / `uifid` / `_comment_uid` / `round_trip_time=50` 本项目全无；实测 `client_comments.py` 0 命中。若将来真启用评论发布，这 4 项是**前置条件**。**仍开放**。

**R7 · `check_risk_response` 未移植 → 假成功风险（行 568-569）** — 与 R4 同源（风控 200+空 body 被 `safe_json` 吃掉）。**仍开放**。

**R8 · 兄弟方法 `diggLiveRoom` 仍用主站 Origin（未修）** — 报告行 154 已披露「同文件 8 个直播接口全部 `live_url`，只有 `sendMsgInRoom` 与 `diggLiveRoom`（:571）落主站」。实测行 571 仍 `Origin = DouyinAPI.douyin_url`。E 线**只修了 `sendMsgInRoom`**，同族缺陷 `diggLiveRoom` **残留**（报告披露但未修，属独立工作线）。

**R9 · 5 条上游对账测试依赖「未入库的上游快照」（可复现性风险）** — `TestUpstreamParity` 以 `@unittest.skipUnless(os.path.isfile(_UPSTREAM))` 门控，而 `_ext_repos/DouYin_Spider_git` 现已被**从索引移除并 gitignore**（`git status` 实测 `D  _ext_repos/DouYin_Spider_git`；`.gitignore:82` 新规则忽略之）。⇒ 在**他人 clone / 新工作区**中该目录不存在，这 5 条「上游逐字对账」会**静默 skip**（40 → 35），E 线最强的那部分证据**不可复现**。风险**仍开放**。

**R10 · 报告内数字瑕疵（文档级）**
- §三 分组表（行 410–420）各行相加 = **38**，但合计栏写 **40**；磁盘实测 T1=**9**、T2=**10**（报告记 8/9）。⇒ 报告分组表**少记 2 条**。
- §〇 表格（行 16–20）报上游 `douyin_api.py` 行数 **2772** / 旧快照 **2047**，`wc -l` 实测 **2771** / **2046**（各差 1，通常为末行无换行符的计数口径差异）。
- 以上均**不影响**「总数 40」「sha256 一致」两项硬结论。

**R11 · 「`with_bd` origin 为空操作」表述不够精确（行 196–201）** — 报告写「签名收 `origin`，但函数体从不使用它」。实测函数体**有**一处使用 `origin`：`hasattr(auth,'session_dtrait_header')` 分支内 `auth.session_dtrait_header(api, aid=aid, origin=origin, …)`（`builder/header.py:65-66`）。因 `DouyinAuth` 实测无该属性（`hasattr` 全 False），该分支当前**不进入** ⇒ **报告结论（当前实现下为空操作）成立**，但「从不使用」的措辞过于绝对。属**表述精度**问题，非事实性错误。

**R12 · 工作区并发写者与 E 线归属** — 工作区共有 **50 个 ` M` 文件 + 27 个 `??`**（7 条线并行未提交），其中 `工作记忆/00_交接卡待办台账.md`、`scripts/verify_live_strategy_live.py`、`artifacts/` 多个文件由**其他执行体**改动。E 线三文件（2 改 + 1 增）在 `git diff --numstat` 中**可单独归因**，且与报告自述**逐字吻合**；但父会话提交前仍须核归属（**报告已自限「未 git add」**）。

**R13 · 测试结论为自述、本卷宗未独立复现** — 依铁律「绝不跑测试」，`Ran 40`/`Ran 69`/`FAILED(failures=15,errors=8)` 均为**报告自述**；本卷宗可独立核对的只有：模块**在场**、**40 个用例**、sha256**一致**、断言源码**在场**、隔离标志**为 False**。

---

## 核查结论摘要

- **自述文件清单**：修改 2 + 新增 1 = **3**；工作区 `--numstat` 与报告自述方向逐字一致，三文件均可单独归因。版本源 6 处、`工作记忆/`、`artifacts/UP_*` 未触碰自述**实测成立**。
- **测试数字**：`Ran 40 tests … OK`（行 405/407）为**唯一** E 线自测自证；另有 `Ran 69 … OK`（无回归，行 430）与负控 `FAILED(15+8)`/`OK(40)`（行 444/445）。**端到端真发全部 pending（6 处）**，且 `test_e2e_flags_still_false` 主动断言两个 E2E 标志 = `False` ⇒ **无一项端到端验证**。总数 40 与磁盘 `def test_` 计数**自洽**；分组表 38≠40 为瑕疵。
- **唯一标识符**：报告给出的 3 个 sha256（2 码文件 + 测试文件）与 2 个上游 sha256 **实测全部逐字吻合**；HEAD/分支/版本/T1·T2 来源 commit 全部命中 ⇒ `identifiers_ok = true`。
- **诚实标注**：质量高——主动把「不真发」写进头部与状态表，明确「本轮结论上限 = 请求构造层对齐」，并自行上报 3 项同链路未修缺陷与 `with_bd` 空操作。
- **其余未决**：R1–R9 为实质性开放风险（含 **R9 上游对账测试依赖未入库快照 ⇒ 5 条测试会静默 skip**）；R10–R13 为文档精度/可复现性/归属类疑点。

{"line":"E","files_claimed":3,"tests_total":40,"identifiers_ok":true,"open_risks":9,"verdict":"E 线 3 个 sha256 与源码对齐、40 用例模块在场且计数自洽，均实测吻合，诚实标注充分；但 T1/T2 端到端真发全部 pending、服务端接受度未知，另 3 项同链路缺陷（splice_url/4 前置/check_risk_response→假成功）+ diggLiveRoom 残留 + 上游对账测试依赖未入库快照共 9 项开放，结论上限仅达离线请求构造层对齐"}
