# -*- coding: utf-8 -*-
"""回放样本加载器与清单（manifest）读取。

**fixtures 不可变**：每个样本在 `fixtures/manifest.json` 里登记 sha256，
加载时逐字节校验；不匹配一律拒绝（防止有人"顺手改一下样本"让用例变绿）。
清单即本层的**单一真值来源（SSOT）**：样本的期望读数也写在里面，
用例从清单读期望值，而不是各处硬编码数字（避免判据双写）。
"""

import hashlib
import json
import os

_FIX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
_MANIFEST = os.path.join(_FIX_DIR, "manifest.json")


class FixtureMissing(FileNotFoundError):
    """样本不存在（尚未录制）。"""


class FixtureTampered(RuntimeError):
    """样本 sha256 与清单不符 —— 样本被改动，拒绝加载。"""


def fixtures_dir() -> str:
    return _FIX_DIR


def manifest_path() -> str:
    return _MANIFEST


def _read_manifest() -> dict:
    if not os.path.exists(_MANIFEST):
        return {}
    with open(_MANIFEST, "r", encoding="utf-8") as f:
        return json.load(f)


def write_manifest(m: dict) -> None:
    """由 recorder 调用；写入即排序，保证 diff 稳定。"""
    os.makedirs(_FIX_DIR, exist_ok=True)
    with open(_MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(m, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def list_fixtures() -> list:
    return sorted(_read_manifest().keys())


def describe(name: str) -> dict:
    """返回该样本的清单条目（含 expected / provenance）。"""
    m = _read_manifest()
    if name not in m:
        raise FixtureMissing(f"清单中没有样本 {name!r}；现有：{list_fixtures()}")
    return m[name]


def fixture_path(name: str) -> str:
    entry = describe(name)
    return os.path.join(_FIX_DIR, entry["file"])


def load_fixture(name: str, verify: bool = True) -> bytes:
    """按清单加载样本字节流；默认校验 sha256。"""
    entry = describe(name)
    path = os.path.join(_FIX_DIR, entry["file"])
    if not os.path.exists(path):
        raise FixtureMissing(f"清单登记了 {name!r} 但文件不存在：{path}")
    with open(path, "rb") as f:
        blob = f.read()
    if verify:
        got = hashlib.sha256(blob).hexdigest()
        want = entry.get("sha256", "")
        if got != want:
            raise FixtureTampered(
                f"样本 {name!r} 已被改动：清单 sha256={want}，实测={got}。\n"
                f"若确为有意更新，请用 `python -m replay.recorder --update {name}` 重录。"
            )
        size = entry.get("size")
        if size is not None and len(blob) != size:
            raise FixtureTampered(
                f"样本 {name!r} 长度不符：清单 {size} 字节，实测 {len(blob)} 字节。"
            )
    return blob


def sha256_of(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


class FixtureUnavailable(AssertionError):
    """夹具缺失/不可用 —— **hard fail**（不再是 SkipTest 静默通过）。

    P3-1（2026-09-23 假绿修复）：夹具缺失此前用 `unittest.SkipTest`，
    而 loader 缺 manifest 返回 `{}` ⇒ 套件**仍 exit 0**（"没跑" 被当成 "通过"）。
    回放层的存在意义就是让回归可跑；夹具缺失是**交付缺陷**，必须红。
    """


def require_fixture(name: str) -> dict:
    """断言夹具存在且完整；缺失/篡改一律 **hard fail**（raise，不 skip）。

    用法（测试模块 setUpClass/setUpModule 顶部）::

        entry = return require_fixture("dm_read_fixture")
    """
    try:
        names = list_fixtures()
    except Exception as e:  # noqa: BLE001
        raise FixtureUnavailable(
            f"回放夹具清单不可读（fixtures/manifest.json 缺失或损坏）："
            f"{type(e).__name__}: {e}") from e
    if name not in names:
        raise FixtureUnavailable(
            f"回放夹具 {name!r} 缺失（清单现有：{names}）。"
            f"夹具缺失 = 功能未验收，必须 hard fail，不得 SkipTest 静默通过。"
            f"请用 `python -m replay.recorder` 录制后重跑。")
    entry = describe(name)
    # 完整性：sha256/size 必须齐备（防「登记了但无冻结指纹」的空壳条目）
    if not entry.get("sha256") or entry.get("size") is None:
        raise FixtureUnavailable(f"夹具 {name!r} 清单条目缺 sha256/size：{entry}")
    # 真跑一次加载 + 校验（篡改/截断即在此 red）
    load_fixture(name)
    return entry
