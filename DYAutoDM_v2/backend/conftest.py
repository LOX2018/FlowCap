"""
测试隔离根门禁（conftest）。

## 根因与修法

`database._db_path()` 的路径来源优先级是：
    member_ctx（已登录）→ vbrowser.app_root() → config.settings.data_dir → "data"
在**测试进程**里 `app_root()` 会解析成**源码树**（DYAutoDM_v2/），于是任何
碰数据库的测试都会把 `dyautodm.db` 写进 `DYAutoDM_v2/data/` —— 污染源码树，
直接触发铁律门禁 R1「源码树无数据根内容」。

此前按「逐个测试补 `DY_APP_ROOT`」修过两轮（M-26 / M-27 及后续），但**只要
新写一个测试忘了补就会复发** —— 这是打地鼠，不是修根因。

现改为**全局兜底**：本 conftest 在**导入任何被测模块之前**把 `DY_APP_ROOT`
指向一个临时目录。这样：
  · 已自带隔离的测试：它们自己设的值**优先**，不受影响（本模块不覆盖已设值）；
  · 未隔离的存量测试：自动获得隔离，不再污染源码树。

⚠️ 为什么是 conftest 而不是改 `_db_path()`：后者是**生产代码**，为测试便利
改生产路径回退属于「测试需求泄漏进产品」。隔离是测试的职责。
"""
import os
import tempfile
from pathlib import Path

# pytest 会在收集阶段导入本文件，早于任何 test_*.py 及其依赖 ⇒ 时机正确。
# 仅当调用方**没有**显式指定时才兜底（显式优先，绝不覆盖）。
if not os.environ.get("DY_APP_ROOT"):
    _root = Path(tempfile.gettempdir()) / "dyautodm_test_app_root"
    _root.mkdir(parents=True, exist_ok=True)
    os.environ["DY_APP_ROOT"] = str(_root)
