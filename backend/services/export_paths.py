# -*- coding: utf-8 -*-
"""导出目录分类（**唯一真源**，2026-10-02 用户要求）。

## 为什么需要这个模块（真实问题）

系统页此前只有一个 `system.export_dir` 字段 ⇒ **所有**导出物（备份包 json、
ChatLab 会话 jsonl、统计 xlsx、聊天长图 png）**全落进同一个目录**，用户在
资源管理器里看到的是一堆混在一起的扩展名，无法按用途找文件。

用户原话：「文件导出目录也需要进行细分图片、表格这些类型要划分好」。

⇒ 引入 **一个根目录 + 固定分类子目录** 的两级结构，根目录仍由配置中心
`system.export_dir` 控制（留空 = `<app_root>/exports`）。

## 分类（SSOT，勿在调用点各写一份）

| key | 目录名 | 装什么 | 写入方 |
|---|---|---|---|
| `backup` | 备份 | 配置与业务数据备份包（`.json`） | `services/backup.py: write_export` |
| `chat` | 聊天记录 | ChatLab 会话导出（`.json` / `.jsonl`） | `services/chatlab_export.py: export_chatlab` |
| `table` | 表格 | 统计与线索表格（`.xlsx` / `.csv`） | `api/tasks.py: export_stats` |
| `image` | 图片 | 聊天长图 / 选区长图（`.png` / `.html`） | `api/messages.py: /render/png`、`/render/html`（`save=true`） |

## 契约

1. **根目录** = 配置中心 `system.export_dir` → 空则 `<app_root>/exports`。
2. **子目录名用中文**：桌面应用面向中文用户，资源管理器里一眼可辨；
   key（`backup`/`chat`/…）只作代码与 API 的稳定标识。
3. **只创建、不清理**：分类目录按需 `mkdir(parents=True, exist_ok=True)`；
   清理是用户自己的事（本模块**永不删**用户文件）。
4. **失败一律抛出、不静默退化**（2026-10-03 补）：`_app_root()` 旧实现
   `except: pass → os.getcwd()`，而 cwd 随进程启动方式漂移 ⇒ 导出物落到
   `<项目>/backend/exports` 之类的地方，用户毫无提示。cwd 已彻底移出兜底链。
5. **安全取文件**：`safe_file()` 复用「拒绝路径分隔 + resolve 后目录包含性」
   双重判据（承 `chatlab_export._safe_export_file` 的既有教训：
   导出文件名含**中文昵称**，不能用 ASCII 白名单，否则恒 404）。
   反向也已归位：`chatlab_export._safe_export_file` 委派本函数，
   全仓**只有这一份**安全判据。
"""
from __future__ import annotations

import os
from pathlib import Path

# key -> (目录名, 说明)
CATEGORIES: dict[str, tuple[str, str]] = {
    "backup": ("备份", "配置与业务数据备份包（.json）"),
    "chat": ("聊天记录", "ChatLab 会话导出（.json / .jsonl）"),
    "table": ("表格", "统计与线索表格（.xlsx / .csv）"),
    "image": ("图片", "聊天长图 / 选区长图（.png / .html）"),
}

# 兼容旧调用点传入的子目录名（`chatlab_export.default_export_dir("chatlab")`）
_ALIAS = {"chatlab": "chat", "images": "image", "images_export": "image"}


def _app_root() -> Path:
    """应用数据根（可写）。**解析失败即抛，绝不静默退化。**

    ⚠️ 2026-10-03 修正：旧实现是 `except Exception: pass` → 回落
    `os.environ.get("FLOWCAP_APP_ROOT") or os.getcwd()`。可 cwd 取决于**进程怎么被启动**
    （桌面快捷方式 / 终端 / 打包 exe 三者各异）⇒ 同一套安装会把导出物写到
    `<项目>/backend/exports` 而不是项目根，用户在别处找不到文件且**毫无提示**。
    实测复现：`except` 生效后 `root()` 静默返回 `...\FlowCap\backend\exports`
    并把目录建了出来，零异常零日志 —— 与 `root()` docstring 里「**不吞错**」
    的声明直接矛盾（声明成了摆设）。

    解析顺序（与 `vbrowser.app_root()` 一致，只是不再绕道 `auto_dm.accounts`）：
      1. 环境变量 `FLOWCAP_APP_ROOT`（是目录即用 ⇒ **完全不触发 import**）；
      2. `auto_dm.vbrowser.app_root()`（**定义处**）。旧实现绕道
         `auto_dm.accounts` 属纯负担：`app_root` 只是它的 re-export
         （`auto_dm/accounts.py:30`），为拿这一个函数拉起 379 个模块，
         还额外给 except 造了一个可以悄悄吃掉失败的落点。

    两步都拿不到 ⇒ **抛**，由 API 层转成 4xx/5xx 如实上报。
    """
    ov = os.environ.get("FLOWCAP_APP_ROOT", "").strip().strip('"')
    if ov and os.path.isdir(ov):
        return Path(os.path.abspath(ov))
    from auto_dm.vbrowser import app_root as _vb_app_root

    return Path(_vb_app_root())


def root() -> Path:
    """导出根目录：配置中心 `system.export_dir` → 空则 `<app_root>/exports`。

    ⚠️ **不吞错**（与旧 `backup._default_export_dir` 不同）：配置里写了个
    不可用的路径却静默回落默认目录，用户会在别处找不到文件且毫无提示。
    这里创建失败直接抛，由调用方（API 层）转成 4xx/5xx 如实上报。
    该声明要求 `_app_root()` 也不许静默退化 —— 旧 `_app_root()` 的
    `except: pass → cwd` 正是从背后捅穿它，现已一并去掉。
    """
    from services import app_config as _ac

    v = str(_ac.get("system", "export_dir", "") or "").strip()
    base = Path(v) if v else (_app_root() / "exports")
    base.mkdir(parents=True, exist_ok=True)
    return base


def dir_for(category: str) -> Path:
    """某分类的导出目录（按需创建）。未知分类回落到根目录。"""
    key = _ALIAS.get(category, category)
    if key not in CATEGORIES:
        return root()
    d = root() / CATEGORIES[key][0]
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_file(category: str, filename: str) -> Path | None:
    """把文件名解析为该分类目录内的安全路径；越界 / 不存在返回 None。

    判据（承 `chatlab_export._safe_export_file`）：
      ① 显式拒绝路径分隔符与 NUL；
      ② **以 resolve 后的目录包含性为准**（权威判据，防 `..` 与符号链接逃逸）。
    """
    name = (filename or "").strip()
    if not name or name in (".", ".."):
        return None
    if any(c in name for c in ("/", "\\", "\x00")):
        return None
    base = dir_for(category).resolve()
    p = (base / name).resolve()
    try:
        p.relative_to(base)
    except ValueError:
        return None
    return p if p.is_file() else None


def all_dirs() -> dict[str, dict]:
    """全部分类的实际路径（供前端系统页展示，用户要能看见落点）。"""
    out: dict[str, dict] = {}
    for key, (name, desc) in CATEGORIES.items():
        try:
            out[key] = {"key": key, "name": name, "desc": desc,
                        "dir": str(dir_for(key))}
        except Exception as e:  # noqa: BLE001
            # 单个分类建目录失败不该让整页 500 —— 如实标注状态
            out[key] = {"key": key, "name": name, "desc": desc,
                        "dir": "", "error": f"{type(e).__name__}: {e}"}
    return out
