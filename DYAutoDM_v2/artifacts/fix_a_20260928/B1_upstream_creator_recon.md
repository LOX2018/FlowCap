# B-1 上游 creator 域只读侦察报告（提案素材）

- **性质**：只读取证。未修改任何源码、未 commit、未 import/运行上游代码、未 clone/install/build。
- **侦察对象（只读）**：
  - 主副本 `DYAutoDM_v2/vendor/douyin_spider_upstream/`（= 上游 commit `4479ea784bf3e63e75fcbe4ca985f84678d46b27`，2026-09-20，见 `vendor/PROVENANCE.md`）
  - 对照副本 `_ext_repos/{DouYin_Spider-master, DouYin_Spider_git, DouYin_Spider_latest}`
- **本项目侧只读参考**：`DYAutoDM_v2/backend/dy_apis/`、`backend/builder/`、`backend/utils/`、`backend/features.py`、`backend/requirements.txt`
- **取证日期**：2026-09-28
- **证据规则**：凡引用上游源码结论均注明「文件路径:行号」；未取得证据处显式标注「未取得证据」。

---

## ① creator 域暴露的端点 / 能力清单（含写操作标注）与规模

### 1.1 规模（相对本项目）

| 项 | 行数 | 字节 | 相对本项目 |
|---|---|---|---|
| 上游 `dy_apis/douyin_creator_api.py` | **2734** | 121,203 | ≈ 本项目 `backend/dy_apis/` 全部 6,850 行的 **40%** |
| 上游 `dy_apis/douyin_im_media.py` | **926** | 39,764 | ≈ 本项目 `dy_apis/` 的 13.5% |
| 两者合计 | **3660** | — | ≈ 本项目 `dy_apis/` 的 **53%** |
| 上游 `dy_apis/*.py` 全部 | 10,009 | — | 本项目 `dy_apis/`（6,850）= 上游的 68% |
| 上游 `*.py` 全部 | 17,058 | — | 本项目 `backend/**/*.py`（94,914）= 上游的 5.6 倍 |
| 上游 `utils/acrawler.py` | 181 | 6,879 | — |
| 上游 `utils/acrawler_runtime/` | 见 ② | 188,480（5 文件合计） | — |

依据：`wc -l`（creator 2734 / im_media 926 / acrawler 181 / dy_apis 合计 10009 / 上游全 py 17058）与 `backend` 侧 `find -name '*.py' | xargs wc -l`（dy_apis 6,850、backend 全量 94,914）。

### 1.2 端点清单（全部出自 `dy_apis/douyin_creator_api.py` 与 `dy_apis/douyin_im_media.py`）

标注约定：**写** = 会产生服务端状态变更；**读** = 仅取回数据；**上传** = 写入字节存储（不直接改作品/消息，但消耗配额、产生可被风控记录的存储对象）。

**A. 创作者发布链（creator.douyin.com）——本项目完全缺失的域**

