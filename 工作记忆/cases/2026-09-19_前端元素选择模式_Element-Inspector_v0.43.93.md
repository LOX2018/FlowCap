# 2026-09-19 前端元素选择模式（Element Inspector）新增 — v0.43.93

> 需求原话：「该项目前端无法精准定位元素，在前端加一个调试按钮，点击后将前端渲染页面变成
> 可选择元素模式（类似 XPath Tool），只选择元素复制元素的结构位置，不触发元素功能，
> 这样方便我调试前端功能。」

## 一、设计意图与契约（Step 1）

| 项 | 内容 |
|---|---|
| 模块 | `frontend/src/lib/element-inspector.tsx` + `element-inspector.css`（新增） |
| 设计契约 | **只选择、不触发**：选择模式下点任何元素，元素自身与祖先（含 React 合成事件委托根）都收不到该事件 |
| 预期行为 | 点顶栏 ◎ 按钮 → 悬停高亮 → 点击选中 → 面板给出 CSS/XPath/Playwright/组件链/结构；Esc 退出 |
| 设计假设 | Tauri 生产构建**没有 DevTools**；元素取址必须只读 DOM/React fiber，零业务耦合 |

**为什么需要它**：Tauri 打包后右键「检查」不可用，前端排查时无法把「界面上的控件」映射回
「源码里的组件 / `data-od-id`」。本项目已有 `data-od-id` 稳定锚点体系（73 处），本工具正是它的
可视化取址入口。

## 二、实现要点（架构洁癖对齐）

1. **零业务耦合**：只读 DOM/fiber，不读不写任何 API / store / localStorage；挂载点仅两处
   （TopBar 按钮 + App 根部面板），业务组件零改动。
2. **取址优先级**：`#id`（唯一）→ `tag.稳定类名`（唯一）→ `tag:nth-of-type(n)`；
   **绝不使用构建期哈希类名**（如 `_9f3a2`，下次构建即变）。
   XPath 优先锚定最近的 `data-od-id`。
3. **不触发**靠三件事同时成立（缺一即静默破约）：
   - 拦截清单覆盖 `click/pointerdown/pointerup/mousedown/mouseup/contextmenu/submit` 等；
   - 在 **window 捕获阶段** 注册（`addEventListener(ev, fn, true)`）；
   - `preventDefault + stopPropagation`。
4. **降级诚实**：React fiber 属实现细节，生产构建压缩组件名 → 组件链显示
   「不可用（生产构建已裁剪）」，**绝不显示 `ep`/`t` 这类压缩名**（误导性信息比不显示更糟）。

## 三、链路溯源：实测捕获的 3 个真缺陷（Step 3/4）

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | 点击能选中（高亮变），但**结果面板永不出现** | `swallow` 与 `onClick` 是 `click` 上的**兄弟监听器**，`stopImmediatePropagation()` 把同节点的 `onClick` 一起吞了 | 去掉 `stopImmediatePropagation`（只 `stopPropagation`） |
| 2 | 悬停后选择器/类名里出现 **`ei-hover`** | 工具自身的悬停类被 `classListOf` 当成业务类 | `classListOf` 过滤 `ei-` 前缀 |
| 3 | 点「复制全部」**毫无反应**（连提示都没有） | `${(__APP_VERSION__ as string) \|\| "?"}` — **模板里的 TS 类型转换让 Vite `define` 的常量替换失效** → 运行时 `__APP_VERSION__` 未定义 → `ReferenceError` **被 promise 静默吞掉** | 包一层 `frontendVersion()`：`try { return String(__APP_VERSION__) } catch { return "?" }`（与 `api/client.ts` 既有惯例一致） |

**缺陷 3 的通用教训**：`define` 注入的常量一旦被包进类型转换/复杂表达式，替换即失效；
且 **`async` 函数里的 `ReferenceError` 会静默消失**（无 pageerror、无 console、被 promise 吞掉），
排查必须直读**构建产物**（`grep __APP_VERSION__` 产物的 JS）而非只跑 dev。

## 四、实机验证（Step 5）

脚本：`scripts/verify_element_inspector.py`（patchright + **一次性临时 profile**，绝不碰账号 profile）。
harness：一次性 `preview-inspector.html`（含「危险按钮 + 计数」，用于证明「不触发」），验证后已删除。

| 验证 | dev server | **生产构建**（minify + prod React） |
|---|---|---|
| 结果 | **20/20 PASS** | **20/20 PASS** |

核心用例（两轮均通过）：
- ★「选择模式下点击危险按钮 → 计数 0 → 0」（onClick 未被触发）
- ★「Esc 退出后点击 → 计数 0 → 1」（功能恢复）
- 悬停高亮、面板取址（`[data-od-id="demo-danger"]` / XPath / 组件链段）、`←` 上移父元素、
  复制完整报告、面板自身可交互、无 pageerror

静态守卫：`backend/test_element_inspector_guards.py` — **13 项 OK**（含
「不得 `stopImmediatePropagation`」「拦截在捕获阶段」「工具类名不进选择器」）。
全量套件 `unittest discover` 303 项：本次改动**零新增失败**（存量 2 项失败与本改动无关，见 §六）。

