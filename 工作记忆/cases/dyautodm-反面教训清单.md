# DYAutoDM 反面教训清单（❌ 禁止重犯）

> **来源**：从 skill `dyautodm-dev-guards` 迁移（2026-09-17）。
> **迁移原因**：skill 只存「思路 + 判据」；具体踩坑叙事归知识库（用户 2026-09-17 定分层铁律）。
> **性质**：以下是**已实机踩过**的坑，规则性内容已在 skill 内压缩保留，此处为**完整原文**（保真，未改写）。

---

## 九、常见错误（已踩坑，禁止重犯）

- ❌ 独立脚本 `launch_persistent_context(user_data_dir=复制的profile)` 另开 chromium 测 DOM → 违反单 profile + 绕过 BCC。
- ❌ 后端用 cookie 批量 `get_user_info` 查昵称 → 风控（已被 08 否掉，改用 `im/user/info` 响应 hook）。
- ❌ 校验时另开无头浏览器用同 profile 截 `im/user/info` → SingletonLock 冲突（`Target page, context or browser has been closed`, exitCode=21）。正确做法：经 BCC `/capture_userinfo` 端点（复用常驻浏览器）。
- ❌ **猜接口/猜方案，不先查知识库**。用户原话纠正：「ws通道在基座项目有说明啊，知识库也有标注呀」。
  知识库 `08_私信列表与会话详情捕获方案.md` 已有大量**实机证真的接口清单**（如 §33.1/
  §34.2 记载 `imapi.douyin.com` 的 `get_message_by_init`/`get_by_conversation`/cmd 301 构造）。
  **动手猜接口名、写 hook 正则、定方案之前，先 grep 知识库**；凭印象写的正则/路径大概率是错的
  （本次把接口域名写成 `www.douyin.com/aweme/v1/web/...`，实测 404 `Unsupported path(Janus)`，
  正确域名 `imapi.douyin.com` 知识库里早就写着）。凡「我要逆向/接入某个能力」→ 先查库，
  查不到再实测，实测结论**回写知识库**（见 §五）。
- ❌ 凭代码猜"DOM 列表项含 conv_id/cid" → 实机证伪（V8~V13：outerHTML/data 属性/React fiber 均无）。
- ❌ 改完不核对知识库、不读 08 → 把已证伪的死路当方案。
- ❌ **用源码仓库路径当实测路径**：源码在 `C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend`，但**实机探针/有账号 profile/抓包原数据全在隔离测试路径 `C:\temp\dyautodm_test`**（含 `工伤小助理` 自有 profile、`auto_dm/accounts/工伤小助理/profile`、`probe_raw/init_1.bin`、一堆 `probe_*.py`）。改完代码要实测验证时，去 `C:\temp\dyautodm_test` 跑探针，**不是**在源码仓库目录找账号（那里无 profile、无 db）。用户原话："查看知识库，写的很清楚，源码和实测不是同一个路径"。
- ❌ **用首包 sec_uid 做关联键**：覆盖率仅 27%、与 im/user/info 重叠仅 5%（变形串），实测端到端 0 命中。正确解见 §二「数字 UID 桥接」。
- ❌ **判窗口是否可见绝不能只比坐标数值大小**：伪装无头写 `--window-position=-32000`，但 Chromium 会把窗口钳制到虚拟桌面边界，实际残留位置是 `-26214` 级（写进去的值 ≠ 实际落点）；且只看左上角会漏掉「窗口主体在屏外、仅边角在屏内」（如 left=-1268）。正确判据：`EnumDisplayMonitors` 枚举全部真实显示器工作区，窗口与任一工作区**双向相交 ≥200px** 才算可见；对 maximized/minimized 状态必须先带 `windowState:'normal'` 再 `setWindowBounds`，否则改不动坐标。伪装模式跑过后 profile 的 `Default/Preferences → browser.window_placement` 会残留屏外位置，一次性清理可删该键让下次按默认位置打开。
- ❌ **本机终端命令的 Windows 坑**：MSYS 路径转换在本会话环境是关闭的，`taskkill /F /IM` 用单斜杠（`//F` 会原样传给 taskkill 报「无效参数」）；tasklist/taskkill 输出是 GBK，Python `subprocess` 读它必须 `decode("gbk", errors="replace")`，UTF-8 解码直接抛异常。curl 带**中文 JSON 体**（账号名）内联 `-d` 会报 body 解析错误——把 body 写入文件（Windows 路径）后 `--data-binary @C:/path/body.json`。PowerShell 单行命令里的 `$_` 会被 bash 转义破坏（报「无法将 X.ItemName 识别为 cmdlet」且刷屏）——含 `$_` 的 PowerShell 逻辑写成 .ps1 文件后 `-ExecutionPolicy Bypass -File` 执行，杀进程等常用操作可固化成临时脚本复用。
- ❌ **想修「昵称/头像全空」却去修 `add_init_script`（hook 注入）**：2026-09-13 实机证伪——抖音前端改版，会话昵称不再经 `im/user/info`，前端自发请求 n=0，**hook 无请求可截**。`__CAP_USERINFO__` 注入后 hooked=true 但 map 恒空。修注入（patchright 屏蔽与否）是死路。**正确方案：直接抓会话列表 DOM**（`.conversationConversationItemtitle`=昵称、首个 `img`=头像，零成本一次全拿），sec_uid 关联键点会话时从详情 URL/请求补。先实机确认 `window.__CAP_USERINFO__.map` 大小 + 前端是否还发 im/user/info，再决定改 hook 还是改 DOM 抓取。