| 端点 / 方法名 | 行号 | 用途 | 写操作 |
|---|---|---|---|
| `DouyinCreatorAPI.get_image_upload_auth` → `GET /web/api/media/upload/auth/v5/` | 1282 / 1296 | 取 ImageX+VOD 共用 STS 临时凭证 | 读 |
| `_create_aweme` → `POST /web/api/media/aweme/create_v2/` | 2620 / 2629 | **发布图文/视频（唯一真正的内容写接口）** | **写** |
| `post_images(...)` | 2280 | 发布图片/图文（组装 create_v2 请求） | **写** |
| `post_video(...)` | 2355 | 发布视频（组装 create_v2 请求） | **写** |
| `_require_publish_security` | 2248 | 发布前强校验 ticket / ts_sign / private_key / dtrait_blob / 会话一致性；缺一则**禁止发请求** | 门禁（非网络） |
| `get_preview_video_list` → `GET /janus/douyin/creator/pc/work_list` | 1025 / 1028 | 发布页历史作品预览映射 | 读 |
| `get_cover_gen_ref` → `GET /aweme/v1/cover/gen/ref/` | 1065 / 1067 | 封面生成参考信息 | 读 |
| `post_cover_gen_task` → `POST /aweme/v1/cover/gen/post/` | 1230 / 1242 | 服务端建封面生成任务 | 写（服务端任务） |
| `poll_cover_gen_task` → `GET /aweme/v1/cover/gen/get/` | 1252 / 1260 | 轮询封面任务 | 读 |
| `get_user_declaration_suggestion` → `GET /aweme/v3/user_declaration/suggestion/` | 1076 / 1085 | 发布页「作品声明」建议 | 读 |
| `video_enable` → `GET /web/api/media/video/enable/` | 1109 / 1111 | 视频可用性信号 | 读（GET 语义，副作用未取得证据） |
| `video_transend` → `GET /web/api/media/video/transend/` | 1116 / 1118 | 转码结束信号 | 读（同上） |
| `post_fast_detect` / `poll_fast_detect` / `_run_fast_detect_flow` → `POST /aweme/v1/post_assistant/fast_detect/poll` | 1139 / 1146 / 1157 / 1187 | 发布助手快速检测（含轮询） | 写（POST，服务端任务） |
| `get_creator_media_url` → `GET /aweme/v1/creator/get/url/` | 1202 / 1204 | creator 媒体 URL 解析 | 读 |
| `CSRF_PROBE_PATH = /web/api/media/anchor/search` | 69 | 换 `x-secsdk-csrf-token` 的探针端点 | 读（HEAD） |
| `build_image_apply_query` / `build_image_commit_query` / `build_video_apply_query` / `build_video_commit_query` / `build_creator_params` | 923 / 938 / 948 / 964 / 974 | 纯 query/参数构造 | 无网络 |
| `build_image_create_item` / `build_video_create_item` | 2139 / 2180 | 纯 create_v2 body 构造 | 无网络 |

**B. 媒体上传链（ImageX / VOD / TOS，出自同文件）**

| 方法名 | 行号 | 用途 | 写操作 |
|---|---|---|---|
| `apply_image_upload` → `GET imagex.bytedanceapi.com`（ApplyImageUpload，AWS SigV4） | 1329 | 申请图片上传，取 StoreUri/Auth/UploadHost | 读 |
| `upload_image_bytes` → `POST https://{UploadHost}/upload/v1/{StoreUri}` | 1369 / 1379 | 上传图片字节 | **上传** |
| `commit_image_upload` → `POST imagex`（CommitImageUpload） | 1406 / 1422 | 提交图片，取 ImageUri/宽高 | **上传（提交）** |
| `upload_one_image` / `upload_image_batch` | 1441 / 1465 | 单张/批量编排 | **上传** |
| `apply_video_upload` → `GET vod.bytedanceapi.com`（ApplyUploadInner） | 1512 / 1532 | 申请视频上传，取 SessionKey | 读 |
| `upload_video_direct` / `upload_video_parts` → `POST .../upload/v1/...` | 1594/1598 / 1605/1608 | ≤3MB 直传 / 分片传输 | **上传** |
| `commit_video_upload` → `POST vod`（CommitUploadInner，带 GetMeta+Snapshot） | 1648 / 1670 | 提交视频，取 Vid/时长/封面帧 | **上传（提交）** |
| `upload_one_video` / `upload_prepared_video` | 1695 / 1710 | 单视频编排 | **上传** |
| `prepare_video_cover_state` / `_prepare_video_cover_preupload` / `_finalize_video_cover_state` / `_complete_cover_gen_flow` | 1752 / 1812 / 1997 / 1774 | 封面预上传与状态机 | **上传/写** |
| `_extract_video_frames` / `_extract_audio_analysis_wav` / `_video_probe` | 415 / 446 / 392 | 本地抽帧/抽音/探测（**依赖 cv2、ffmpeg、wave**） | 本地 |
| `_browser_random_s`（子进程调用 `node -e` 生成 V8 随机串池） | 100 / 105–130 | 复刻浏览器 `Math.random().toString(36)` 形态 | 本地（**需 Node**） |

**C. IM 富媒体上传（`dy_apis/douyin_im_media.py`）——本项目已有自研等价物**

