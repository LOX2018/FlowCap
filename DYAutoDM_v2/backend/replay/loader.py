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
