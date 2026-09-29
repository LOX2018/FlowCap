# Spike: Scrapling 自适应定位（DOM 定位兜底层） — Verdict: VALIDATED

> 日期 2026-09-29 · 隔离 spot（`artifacts/spike-scrapling-locator/`，**未进源码树、未 commit**）
> 目标：验证「用 Scrapling 的自适应元素定位，取代/兜底项目里脆弱的 DOM 选择器（扫码登录 / 验证码登录
> 按钮、手机号/验证码输入框、提交按钮）」是否可行。**BCC 与其浏览器栈不动**——只换 DOM 定位层。

---

## 问题（Given / When / Then）

| # | Spike | 判据 | 结果 |
|---|---|---|---|
| S1 | 只取 parser、能否**不引 Chromium 浏览器栈** | 只装 base 包应不拉 playwright/patchright | ✅ 7 包、零浏览器 |
| S2 | 能否落在项目 **Python 3.14** | 解析+实装通过 | ✅ 3.14.6 干净安装 |
| S3 | 改版（id/name/class 全变）后能否**自适应重定位** | 合成页 15/15 + 真实 DOM 全漂移后 5/5 | ✅ |
| S4 | **误命中**风险是否可控 | 干扰页 0 误命中 + 校验器负控全 False + 几何框不重复 | ✅ |
| S5 | 能否**接回真实浏览器点击** | 定位 XPath → Camoufox：5/5 可见且有框 | ✅ |

## 实测证据

| 验证 | 命令 | 结果 |
|---|---|---|
| 离线（合成页，含真实结构） | `python verify_offline.py` | **15/15 PASS**（基线5 + 改版A5 + 干扰页5）；负控 3/3 False |
| 真实 DOM（人机改版后重定位） | `python real_dom_test.py` | 真实页 5/5；**人为改版后仍 5/5** |
| 真机捕获 | `python live_probe.py` | 面板出现；旧锚点 `normal-input`/`button-input` 真实存在（x2）；二维码=**SVG**（`img` 命中 0） |
| 真机闭环 | `python bridge_live.py` | **5/5 定位成功、5/5 可见且有独立几何框** |

## 关键发现（顺带命中项目现有缺陷）

1. **项目 `login_remote.py` 的锚点有错/过时**：
   - `SEL_QR_IMG = "#animate_qrcode_container img"` → 实测二维码已改 **SVG**，`img` 命中 **0**（真实容器内 13 个 `<image>`）。
   - 注释把 `#douyin_login_comp_btn_id` 标为「验证码登录」tab，**实为「登录」提交按钮**。
   - 「扫码登录/验证码登录」真实是 `<span class="C6OZQwMA">`（**哈希类名、无 id**）——正是最该用「自适应/文本」兜底的对象。
2. **登录面板 DOM 依入口/状态而变**（本次无头首屏无「一键登录」，是二维码 tab）；这本身就说明「纯硬编码锚点」不可靠。
3. Scrapling 接口边界：`find_by_text()` 唯一命中时返回**单元素**（非列表）；`Selector` 包装无 `getroottree()`，需取 `_root`。

## v1 → v2 修正（实测暴露的真误命中）

v1 用 `get_all_text()`（含子孙文本）校验 ⇒ **父容器**（面板同时含两段文案）同时命中
`tab_scan` 与 `tab_sms` ⇒ 两者解析到**同一元素**。v2 改为：
**「自身直接文本」判据 + 候选唯一性（>1 即拒绝，fail-closed）**。

## 建议的落地形态

- **仅 parser 层**：`scrapling`（base，BSD-3-Clause，~7 依赖，无浏览器）——不引 `fetchers` extra。
- **降级链**：① Scrapling 自适应(旧选择器) → ② 自身文本锚点 → ③ 结构域(属性) → ④ 报错。
- **硬约束**：每个候选必须过**确定性校验器**（tag/自身文本/属性/祖先域），且**唯一命中**；
  否则**拒绝返回**（宁缺勿错）。绝不把「相似度猜测」当确定判据。
- **桥接**：定位 → 绝对 XPath → 既有 `page.locator(xpath)`（复用项目已实测的「真实鼠标点击」）。
- **作用域**：自适应库须**按账号隔离**（不能跨账号共享漂移结果），与单 profile 铁律对齐。

## Verdict: VALIDATED

### What worked
- 自适应定位在 id/name/class 全漂移后**仍能重定位**（合成 + 真实 DOM 双证）。
- 加「确定性校验 + 唯一性」后**误命中为 0**（含导航栏同名干扰）。
- 与真实 Camoufox 的桥成立（定位 XPath → 可见可点元素）。

### What didn't / 未验证
- **未**在项目真实 GUI 有头登录流程里端到端跑（仅无头首屏 + 合成页）。
- 文案本身被改写（如「扫码登录」→「二维码」）时，文本策略会 MISS（**宁缺勿错**，需补确定判据或接口信号）。
- Scrapling 自适应库默认按**域(tld)**存储——与「单 profile/多账号」铁律冲突，落地须改 per-account。

### Recommendation for the real build
1. 先接线到 `login_remote.py`：把 `SEL_*` 定位改成「自适应兜底 + 校验器」，**保留现有确定性判据为第一优先**。
2. 每个 target 的 identifier 建**基线播种**（首次成功定位即 `auto_save`）。
3. 补机械门禁：注入「漂移 DOM」必须仍命中；注入「同名错域」必须被拒（负控）。
4. 与项目铁律核对：单 profile 作用域、只读不写真实 profile、不引新浏览器栈。

## 复现

```bash
# 解释器需：scrapling(base) + （真机部分）camoufox/playwright
python verify_offline.py     # 离线，无浏览器
python real_dom_test.py      # 用 _live/live_login.html（真机捕获的快照）
python live_probe.py         # 真机捕获（无头临时 profile，只读）
python bridge_live.py        # 真机闭环（定位->XPath->真实元素可见性/几何框）
```

## 文件
| 文件 | 作用 |
|---|---|
| `locator.py` | 核心：Target 定义 + 确定性校验器 + 自适应降级链（v2） |
| `verify_offline.py` | 离线可复现验证（S3/S4，含负控） |
| `live_probe.py` | 真机捕获登录页 DOM（只读） |
| `real_dom_test.py` | 真实 DOM + 人为改版后的重定位 |
| `bridge_live.py` | 真机闭环（定位→真实元素可见性/几何框） |
| `_live/live_login.html` | 真实登录页 DOM 快照（证据） |
| `_live/bridge_report.json` | 闭环读数（5/5） |