| 方法名 | 行号 | 用途 | 写操作 |
|---|---|---|---|
| `DouyinIMMedia.get_upload_config` → `GET /aweme/v1/web/im/upload/config/v2` | 302 / 39 | 取四组短期 STS（内存缓存，不落盘） | 读 |
| `_apply_upload` / `_upload_source` / `_commit_upload` | 358 / 400 / 446 | VOD Apply/直传/Commit | 读 / **上传** / **上传** |
| `upload_image` | 506 | 上传图片并转成 IM message content | **上传** |
| `upload_video` / `_video_cover` | 567 / 532 | 上传视频（无封面时抽首帧） | **上传** |
| `upload_audio` / `upload_file` | 628 / 645 | 上传语音 / 文件 | **上传** |
| `build_share_aweme_content` / `build_share_photos_content` / `build_share_web_content` / `build_user_card_content` / `sticker_content` | 675 / 746 / 816 / 854 / 891 | 纯 IM 卡片 payload 构造 | 无网络 |
| 该模块从 creator 模块**反向 import 私有符号**：`_MediaSource, _crc32_hex, _image_size, _slice_size_for` | 29–34 | 说明两文件是**耦合的一个整体** | — |

> 小结：creator 域 **1 个真正的内容写接口**（`create_v2`）+ 2 个服务端任务写接口（cover/gen、fast_detect）+ 全套媒体上传（写入 VOD/ImageX）。其余 14 个为读接口或纯算。

---

## ② `utils/acrawler_runtime` 到底是什么

### 2.1 定性：**是**浏览器内 JS 签名运行时（离线 node `vm` 复现），**不是**浏览器/CDP 驱动

- `utils/acrawler.py:2-8` 模块 docstring 原文：*"Run the page acrawler bundle without starting a browser. The bundled VMP is executed in a small Node `vm` context whose DOM and navigator prototypes are shaped from the captured Chromium page."*
- `utils/acrawler_runtime/ac_vm.js` 首 200 字符：`var glb;(glb="undefined"==typeof window?global:window)._$jsvmprt=function(...)` —— 即抖音 acrawler 的 **VMP 混淆包**（`_$jsvmprt` 入口）。
- `acrawler_runtime/run_ac_node.js:940-959` 读取 VMP 源并用 `vm.createContext(ctx)` + `vm.runInContext(acSource, ctx, {filename:'ac_vm.js', timeout:5000})` 执行；`:972` 调 `window.byted_acrawler.sign('', nonce)` 取签名。
- `acrawler.py:51-62` 对外唯一 API：`generate_ac_signature(nonce, cookie, ...) -> {"sig","cookie_header","cookie","provenance"}`；`provenance` 正常值为 `"node_page_js"`（:176），失败时**不返回伪造签名**（strict 模式直接 raise，:155-159）。

### 2.2 依赖：**只有 Node + Node 内置模块**（无 npm、无浏览器、无 CDP、无远程服务）

| 依赖项 | 事实 | 证据 |
|---|---|---|
| Node 可执行文件 | `shutil.which("node")`，缺失且 strict 时 `raise RuntimeError("Node.js is required...")` | `utils/acrawler.py:84-90` |
| Node 内置模块 | `run_ac_node.js` 全部 `require()` 只有两处：`require('fs')`、`require('vm')` | `run_ac_node.js:1-2`（`grep -oE "require\(...\)"` 全量结果） |
| npm 包 | **无**。上游根目录**没有 `package.json`**；`newsign/package.json`（canvas/jsdom/jsrsasign/sdenv/vm）在 `.gitignore` 的 `/newsign/` 之列、`git ls-files newsign` 为空 ⇒ 与 acrawler_runtime 无关 | `.gitignore`；`git ls-files newsign` |
| 浏览器 / CDP | 无任何浏览器或 CDP 调用；DOM/navigator/canvas/WebGL 全部是**硬编码的捕获值快照** | `run_ac_node.js:52-138`（字体宽度表、WebGL vendor/renderer 常量、CANVAS_DATA_URL）、`:565` 读 `browser_window_shape.json` |
| 远程服务 | 无。全程 `subprocess.run([node, _RUNNER], cwd=_ROOT, env=env, timeout=15)`，不联网 | `utils/acrawler.py:128-136` |
| 本机二进制 | 无（只用 node 与文本 fixture） | 同上 |
| 运行时数据 fixture | `browser_window_shape.json`(47,428B)、`canvas_actual_exact.json`(1,300B)、`runner_window_keys_baseline.txt`(3,254B) | `ls -la utils/acrawler_runtime/` |
| Node 版本要求 | README 徽章与「运行环境」写 `Node.js 18+`（用了 `vm`/`Proxy`/`WeakMap`） | `README.md:13-14`、`:104` |

