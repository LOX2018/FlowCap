# 修复批次所有权表（2026-09-29 · 依据 OCR v1.12.9 报告 31 条）

## 声明
- 本批次由父会话（Hermes）统筹；**版本升版与 git 提交由父会话最后统一做**，子 agent 一律禁止 git 写与改版本号。
- 数据根/浏览器/守护/打包：**本批一律不碰**（真机类验证留给用户）。
- 开工基线：HEAD=`4f81582`，版本 0.45.94；源码树无未提交改动（他人在制的 `工作记忆/00_交接卡待办台账.md` 未提交改动 **原样保留，不覆盖**）。

## 文件 → 桶（每文件只属一个桶，已反查无重叠）
| 桶 | 负责文件 | 覆盖条目 |
|---|---|---|
| A | backend/services/ai_reply.py（+ test_lead_disposition_guard.py） | [22]HIGH [9]MED [10]MED [8]MED |
| B | backend/api/tasks.py · backend/services/delivery_verify.py | [6]CRIT [11]LOW · [19]数字(注释) |
| C | backend/api/linkmic.py | [20]MED [7]MED [21]LOW |
| D | backend/auto_dm/dom_locator.py | [13]MED [14]LOW [15]LOW |
| E | backend/daemon/bcc_login.py | [16]LOW |
| F | frontend .../accounts/{accounts-page,LoginDialog,AccountDrawer}.tsx | [1]MED [2]LOW [5]LOW [3][4]LOW [12]LOW |
| G | frontend .../live/{live-page,live-shared}.tsx | [17]HIGH [18]MED [26]LOW · [19]数字(注释) |
| H | frontend .../settings/HighValueKeywordsSection.tsx | [23]MED [24]LOW [25]LOW [27]MED |
| I | scripts/migrate_fallback_pool.py · scripts/verify_lead_disposition_real.py | [28]MED [29]MED [30]MED [31]HIGH |

## 跨桶耦合处理
- **[19] 硬编码业务数字矛盾**（live-page.tsx:309 = 24 / live-shared.tsx:88 = 34 / delivery_verify.py:143 = 34）：
  **统一改为「不含具体数字的定性描述」**，G 桶负责前端两处、B 桶负责后端一处 ⇒ 无数字一致性要求，桶间解耦。
- 其余条目无跨桶共享文件。

## 不进本批的项（显式交代）
- OCR[16] 已在 E 桶；LOW 样式项（嵌套三元等）在 F 桶，属可选级。
- 需**真机/独占资源**的验证（连麦真实申请、BCC 漂移、事件循环冻结的端到端）：子 agent 只做单元/
  静态级，报告 §4 标注「待真机验证」。
- 我报告独有 C1（版本粒度）C2（台账损坏）C3（红线 27/20）：不在本批，父会话另行处置。
