# 源项目追踪（Upstream Tracking）

> 本项目多处「借鉴 / 迁移 / 嵌入」了外部开源项目。上游一旦变更
> （接口改名、反检测策略调整、风控对策更新），本项目对应实现就可能失效
> —— **而上游的更新说明往往已经写明了根因和解法**。
>
> 因此：**排查疑难问题前先跑一次本脚本**，看上游有没有相关更新。

## 文件

| 文件 | 作用 |
|---|---|
| `docs/upstream_sources.json` | **清单**（人工维护）：每个源项目 id / repo / 地址 / 类型 / 借鉴内容 / 落点 |
| `docs/upstream_baseline.json` | **基线**（脚本自动维护）：上次检查时的 `pushed_at` + `head_sha`。首次运行自动创建 |
| `scripts/track_upstream.py` | **追踪脚本** |

## 用法

```bash
cd DYAutoDM_v2

# 检查更新（打印报告）
python scripts/track_upstream.py

# 附带最近 5 条提交（看上游在改什么）
python scripts/track_upstream.py --commits 5

# 只查指定项目
python scripts/track_upstream.py --only douyin_chat_export,patchright

# 输出 JSON（供其他工具消费）
python scripts/track_upstream.py --json

# 确认无碍后更新基线（否则下次仍会报同一批更新）
python scripts/track_upstream.py --update
```

## 状态含义

| 标记 | 含义 | 动作 |
|---|---|---|
| 🔔 有更新 | 上游 `pushed_at` 或 `head_sha` 变了 | **对照 `landing` 落点评估影响**，变更记入知识库 |
| 🆕 新建基线 | 首次见到（无基线） | 跑 `--update` 落基线 |
| ✅ 无变化 | 与基线一致 | 无需动作 |
| ⏭ 跳过 | `track=false` 或缺 repo 地址 | 补地址 |
| ⏳ 限流 | GitHub 匿名 API 60 次/小时用尽 | 等 1 小时 / 换 IP / 配 token |
| ⚠️ 失败 | 仓库不存在或改名 | **修正清单地址** |

## 必看项（与本项目强耦合）

| 项目 | 为什么必看 |
|---|---|
| **TeamBreakerr/douyin-chat-export** | 同类问题的第三方实现。其 commit message 记录了抖音前端的**每次改版应对**（虚拟滚动、short_id、field14、headless 跑法）——排查抖音侧问题时**第一个看它** |
| **adryfish/fingerprint-chromium** | 指纹内核。升版会改模块路径（`/ts_sign`、`/s_sdk_sign_data_key/web_protect`）与 JS 特征 |
| **Kaliiiiiiiiii-Vinyzu/patchright-python** | 反检测分支。⚠️ **2026-09-24 订正**：原记「实测其下 `add_init_script` **静默失效**（见知识库 08 §31.3），升版需复验」为**编号+结论双漂移**（`31.3` 实为 `工作记忆/05f_源项目对照与租约.md`，原意是窗口最小化节流；patchright 仅次要嫌疑）。本机复测（`artifacts/UP_L3_浏览器依赖升级评估_20260923.md` §四）1.62.3/1.63.0 下**均正常生效**，真因是**读取侧世界不匹配**。升版无收益（且 camoufox 硬钉 `<1.63`） |
| **cv-cat/DouYin_Spider** | 签名/接口基座。抖音改签名算法时**必须先跟这里** |
| **lyu0805/OpenBrowser** | 指纹/字体层对策（近期持续在改字体探测出口），与我们的环境门阀相关 |

## 限流对策

GitHub 匿名 API 限 **60 次/小时/IP**（本清单 11 个项目 × 2 次请求 ≈ 22 次/轮）。

1. **本地代理**：`set DY_UPSTREAM_PROXY=http://127.0.0.1:10808`（脚本也会自动探测 10808/7890/7891/1080/3128/8888）
2. **Token**（未实装）：`Authorization: Bearer <token>` 可提到 5000 次/小时
3. 等 1 小时后重跑（脚本会提示恢复时间）

## 维护约定

1. **新增借鉴 = 同时更新清单**：往 `upstream_sources.json` 加一条（id/repo/kind/borrowed/landing）
2. **修正错误地址**：脚本报 404 时改清单的 `repo`
3. **每轮上游变更 → 记知识库**：在 `工作记忆/*.md` 对应章节追加「上游变更与本项目影响」
4. 本机制**只读写 `docs/` 下两个 JSON**，不触碰业务代码