### 2.3 引它进本项目需新增的运行时依赖

| 需新增 | 现状 | 说明 |
|---|---|---|
| **Node 运行时（≥18）** | 本机 **已装**：`/c/Program Files/nodejs/node`；但 **未在 `backend/requirements.txt` 或任何部署清单里声明** | 这是唯一的新增「非 Python」运行时。不影响 Python 依赖图 |
| npm 包 | **不需要** | 见上表 |
| 浏览器 / CDP / playwright | **不需要** | 与本项目现有 camoufox 内核无关，是两条独立路径 |
| 文本 fixture（4 个文件，188 KB） | 需随代码一起带 | 若走「直接引代码移植」需一并复制 |

> **定性结论**：acrawler_runtime = 「**Node `vm` 里跑抖音页面 VMP 的离线签名器**」。它不启动浏览器、不走 CDP、不联网，只需 Node 可执行文件 + 4 个文本 fixture。对本项目而言，其**接入成本主要是「引入 Node 作为运行时依赖」这一条**，而非依赖膨胀。
>
> **职责边界**：它是**匿名态**签名（`__ac_nonce` → `__ac_signature`），服务于登录/挑战页；与 creator 发布链**无调用关系**。调用点唯一：`dy_apis/login_api.py:947-978`（`_apply_ac_signature`）。

---

## ③ 许可证事实

### 3.1 本地：**未取得证据表明存在任何 LICENSE/COPYING/NOTICE 文件**

| 检查位置 | 命令 | 结果 |
|---|---|---|
| vendor 主副本顶层 | `ls -la` | 仅 `.env.example / .gitignore / Dockerfile / README.md / requirements.txt / main.py / quick_publish.py` + 目录；**无 LICENSE/COPYING/NOTICE** |
| vendor 主副本深层 | `find . -maxdepth 2 -iname '*licen*' -o -iname '*copying*' -o -iname '*notice*'` | 0 命中 |
| vendor 主副本 git 索引 | `git ls-files \| grep -iE 'licen\|copying'` | 空 |
| 三个对照副本 | 各自 `ls -a \| grep -iE 'licen\|copying\|notice'` | 全部 `(no LICENSE/COPYING/NOTICE)` |
| `_ext_repos/DouYin_Spider_git` git 索引 | `git ls-files \| grep -iE 'licen\|copying\|notice'` | 空（该副本 `.git` 存在，remote = `https://github.com/cv-cat/DouYin_Spider.git`） |
| README 正文 | `grep -niE 'licen\|授权\|许可\|MIT\|GPL\|apache\|copyright\|版权\|商业' README.md` | **0 命中**（README 无任何许可声明） |

**⇒ 本地 LICENSE 文件：不存在（文件名与首行均无 —— 无对象可引用）。**

README 中唯一相关的**非许可性**声明在 `README.md:55`：
> `**⚠️ 严禁用于发布不良信息、违法内容！本项目仅供学习与技术研究使用，如有侵权请联系作者删除，后果自负。**`

注意：该句是**使用限制声明**，**不是**许可授予（不含授权条款、不含分发/衍生/商用授权）。

### 3.2 GitHub API 实测（本机 PAT，2026-09-28 实测）

```
GET https://api.github.com/repos/cv-cat/DouYin_Spider
  license            = null            (键存在，值为 null)
  license.spdx_id    = null            (无对象 ⇒ 无 SPDX 标识)
  stargazers_count   = 3198
  default_branch     = "master"
  pushed_at          = "2026-09-27T09:40:31Z"
  description        = "抖音逆向，抖音爬虫，抖音全部api、私信、直播间监听"

GET https://api.github.com/repos/cv-cat/DouYin_Spider/license
  → HTTP 404            (GitHub 明确「本仓库无许可证文件」)
```

