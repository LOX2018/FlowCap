# 测试隔离失效 → 全表 DELETE 删空生产库（v0.46.60）

> **结论先行**：7 个测试的数据库隔离是**静默失效**的（`setdefault` 不覆盖已有环境变量），
> 其中 4 个带**无 WHERE 的全表 DELETE**。本轮实测已真实删空生产库 `tasks` 表并插入测试行，
> 已用基线备份完整还原。16 文件修复，89 项测试全绿，版本门禁 6/6 齐平，铁律门禁 20/20。
> 提交 `945c660`。

---

## 一、缺陷模式

```python
os.environ.setdefault("DY_APP_ROOT", _ROOT)   # ← 陷阱
```

`setdefault` **只在键不存在时写入**。而在 Hermes 会话 / 部署环境里 `DY_APP_ROOT`
**几乎总已存在**（指向真实部署根 `C:\temp\dyautodm_design`）⇒ 隔离**静默失效**，
测试直连生产库。

**为什么危险**：它不像崩溃那样立刻可见——测试**照常通过**，只在后台删数据。
实测 `test_lead_nickname` / `test_leads_backfill` / `test_leads_window` 三个文件，
全程显示 `OK`，实际写的是生产库。

**同类缺陷一次性修完**（不逐文件拉锯）：AST 扫描全仓 22 个写库测试文件，
统一判定后再动手。

## 二、实测取证（生产库污染）

修复前 `test_unified_task_model` 的连接路径：

```
SQLite 已初始化: C:\temp\dyautodm_design\members\me3249f790286c4a0\data\dyautodm.db
                                ↑ 生产库，非隔离根
```

其 `DELETE FROM tasks`（无 WHERE）已执行，结果：

| 表 | 修复前 | 备份基线 | 差异 |
|---|---|---|---|
| `tasks` | 1 行（`acct='acct_e'` 测试占位） | 3 行真实 | ❗ 删掉 3 条真实任务 + 插入测试行 |
| `crawl_history` | 47 | 46 | ❗ 多 1 行测试插入 |

删除的 3 条真实任务（全部 `acct='小助理'`）：

```
1790880027717  live_id=''     status='stopped'
1790917347041  live_id=''     status='finished'
1791100861127  live_id='200763865498' kind='crawl' status='finished'
```

## 三、恢复（脚本 `restore_prod_db.py`）

恢复纪律：**只从已核验的基线备份取数据，不做猜测**；写前再备份一次；只还原缺失行、
只删带测试账号占位符（`acct_e`/`acct_ce`/`acctA`/`acctB`）的行；逐条打印不静默。

写前二次备份：`C:\Users\LOX\AppData\Local\Temp\lead_audit\dyautodm.db.pre_restore_20261004_185525`

恢复后终态核对（全 ✅）：

```
ai_leads           1   （基线 1）
dm_conversations   0   （基线 0）
dm_messages       303   （基线 303）
tasks               3   （基线 3）
crawl_history      46   （基线 46）
dm_uid_sink        43   （基线 43）
真实线索: (1, '3759948506333804', '13037765888', '13037765888微信')  完好
```

## 四、修复

新增 `test_isolation.py` —— **唯一入口**：

```python
env_isolate(tag, root=BACKEND)   # 显式赋值 + 唯一 tag 隔离根 + teardown 自动清理
```

| 修前 | 修后 |
|---|---|
| `os.environ.setdefault("DY_APP_ROOT", _ROOT)` | `env_isolate("id_uniq", root=_ROOT)` |
| `os.environ["DY_APP_ROOT"] = _root`（非唯一名） | `env_isolate("lead_nick", root=_ROOT)` |
| `isolate(root=_ROOT)`（非唯一名） | `isolate("live_ai_send", root=_ROOT)` |

顺带修掉 `test_id_uniqueness` 用非唯一名泄漏 `C:\Temp\dyautodm_uniq`（含测试
`task_stats` 表）的残留。

9 个测试文件改用 `env_isolate`，高危 4 个（带全表 DELETE）：
`test_id_uniqueness` / `test_task_detail` / `test_task_stats` / `test_unified_task_model`。

## 五、验证

| 验证项 | 结果 |
|---|---|
| 11 个相关测试 | **89 项全绿**，无跨测试串扰 |
| 连接路径逐个 grep | 全部落隔离根（`id_uniq` / `lead_nick` / `task_detail` / `task_stats` / `unified_task` / `reply_purify` / `live_ai_send` / `cfgtest_*`） |
| 全仓 AST 扫描 22 个写库测试 | 剩余「未用 env_isolate」者**逐个实测**全部连临时库（`test_delivery_verify` / `upstream_p3-p5` 等），无高危残留 |
| 版本门禁 | 6 处齐平 `0.46.60` |
| 铁律门禁 | 20/20 通过 |
| EOL 检查（`core.autocrlf=true` 仓库） | `--numstat` 与 `--ignore-all-space --numstat` 逐文件相等 ⇒ 无纯 EOL 重写 |
| 我的文件集删除行 | 0（全仓 18 个删除均为历史 `artifacts/` spike 文件，未暂存、非我造成） |

## 六、判据（可复用的经验）

1. **`setdefault` 做隔离 = 假隔离**。隔离类环境变量必须**显式赋值**。
   自检：修完 grep 一次连接路径字符串，若出现部署根 `members\` 段即失败。
2. **「测试通过」≠「没污染」**。静默失效的隔离不会让测试变红，只会让数据变少。
   对带全表 DELETE 的测试，**必须** grep 一次连接路径。
3. **AST 扫描优于 grep**：grep 会命中注释/字符串字面量造成假阳性
   （本轮我注入的说明注释里含 `setdefault("DY_APP_ROOT", ...)` 字样，
   让 grep 误报 3 个「高危」，实际全是已修复的文件）。
4. **同类缺陷一次性修完**：第一次发现 `setdefault` 就全量扫描 22 个文件再动手，
   而非「修一个 → 跑一次 → 又发现一个」。
5. **测试污染生产库时的恢复纪律**：只从已核验备份取数据；写前再备份一次；
   只按测试占位符精准删行；逐条打印。恢复后必须**逐表核对行数 + 抽查关键业务行**。
6. **别相信门禁绿灯**：本轮版本门禁与铁律门禁全绿，但生产库已丢数据——
   现有门禁**没有任何一条**检查「测试是否连对了库」。
   建议补一条机械门禁：扫描 `test_*.py` 中 `DELETE FROM <表>` 无 WHERE 的，
   断言同文件存在 `env_isolate(`。

---

## 附：备份与脚本位置

| 项 | 路径 |
|---|---|
| 基线备份（还原数据源） | `C:\Users\LOX\AppData\Local\Temp\lead_audit\dyautodm.db.base_20261004_184106` |
| 写前二次备份 | `C:\Users\LOX\AppData\Local\Temp\lead_audit\dyautodm.db.pre_restore_20261004_185525` |
| 恢复脚本 | `…\profiles\lox\cache\scratch\restore_prod_db.py` |
| 隔离扫描脚本 | `…\profiles\lox\cache\scratch\scan_isolation_strict.py` |
| 隔离 helper | `DYAutoDM_v2/backend/test_isolation.py` |
