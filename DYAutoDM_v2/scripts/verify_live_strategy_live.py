# -*- coding: utf-8 -*-
#
# ╔══════════════════════════════════════════════════════════════════════╗
# ║  [退役声明 · RETIRED]  此文件已退役并**禁止执行**                    ║
# ║                                                                      ║
# ║  退役原因: 判据写死 v0.43.93，策略体系已重构为 v0.44.x ADR-002 §5.4  ║
# ║            策略中心，无法在当前产品上跑通（H-8 普查实测）。           ║
# ║                                                                      ║
# ║  替代方案: scripts/verify_live_strategy.py（TestClient 层）          ║
# ║            与 ADR-002 §5.4 策略中心测试规范                          ║
# ║                                                                      ║
# ║  2026-09-23 P3-7 修复（原缺陷三连，实测）：                          ║
# ║    ① 退役横幅只是注释，**不阻止执行**；                              ║
# ║    ② 无 `if __name__ == "__main__"` guard，import 即执行；           ║
# ║    ③ 模块级裸代码**会无条件对 :8000 实机部署发请求**（建/改/删策略）  ║
# ║       —— 退役脚本仍能改坏真实环境，属危险假验收资产。                ║
# ║  现改为：**真退役** —— 模块级零副作用，任何入口（import / 直接运行）  ║
# ║    都在 `__main__` guard 内立即 SystemExit(2)，绝不发网络请求。       ║
# ╚══════════════════════════════════════════════════════════════════════╝
#
"""**已退役**：对部署产物（:8000）跑真机 HTTP 验证 —— v0.43.93 纯策略契约。

> 本文件不再执行任何验收逻辑。历史判据全文见 git 历史
> （`git log --follow -- DYAutoDM_v2/scripts/verify_live_strategy_live.py`）
> 与归档 `artifacts/_retired_scripts_H8/verify_live_strategy_live.py`。

原判据（仅供追溯，**已失效**）：
  1. 版本/端点面：0.43.93；config-tags 4 端点；target-rooms **不存在**
  2. 多策略并存：建两条 → 列表两条 → 参数互不覆盖
  3. 策略体**零身份字段**：不返回 room_id / live_url / force_rescan
  4. 中文策略名可作 id（URL 编码往返）
  5. restart/apply 携带 {room_id, account} 上下文；respond 不谎报
  6. 删除只删自己（无跨模块解绑副作用）

现判据由 `scripts/verify_live_strategy.py` 承接。
"""

RETIRED = True
RETIRED_REASON = "判据写死 v0.43.93；策略体系已重构为 v0.44.x ADR-002 §5.4 策略中心"
SUCCESSOR = "scripts/verify_live_strategy.py"


def _refuse():
    """真退役：立即退出，**不发任何网络请求**。"""
    raise SystemExit(
        f"[RETIRED] {__file__} 已退役，拒绝执行：{RETIRED_REASON}。\n"
        f"请改用承接者：{SUCCESSOR}")


if __name__ == "__main__":
    _refuse()