与父会话已实测事实（`license.spdx_id = null`、3198★、`master`、`pushed_at=2026-09-27`）**逐项一致**；本次补充了 `/license` 端点 **404** 这一更硬的证据。

### 3.3 合规判定

按伯尔尼公约与 GitHub 官方口径：**无语可证 = 保留所有权利（All rights reserved）**。默认版权法下，未经许可的**复制、修改、分发、衍生**均构成侵权风险；「署名/非商用」也不能自动免责（无许可即无授权范围）。

**⇒ 代码级移植（把上游源码复制/改写进本项目）存在明确的版权合规风险。**

**本项目内部的先例与张力（供决策，非本报告结论）**：
- `docs/adr/ADR-017-im-remote-login-credential-update.md:424-427` 对**另一个**上游（`mkuko52/douyin_spider`）的判定是：*「该上游不可采用：`license = None`（仓库根无 LICENSE 文件），……按 OSS 选型判据（协议宽松为第一条）出局；本次只取其情报结论，未引入其任何代码。」*
- 但本项目**已经**把本上游整仓作为 read-only 副本放进 `vendor/douyin_spider_upstream/`（`vendor/README.md:1-3` 自述「第三方开源代码的原样副本」「DO NOT EDIT」），并已由 `backend/auto_dm/login_api_vendor.py` 走 `sys.path` 注入调用其 `dy_apis/login_api.py`。
- 即：**同一份 license=null 的仓库，本项目已按「只读 vendor + 适配层」方式接了登录模块**。因此本次 creator 域的增量风险取决于走**「继续只读 vendor」**还是**「把代码搬进 backend/ 自有源码树」**——后者是新的、更大的合规敞口。

---

## ④ 「直接引代码移植」vs「只取协议形态、自己实现」的改动面与合规差异

### 4.1 路径 A：直接引上游代码（整模块移植 / 继续 vendor + 适配层）

**改动面（本地模块 / 新依赖）**

| 类别 | 具体项 | 依据 |
|---|---|---|
| 新代码文件 | `dy_apis/douyin_creator_api.py`(2734) + `douyin_im_media.py`(926) 及其 4 个私有符号耦合；`utils/imagex_sign.py`(130) | 文件清单 |
| 必带 fixture | `acrawler_runtime/` 4 文件（188 KB）——若同时要匿名签名 | `ls -la` |
| 新**传输层**依赖 | `curl_cffi`（`utils/http_client.py`，247 行）——**本项目已装且已在 requirements 声明** | `backend/requirements.txt`（ADR-017 段）；`utils/http_client.py:1-45` |
| 新**媒体处理**依赖 | `opencv-python-headless`、`av`（`requirements.txt:7-8`）；运行时 `ffmpeg` 二进制（`shutil.which("ffmpeg")`，`douyin_creator_api.py:458`） | 同左 |
| 新**非 Python 运行时** | Node ≥18（`_browser_random_s` 用 `node -e`；acrawler 用 node `vm`） | `douyin_creator_api.py:105-130`；`utils/acrawler.py:84-90` |
| 本项目既有可复用面 | `backend/utils/ab_pure.py:37` 已含 `creator.douyin.com → (2906, 33638)`；`builder/header.py:26` 注释已含 `creator=2906`；`builder/params.py:52-63` `with_a_bogus(host=...)` 已支持 creator 域；`utils/dy_util.py` 已有 `generate_a_bogus` / `generate_csrf_token` | 各文件行号 |
| 本项目**缺失**的接线 | 上游依赖的 `auth` 属性本项目不存在：`creator_cookie_str`(852)、`creator_uid`(2616)、`creator_csrf_token`(2675)、`ticket_matches_session()`(2266)、`publish_attempted/publish_server_verified/creator_upload_verified/creator_read_verified`；`builder/params.py` 无 `with_creator_platform()`；本项目无 `utils/imagex_sign.py`、无 `utils/dtrait.py`（上游 223/243 行） | 上游 `grep -noE 'auth\.[a-z_]+'`；本项目 `utils/` 清单 |
| 建议接法（沿用既有 SoC 范式） | 照 `backend/auto_dm/login_api_vendor.py` 的「vendor 只读 + 适配层」模式另建 `*_vendor.py`，而非把源码搬进 `backend/`（ADR-017 已确立该模式） | `login_api_vendor.py:1-33` |

