# 案例归档：专用 Camoufox（禁用 vb_chromium 回退），v0.44.2

**日期**：2026-09-20
**版本**：v0.44.2（design/better-douyin，运行实例 `C:\temp\dyautodm_design`）
**用户拍板**：「先禁止使用 vb_chromium，专门使用 Camoufox 测试」
**状态**：内核选择已固化到配置真源；Camoufox 失败**禁止静默回退**

---

## 一、本轮要解决的问题

用户在 GUI 里启动应用后，**实际仍走 Chromium**，导致"换了内核"这件事
始终没真正生效——而这**全程无声**。

### 根因（实测踩坑）

| 路径 | 是否继承 `DY_BROWSER_KERNEL=camoufox` |
|---|---|
| 我在 shell 里跑 python 脚本 | ✅ 继承（所以我的验证都是 Camoufox） |
| **GUI 双击启动 → sidecar** | ❌ **不继承**（所以用户跑的一直是 Chromium） |

**教训**：以**环境变量**作为内核开关，在"GUI 启动"这条路径上**天然失效**。
凡"必须生效"的行为，必须落在**配置真源**（文件），不能只靠进程环境。

> 这与用户的「显式配置原则」完全一致：行为由配置显式选择决定，
> 不依赖本机/进程等外部可变状态。

## 二、修复清单（v0.44.2）

| # | 文件 | 改动 |
|---|---|---|
| 1 | `backend/auto_dm/config.py` | **新增 `DY_BROWSER_KERNEL = "camoufox"`**（配置真源，含取值说明与踩坑注记） |
| 2 | `backend/vbrowser.py` | `launch_async` / `launch_sync`：启用 camoufox 时**禁止回退** Chromium，失败抛 `BCC-058`（不再静默降级） |
| 3 | `backend/test_browser_visibility_guard.py` | 新增 `TestL_CamoufoxNoFallback`（3 项：配置声明、判定不依赖环境变量、两出口均含禁止回退分支） |

### 「禁止回退」的设计理由

静默降级会把"内核没切过去"变成**无声事实**（本轮就是这么被坑的）。
因此：**启用 Camoufox 后，启动失败必须响亮**（抛 BCC-058），
宁可报错，不可假装成功——这是「诚实的失败」原则。

## 三、Chromium 是否还有其他硬依赖（排查结论）

- `browser_daemon.py` 中 `_backend == "exe"` 的分支在 camoufox 下**自然不命中**；
- 未发现 CDP 端口（9222）等 Chromium 专属硬依赖；
- JS 注入已在 camoufox 模式下禁用（v0.44.0）。

## 四、验证

- 守卫测试 **44/44 PASS**。
- 不设任何环境变量时：`camoufox_enabled(cfg) == True`
  （证明配置真源生效，GUI 路径也会走 Camoufox）。
- Camoufox 有头窗口实测：页面显示「尚进工伤小助理 一键登录」——
  **登录态保持、无弹窗**。

## 五、仍待解决（诚实记录）

**HTTP 拉会话内容被拒**：`get_message_by_init` 返回 75 字节
`unexepcted session length`（`cmd 609`）。

取证结论（**与内核无关**）：

| 日期 | 失败(RECV-012) | 成功提取 | 失败率 |
|---|---|---|---|
| 09-16 | 0 | 24 | 0% |
| 09-17 | 1 | 9 | 10% |
| 09-18 | 6 | 2 | 75% |
| 09-19 | 21 | 17 | 55% |
| 09-20 | 8 | 1 | 89% |

→ **换 Camoufox 之前就在恶化**，是既有缺陷，不是迁移引入。
WS 长连接本身正常（`connected=true`, `disconnects=0`, `hb_failed=0`），
失败的是**HTTP 拉历史/会话内容**这条路径。

**下一步方向**（未验证，勿当结论）：
该报错疑似与 sessionid ↔ 设备指纹配对的**会话合法性**判定有关；
09-19 曾 17 次成功，说明并非永久失效。需按「迭代止损律」换视角，
从上位（抖音 IM 协议层）取证，而非继续在重试/刷新 cookie 上打转。
