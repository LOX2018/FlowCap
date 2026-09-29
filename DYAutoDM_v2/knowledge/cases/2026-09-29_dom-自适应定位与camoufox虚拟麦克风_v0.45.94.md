# 案例：DOM 自适应定位兜底 + Camoufox 虚拟麦克风（含申请连麦 DOM 化）

> 日期 2026-09-29 · 版本 0.45.92 → **0.45.94** · 分支 `design/better-douyin`
> 性质：能力引入（H-12）+ 缺陷修复（登录页锚点漂移兜底）
> 状态：后端已实现并**离线/真机验证**；**GUI 有头端到端待验收**（见 §5）

---

## 一、设计与契约（Design-First）

| 项 | 内容 |
|---|---|
| 模块 | `backend/auto_dm/dom_locator.py`（新）；`backend/auto_dm/login_remote.py`（接线）；`backend/vbrowser_camoufox.py`（媒体 pref）；`backend/api/linkmic.py`（连麦 DOM 化） |
| 设计契约 | **DOM 定位兜底层**：仅当既有确定性判据（稳定锚点 / JS 文本）失败时启用；候选必须过**确定性校验器 + 唯一性**，否则**拒绝返回**（fail-closed）。<br>**媒体**：Camoufox 用 Firefox 原生 pref 提供**虚拟**麦克风/摄像头（绝不触碰真实设备）。<br>**连麦**：申请走**页面原生 DOM**（天然带签名、能驱动设备对话框），接口直调降级保留。 |
| 预期行为 | 抖音改版后登录控件仍可定位并用真实鼠标点击；连麦申请在直播间页发起且麦克风对话框被自动处理。 |
| 设计假设 | ① 登录面板 id/class 含 latin 标记 `login`（域判据）。② 连麦按钮文案含「申请连线」或「申请连麦」。③ 进房后按钮约 24s 出现。 |

## 二、观察到的偏差

| # | 偏差 | 根因（RCA） |
|---|---|---|
| 1 | 登录页锚点失效（ADR-023 D3/D4：`normal-input` 不在、扫码 tab 点不到） | 抖音改版使**硬编码锚点漂移**；且项目注释把 `#douyin_login_comp_btn_id` 误标为「验证码登录」（实为「登录」提交按钮） |
| 2 | QR 锚点 `#animate_qrcode_container img` 命中 0 | 二维码已改为 **SVG**（容器内 13 个 `<image>`） |
| 3 | 连麦点「申请连麦」无反应 / 提示需开麦克风 | ① 按钮真实文案是「**申请连线**」（探针只匹配「连麦」→ 0 命中）；② Camoufox 是 **Firefox 内核，Chromium 假媒体 flag 静默失效** ⇒ 无虚拟麦克风 |

## 三、修复

1. **`dom_locator.py`（新）**：Scrapling **parser 层**（base 包，无浏览器）封装 —— 降级链
   ① 自适应(旧选择器) → ② 自身文本 → ③ 结构域；**确定性校验器**（标签∈集 / 自身直接文本·属性 /
   祖先域 id-class 的 `login` 标记）+ **唯一性**（>1 即拒绝）。缺失即**优雅降级**（零回归）。
2. **`login_remote.py`**：`fill_phone/fill_code/click_by_text/click_login` 在原判据失败时调用兜底。
3. **`vbrowser_camoufox.py`**：`firefox_user_prefs=_media_prefs(cfg)`（`media.navigator.streams.fake` 等
   4 项），显式开关 `DY_FAKE_MEDIA_OFF`。
4. **`api/linkmic.py`**：`/apply` 默认走 BCC `/linkmic_run` 的 **DOM 流程**（等按钮→点→处理麦克风
   对话框），`method="api"` 降级保留。

## 四、Live-Instance 验证（实测读数）

| 验证 | 命令 | 结果 |
|---|---|---|
| Scrapling 只装 base 不引浏览器 | `uv pip install scrapling` | 7 包（lxml/cssselect/orjson/tld/w3lib/typing-extensions），**无** playwright/patchright |
| Python 3.14 兼容 | 3.14.6 实装 | ✅ |
| 离线自适应（含真实结构） | `artifacts/spike-scrapling-locator/verify_offline.py` | **15/15 PASS**（基线5+改版5+干扰5）；校验器负控 3/3 False |
| 真实 DOM + 人为改版 | `real_dom_test.py` | 真实 5/5；**改版后仍 5/5** |
| 真机闭环（定位→XPath→Camoufox） | `bridge_live.py` | **5/5 定位、5/5 可见且有独立几何框** |
| 门禁（定位） | `python -m unittest backend.test_dom_locator_gate` | **3/3 OK**（G3 负控曾抓出真实误命中，已修） |
| **虚拟麦克风（正控）** | `probe_mic_camoufox.py --fix` | **PASS**：`getUserMedia` 返回 **1 条音轨**（"Default Audio Device"） |
| **虚拟麦克风（负控）** | `probe_mic_camoufox.py` | **FAIL**：`Target crashed` / 无音轨 ⇒ 差异确由 pref 造成 |
| 门禁（媒体） | `python -m unittest backend.test_camoufox_media_prefs` | **4/4 OK** |
| 版本 | `python scripts/check_version_sync.py` | **六处齐平 0.45.94** |
| 前端类型 | `npx tsc --noEmit` | **exit=0** |

## 五、未验证项（诚实标注）

- **GUI 有头端到端未做**：连麦申请需**正在开播**的直播间 + 有头浏览器 + 用户在场（agent 做不到）；
  登录 RPA 需真机短信/扫码。本次只到「后端逻辑 + 离线/真机元素级」。
- 连麦 DOM 脚本的**按钮等待 40s / 对话框处理**：按 `工作记忆/12 §5.5` 实测参数实现，**未在真实开播间复跑**。
- Scrapling 自适应库默认**按域(tld)**存储；本项目单机单账号场景可接受，**多账号共用需评估作用域**。
- 打包：requirements + `build_sidecar.py` collect-all 已声明；**未实际打包部署**（等用户指令）。

## 六、关键教训（可复用）

1. **自愈定位必须 fail-closed**：用「含子孙文本」校验会让**父容器**命中所 有目标 ⇒ 多目标指到同一元素。
   改为「**自身直接文本 + 唯一性**」，>1 即拒绝。（门禁 G3 + 真实 DOM 双重抓出）
2. **子串匹配会过度捕获**：`btn_submit` 的 `want_text=("登录",)` 把「扫码登录/验证码登录」也算入 ⇒ 需
   `text_mode="exact"`。
3. **域判据要用 latin 标记**：`scope_text=("登录",)` 太弱（导航栏同名）⇒ 改锚 `login`（面板 id/class 含）。
4. **Chromium flag ≠ Firefox pref**：迁内核后必须逐项复核启动参数语义（本项目第 N 次同类）。
5. **不同内核失败形态不同**：媒体缺 pref 在 Firefox 下是**崩溃/无轨道**，在 Chromium 下是**静默无设备**。

## 七、相关

- 方案：`.hermes/plans/2026-09-28_162130-bcc-replacement-plan.md`（M-25，路线 B）
- 台账：`工作记忆/00_交接卡待办台账.md` H-12 / M-25
- 知识库：`工作记忆/12_业务域_直播监听.md` §5.5-5.6（连麦 DOM 铁律）
- spike 产物：`DYAutoDM_v2/artifacts/spike-scrapling-locator/`
