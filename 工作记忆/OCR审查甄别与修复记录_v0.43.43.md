# OCR 审查报告甄别与修复记录（v0.43.43）

> 报告来源：`D:\SJ  agent\DYchajian-review-design-better-douyin-{detailed.md,json}`
> 生成：2026-09-17 16:30，open-code-review v1.12.4，模型 deepseek-v4.1-flash
> 范围：`design/better-douyin` vs `main`，355 文件 / 1484 条意见
> 处理：**实测优先**——每条先验证真伪再动手（机器报告≠真实缺陷）

---

## 1. 范围甄别（先剔除不属于本项目的路径）

| 顶层路径 | 条数 | 处置 |
|---|---:|---|
| `DYAutoDM_v2/backend` | 740 | ✅ 纳入 |
| `DYAutoDM_v2/frontend` | 369 | ✅ 纳入 |
| `_ext_repos/DouYin_Spider-master` | 142 | ❌ **第三方对照库**，非本项目交付物 |
| `DYAutoDM_v2/scripts` | 133 | ✅ 纳入 |
| `DYAutoDM_v2/src-tauri` | 21 | ✅ 纳入 |
| 其它（docs/.gitignore/LLM-Wiki/根脚本） | 79 | ⚠️ 非产品代码，低优先 |

→ 本项目有效意见 **1312 条**（剔除第三方 172 条）。
其中 CRITICAL **38** / HIGH 197 / MEDIUM 666+ / LOW 545。

## 2. CRITICAL 抽样核验结果（实测，非采信）

用脚本 `scripts/_triage_ocr_critical.py` 批量核验，**6 真 / 3 误报**：

| 意见 | 核验方式 | 结论 |
|---|---|---|
| `pro_kb.py` logger 未定义 | 正则判 import/use | ✅ 真实（**我上轮修补引入**） |
| `vbrowser_window.py` 缺 `_ctypes` | 判 use/import | ✅ 真实 |
| `utils/data_util.py` 正则失效 | 实跑 `re.sub` | ❌ **误报**（非法字符被正确去除） |
| `features.py` 参数个数 | 比对定义/调用 | ✅ 真实 |
| `mcp/tools.py` 关键字参数 | 比对底层签名 | ✅ 真实（5 处） |
| `scripts/probe_img_*.py` 缺 os | 判 use/import | ❌ 误报（已 import） |
| `auto_dm.py:773` 未定义 config | 读源码 | ✅ 真实 |
| `builder/auth.py` 空 cookie 覆盖 | 读实现 | ✅ 真实 |
| `test_send_gate_config.py` 常量名 | 比对定义/引用 | ✅ 真实（6 断言错） |
| `login_capture.py:149` None 除法 | 读源码 | ❌ 误报（已有 `is not None` 保护） |

**误报率约 30%**（9 抽样中 3 条）→ 印证「必须逐条验证」。

## 3. 本轮已修复（13 项）

| # | 文件 | 问题 | 修复 | 验证 |
|---|---|---|---|---|
| 1 | `services/pro_kb.py` | logger 未导入（**我引入的回归**） | 补 `from loguru import logger` | 语法 OK |
| 2 | `vbrowser_window.py` | 拆分丢 ctypes 导入 | 补 `import ctypes as _ctypes` + `wintypes`，非 Win 置 None | 语法 OK |
| 3 | `features.py` | 多传 `to_user_id` 致 TypeError | 只透传游标 + 忽略告警 | 比对签名 |
| 4 | `core/auto_dm.py:773` | `config` 未定义致 NameError | 改 `self.force_rescan`（103/300 行确有定义） | 读源码确认属性存在 |
| 5 | `builder/auth.py` | 空 cookie 覆盖有效凭证 | 空串不覆盖，仅刷新签名 | 读实现 |
| 6 | `test_send_gate_config.py` | 6 条断言错（常量名+0.5 折扣） | 全部改正 | **6/6 通过**（原 4fail+2err） |
| 7-11 | `mcp/tools.py` ×5 | 参数名与底层签名不符 | `query=/num=/url=` 对齐真实签名 | 比对 13 个方法签名 |
| 12 | `dy_apis/client_video.py` | 丢 `@staticmethod` | 补齐 | **实机：装饰器确认 + 语义验证** |
| 13 | `dy_apis/client_live.py` | 同上 | 补齐 | 同上 |
| 14 | `dy_apis/client_user.py` | **硬编码他人 sec_uid** | 改用传入 `sec_id` | 全库扫无残留硬编码 |

**同类排查**：`scripts/_scan_missing_staticmethod.py` 全量扫描 dy_apis/core/services/daemon/auto_dm
→ **除上述 2 处外无其它**（把该类别清零）。

## 4. 仍待处理（择要）

- **CRITICAL 剩余**：`api/logs.py`（Windows 竞态）、`api/messages.py:78`（lease 未释放）、
  `core/live_hook.py:275`（protobuf 字段名）、`src-tauri/src/lib.rs:34`（Mutex 跨 await）、
  3 个前端 bug（accounts-page 双 setRole / platform-page 未传 id / AgentSection effect 覆盖 draft）
- **HIGH/MEDIUM/LOW**：共 1241 条，多为健壮性/可维护性，需分类批量处理
- **误报**：约 30%，不逐个修

## 5. 方法学结论

1. **机器报告必须逐条实测**——本次抽样误报率 30%，`login_capture`/`data_util` 两条
   若不验证就改，会**破坏本来正确的代码**。
2. **审查报告能发现真问题**——`pro_kb` logger（我引入的）、`client_user` 硬编码他人 ID、
   `auto_dm` NameError 都是真缺陷，且 2 条是我上轮修补的回归。
3. **修复后必须同类排查**——`@staticmethod` 丢装饰器是拆分事故，扫全库确认仅 2 处，
   避免"修一个漏一片"。