**合规差异**：**最高风险**。复制 2734 行主体代码 + 派生修改 → 直接落在「无许可 = 保留所有权利」的侵权射程内。若仅维持 `vendor/` 只读副本并**不修改**，风险面与现状持平（但仍非零，且 `vendor/README.md` 的「原样副本」自我定位无法替代授权）。

### 4.2 路径 B：只取协议形态、自己实现（本项目已有强先例）

**改动面**

| 类别 | 具体项 | 依据 |
|---|---|---|
| 新代码文件（自研） | `backend/dy_apis/client_creator.py`（照现有 `client_*.py` mixin 范式）+ `backend/utils/imagex_sign.py`（AWS SigV4 自研） | `dy_apis/_bindings.py:24-27` |
| 可**直接复用**本项目现有实现 | ① AWS4 签名与 VOD Apply/直传/Commit **已在** `backend/dy_apis/image_sender.py:99-410`（`_aws4_get_authorization`/`apply_upload`/`upload_to_tos`/`commit_upload`）——creator 的 VOD 链可直接镜像此范式；② `ab_pure.py` creator 域常量已有；③ `utils/dy_util.py` 的 a_bogus/csrf 已有；④ `secsdk_web_sign`、`bd_ticket`、`mstoken` 均已有本地版本 | 各文件行号 |
| 需自研的净增量 | ImageX 侧 AWS4（`imagex` service，与现有 `vod` service 同构，约 130 行量级）、creator 域 cookie/CSRF/ticket 域隔离、`create_v2` body 形态与封面状态机 | 上游 `utils/imagex_sign.py:1-30` docstring |
| 新依赖 | `opencv-python-headless` / `av` / `ffmpeg` **仅在需要本地抽帧/抽音时**才需要；若封面走服务端 Snapshot（上游 `VOD_PROCESS_ACTION`，`douyin_creator_api.py:74-77`）则可避免 | `requirements.txt:7-8` |
| 接入成本 | 显著高于 A（需自研+实机对拍 create_v2 字节），但落在本项目**已确立的 SoC**内 | — |

**合规差异**：**最低风险**。只借鉴「端点路径、参数名、字段形态、流程顺序」这类**事实性协议信息**（不受版权保护），不复制表达层代码。

### 4.3 两路对照速查

| 维度 | A 直接引代码 | B 取协议形态自研 |
|---|---|---|
| 合规风险 | 高（无许可 + 大段复制） | 低（不复制表达） |
| 新增 Python 依赖 | `opencv-python-headless`、`av`、（`curl_cffi` 已有） | 同上，且可按需裁剪 |
| 新增非 Python 运行时 | Node ≥18（若含 acrawler / `_browser_random_s`） | 仅当自研需要时 |
| 新增 fixture | `acrawler_runtime/` 188 KB | 无 |
| 落地工期 | 短（接线 + 适配层） | 长（自研 + 实机对拍） |
| 维护面 | 需持续跟上游 diff（上游 `pushed_at=2026-09-27`，活跃） | 自持，不随上游漂移 |
| 本项目既有缺口 | 需补 `auth` creator 属性、`with_creator_platform`、`imagex_sign` | 同 |

---

## ⑤ 需要用户拍板的决策点（5 条）

1. **合规基线**：本项目是否允许把 `license = null` 的上游 `cv-cat/DouYin_Spider` 的 **creator 域主体代码**（2734+926 行）**搬进 `backend/` 自有源码树**？
   - 现状事实：ADR-017 已明确以「`license=null` ⇒ 出局、只取情报」为判据拒绝过**另一家**同 license 状态的上游；但对**本**上游却已采用「整仓只读 vendor + 适配层」。
   - 需拍板：creator 域是**继承既有 vendor 模式**（只读 vendor + 新适配层），还是**升级为搬入自有源码树**（新敞口）？