- ❌ **判断昵称捕获只能靠 hook，忽略 DOM 内嵌**：实机证明抖音把昵称/头像直接渲染进会话列表 DOM（`.conversationConversationItemtitle`/`img.src`），抓 DOM 零主动请求、零风控、一次全拿，不需要滚动 40 轮点会话。`.conversationConversationItemwrapper` 无 sec_uid 属性——关联键要通过点击会话拿（前端详情自带），或接受昵称头像已到手只差关联。

> 参考 `D:\文档\Biancheng   CK\DY v3\工作记忆\14_调试案例库与踩坑台账.md`：数字 UID 桥接技术细节 + 无头/有头探针写法 + 端到端验证命令。
> 参考 `references/nickname_storage_triangle.md`：capture_all ↔ recv_daemon ↔ messages.py 落库职责边界 + self 污染排查实录 + 日志去重写法。

- ❌ **改启动参数前先确认用户的真实意图**（2026-09-06 教训）：用户说“BCC 直接采用无头状指纹浏览器，避免风控”，误读为“视口对齐”（viewport/screen 1440x900）动手就改，被用户“不对”打断；再次澄清后真实需求是 **“直接调用指纹浏览器，而不是新开”** —— 即 BCC 与“双击打开指纹浏览器”同链路同环境，只是无头状。凡用户提出模糊的架构级需求（“直接调用 X”、“像 Y 一样”），先一句确认“X 指的是哪种/要哪种效果”再动手，不要按自己的第一解读实现（回滚浪费一轮）。
- ❌ **查 bug 方向搞反：前端现象却去查后端源码**。用户原话纠正：「你不是应该查前端收到了什么吗，为什么去查后端」。定位「前端没显示/显示错」类问题时，**第一步是看前端实际收到的接口返回**（curl/实机截图），不是去翻后端存储或源码猜。顺序：先 curl 接口看返回 → 再比前端渲染消费字段 → 最后才看后端实现。本会话「聊天记录不显示」最初走反方向浪费一轮，后在 `messages.tsx` 的 `openConv`/`detailQ` 与接口字段对照才定位。
- ❌ **「前端看不到数据」时凭现象猜是「没部署」**。先按 §九·甲 md5 比对确认部署文件=构建产物，再谈 bug。本会话用户连续两次质疑「是不是没部署」，实机 md5 证明部署无误，真因是列表排序把空会话顶到前面。
- ❌ **sidecar 入口脚本里用相对导入 → 服务启动即崩**。`build_sidecar.py` 把入口当
  **PyInstaller 入口脚本**（没有父包），此时 `from .xxx import ...` 必报
  `ImportError: attempted relative import with no known parent package`，**整个 sidecar 不可用**
  （实机证据：部署的 browser-daemon exe 一启动即抛，BCC 根本起不来）。
  两种模式导入语义不同：**包模式（`python -m pkg.mod`）能过、入口模式不能**——所以
  「模块 import 成功 + md5/语法检查」**验证不了入口**，必须**真的把入口/exe 跑一次**。
  修法：双兼容导入 `try: from .sibling import X  except ImportError: from sibling import X`。
  同仓其它 sidecar 入口（`recv_daemon.py`/`main.py`）**均无相对导入**，可作基准对照。
- ❌ **在同一处反复调导入方式、却没用正确解释器跑入口**：入口崩不一定是导入写法——
  先用**项目自带 Python**（Python314，含 playwright/patchright）前台跑一次入口，
  看真实 traceback，再决定改哪里。Hermes 会话的 `python`(3.11) 缺 playwright，
  会把「依赖缺失」误当「代码写错」。