## 五、架构变更（契约层）

- **新增前端调试规范（未来同类需求照此办理）**：
  1. 取址类工具一律**只读**，挂载点收敛到 TopBar，不允许散落各页；
  2. 「不触发」必须靠**捕获阶段拦截**，不允许依赖调用方自觉（本项目既有铁律：
     约束要做进资源层自证）；
  3. 取址优先 `data-od-id` → 稳定类名 → `nth-of-type`，**哈希类名永不入选择器**
     （对应全仓选择器纪律，见 `docs/frontend_redesign_spec_pages.md`）；
  4. **降级诚实**：拿不到组件名要显式写「不可用」，禁止展示压缩名/猜测名。

## 六、遗留与说明（诚实交代）

1. **并发工作线**：本次改动期间有另一条工作线在**同一前端**推进 v0.43.93
   （直播策略/目标直播间）。我只改了自己创建的 2 个新文件 + `App.tsx` 的 8 行；
   `git diff App.tsx` 实测**仅含我的 8 行**，无并发改动混入。该线的后端改动我**未触碰**。
2. **全量套件存量失败（非本次引入）**：
   - `test_ai_agent.TestConsumerWiring.test_generate_reply_passes_account` —— 断言
     `KB.find_match(...)`，而代码已改为 `reply_kb.find_match(...)`（并发线改动，测试未同步）；
   - `test_member_smoke` —— import 期 Fernet 签名不匹配（会员凭证/环境问题）。
   两项均在**我的文件之外**，且与本功能无调用关系。
3. **已打包并部署（19:30 完成）**：`build_sidecar.py --onedir` → `tauri build --no-bundle`
   → `deploy.py`，三阶段退出码 **0/0/0**。部署到 `C:\temp\dyautodm_design`。

## 七、部署与部署后实机验证（18:47~19:32）

| 验证项 | 判据 | 实测结果 |
|---|---|---|
| 版本五处齐平 | `check_version_sync.py 0.43.93` | ✓ 五处齐平（含 `_build_version.py`，由 `build_sidecar.py` 写入） |
| 构建链 | 逐段捕获退出码（**禁把命令接进管道读 `$?`**） | `step1_exit=0 step2_exit=0 step3_exit=0` |
| 部署 exe = 构建产物 | `md5sum` 双向 | **一致**（`3ccd848ea2c640f5cc9d844c6acf6a36`） |
| 前端 bundle 已进 exe | `grep <bundle名> <exe>` | 命中 `index-4vu0aOS3.js`（= `index.html` 实际引用、且**含** `debug-inspector-toggle` 的那一个） |
| 部署目录唯一主 exe | `ls \| grep '^DYAutoDM.*\.exe$'` | 仅 `DYAutoDM_v2_0.43.93.exe` |
| 运行实例版本 | `curl :8000/api/version` | `{"backend":"0.43.93","frozen":true}` |
| 就绪探针 | `curl :8000/api/ready` | `{"ok":true,"daemons_ready":true,"accounts":2}` |
| **真实链路可用** | `curl :12726/status` | `version 0.43.93`；账号「尚进工伤小助理」`connected:true`、`connects:1`、`backoff_stage:0`、**conv_count 83** |

### ⚠️ 本轮踩坑：`dist/` 旧 bundle 堆积（已知坑复现）

`frontend/dist/assets/` 下同时存在 **3 个** `index-*.js`，其中**只有 `index-4vu0aOS3.js`
含本次的 inspector**（另两个 inspector=0）。原因是项目已记录的「`emptyOutDir` 被 IDE
safe-delete 拦截」坑（见 `vite.config.ts` 注释与 08 §三十一）。

**判据（勿凭 bundle 名新旧推断）**：必须**读 `dist/index.html` 的实际引用**，
再核对**那一个文件**是否含目标特征串 —— 我一开始按 `ls -t` 取「最新」的 `index-F3_Uwzbp.js`，
它就**不含** inspector，差点得出「功能没打进产物」的错误结论。

### ⚠️ 强杀 backend 会留 recv 孤儿（本项目铁律复现）

smoke 脚本按 `/api/version` 的 pid 精确 `Stop-Process` 关闭 backend，verification 成功，
但**留下 2 个 recv_daemon 孤儿**（父进程=刚被杀掉的 backend pid，占 12726/12687）
—— 印证「`_kill_spawned_daemons()` 只在**优雅退出**时执行」。
**收尾必须走 `POST /quit`**（实测两个孤儿均返回 `{"ok":true}` 并退出），
绝不用 taskkill；清理后复查进程表与端口应双双归零。

## 八、文件清单

| 文件 | 类型 |
|---|---|
| `frontend/src/lib/element-inspector.tsx` | 新增（核心，含设计契约注释） |
| `frontend/src/lib/element-inspector.css` | 新增（全部使用主题令牌，亮/暗自动适配） |
| `frontend/src/App.tsx` | 改 8 行（import + TopBar 按钮 + App 根部面板） |
| `scripts/verify_element_inspector.py` | 新增（20 项实机验证，可重复运行） |
| `backend/test_element_inspector_guards.py` | 新增（13 项静态守卫） |