2. **路线选择**：走 A（引代码移植，快）还是 B（取协议形态自研，合规低）？若选 A，是否**强制限定**为「`vendor/` 只读 + `*_vendor.py` 适配层，禁改上游、禁复制进 `backend/`」？

3. **Node 运行时**：是否接受把 **Node ≥18** 变为项目的**显式部署依赖**（本机已装，但 `backend/requirements.txt` 与部署清单均未声明）？这决定 `acrawler_runtime`（匿名签名）与 `_browser_random_s`（V8 随机串）能否可用。

4. **发布写接口的准入**：`/web/api/media/aweme/create_v2/` 是**平台内容写操作**，上游实现自带 `_require_publish_security` 硬门禁，要求 `DY_TICKET / DY_TS_SIGN / DY_PRIVATE_KEY` + **可按 create_v2 path 重算的 `DY_DTRAIT_BLOB`**（静态 `DY_SESSION_DTRAIT` 被明确禁止）。
   - 需拍板：本项目是否具备（或愿意获取）这套发布安全素材？若不具备，creator 域只能做到**上传链 + 读接口**，`create_v2` 不可用。

5. **媒体处理依赖**：是否同意为 creator 域新增 `opencv-python-headless` / `av` / `ffmpeg` 依赖（用于本地抽帧/抽音/封面）？若否，需限定为「封面一律走服务端 Snapshot + 显式 `cover_uri`」的裁剪实现。

---

## 附录：证据索引（可复核）

| 结论 | 证据位置 |
|---|---|
| creator 域端点与写语义 | `vendor/douyin_spider_upstream/dy_apis/douyin_creator_api.py:69,1028,1067,1085,1111,1118,1146,1204,1242,1260,1296,2629` |
| 发布门禁 | 同上 `:2248-2278` |
| IM 域端点 | `dy_apis/douyin_im_media.py:39,302,506,567,628,645` |
| acrawler = Node `vm` 离线签名器 | `utils/acrawler.py:2-8,84-90,128-136,176`；`utils/acrawler_runtime/run_ac_node.js:1-2,940-959,972`；`utils/acrawler_runtime/ac_vm.js:1` |
| 无 npm 依赖 | `run_ac_node.js` 全量 `require()` 仅 `fs`/`vm`；上游根无 `package.json`；`.gitignore` 含 `/newsign/` |
| Node ≥18 要求 | `README.md:13-14,104` |
| acrawler 唯一调用点 | `dy_apis/login_api.py:947-978` |
| 本地无 LICENSE | 六处 `ls`/`find`/`git ls-files` 全 0 命中（见 3.1 表） |
| README 无许可声明 | `grep -niE 'licen\|授权\|许可\|MIT\|GPL\|apache\|copyright\|版权\|商业' README.md` → 0 命中；仅 `README.md:55` 使用限制 |
| GitHub API 事实 | `GET /repos/cv-cat/DouYin_Spider` → `license=null`；`GET /repos/cv-cat/DouYin_Spider/license` → `HTTP 404`（2026-09-28 实测） |
| 本项目已有/缺失面 | `backend/utils/ab_pure.py:37`；`backend/builder/header.py:26`；`backend/builder/params.py:52-63`；`backend/dy_apis/image_sender.py:99-410`；`backend/requirements.txt`（curl_cffi 段）；`backend/utils/` 清单（无 imagex_sign/dtrait） |
| 既有 vendor 先例 | `vendor/README.md:1-43`；`vendor/PROVENANCE.md:1-12`；`backend/auto_dm/login_api_vendor.py:1-74`；`docs/adr/ADR-017-im-remote-login-credential-update.md:424-427` |
| 上游活跃度 | `pushed_at = 2026-09-27T09:40:31Z`（API）；本地快照 commit `4479ea784b`（`vendor/PROVENANCE.md:6`） |

**未取得证据项**（不推断）：
- `/web/api/media/video/enable/` 与 `/web/api/media/video/transend/` 的**服务端副作用**（上游以 GET 发送，是否有状态变更未取得证据）。
- 上游 `create_v2` 实际发布成功率（未运行上游代码，且本任务明令禁止真机发布）。
- 仓库在**历史 commit** 上是否曾存在 LICENSE（当前 HEAD 与 git 索引均无；未遍历全历史）。
