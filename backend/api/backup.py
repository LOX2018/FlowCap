# -*- coding: utf-8 -*-
"""备份路由（系统页「备份」子板块）。

端点
----
- `GET  /api/backup/scopes`            范围清单（供勾选）
- `POST /api/backup/export`            按范围导出并落盘 → 返回路径/字节数
- `GET  /api/backup/download/{name}`   下载导出文件
- `POST /api/backup/import`            上传备份包按范围导入（覆盖，失败回滚）
- `GET  /api/backup/export_dir`        当前导出目录（来自 general.export_dir）

写操作仅在 import；export 为纯读 + 写一个新文件（不覆盖任何业务数据）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel

from services import backup as bk

router = APIRouter()


@router.get("/scopes")
async def get_scopes() -> dict:
    return {"ok": True, "scopes": bk.scopes_meta()}


@router.get("/export_dir")
async def get_export_dir() -> dict:
    """导出根目录 + **分类子目录**（2026-10-02 用户要求按类型细分）。

    前端系统页据此展示「根目录可改 + 四个分类落点」，让用户看得见文件去哪了。
    单个分类建目录失败不影响其余分类（`all_dirs` 已逐项兜住）。
    """
    from services import export_paths

    try:
        root = str(export_paths.root())
        err = ""
    except Exception as e:  # noqa: BLE001
        root, err = "", f"{type(e).__name__}: {e}"
    return {"ok": not err, "dir": root, "error": err,
            "categories": export_paths.all_dirs()}


@router.get("/export_file/{category}/{filename}")
async def download_export_file(category: str, filename: str):
    """下载某个分类目录下的导出文件（替代旧的无分类 `download/{name}`）。

    分类目录**只创建、不清理**，故导出物可能已积累；给用户一个直接取回的口子。
    安全判据全部在 `export_paths.safe_file`（拒分隔符 + resolve 包含性）。
    """
    from services import export_paths

    if category not in export_paths.CATEGORIES:
        raise HTTPException(400, f"未知分类 {category}")
    p = export_paths.safe_file(category, filename)
    if p is None:
        raise HTTPException(404, "文件不存在")
    return FileResponse(str(p), filename=p.name)


class ExportBody(BaseModel):
    scopes: list[str] = []
    filename: str = ""


@router.post("/export")
async def do_export(body: ExportBody) -> dict:
    pkg = bk.export_scopes(body.scopes or [])
    # 统计一下体量，便于 UI 提示
    n_kv = len(pkg.get("kv") or {})
    n_tbl = sum(len(v.get("rows") or []) for v in (pkg.get("tables") or {}).values())
    try:
        r = bk.write_export(pkg, body.filename or "")
    except Exception as e:  # noqa: BLE001
        logger.error(f"[BAK-001] 导出落盘失败: {e}")
        raise HTTPException(500, f"导出失败：{e}") from e
    logger.info(f"[backup] 导出 scopes={pkg.get('scopes')} -> {r['path']}")
    return {"ok": True, "path": r["path"], "bytes": r["bytes"],
            "filename": r["path"].replace("\\", "/").split("/")[-1],
            "category": "backup",
            "scopes": pkg.get("scopes"), "kv_keys": n_kv, "table_rows": n_tbl}


@router.get("/download/{filename}")
async def download(filename: str):
    """下载**备份分类**下的导出文件（保留旧端点，前端下载按钮仍走这里）。

    ⚠️ 2026-10-02：导出物已按类型分目录（`备份/`），故本端点不能再拿
    文件名直接拼根目录 —— 那会 404。委派 `export_paths.safe_file` 走分类目录。
    """
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "非法文件名")
    from services import export_paths

    p = export_paths.safe_file("backup", filename)
    if p is None:
        raise HTTPException(404, "文件不存在")
    return FileResponse(str(p), media_type="application/json", filename=p.name)


@router.post("/import")
async def do_import(file: UploadFile, scopes: str = "") -> dict:
    """上传备份包导入。`scopes` 为逗号分隔的限定范围（空 = 包内全部）。"""
    raw = await file.read()
    try:
        pkg = json.loads(raw.decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"备份包不是合法 JSON：{e}") from e
    want = [s.strip() for s in scopes.split(",") if s.strip()] or None
    r = bk.import_scopes(pkg, want)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error") or "导入失败")
    logger.info(f"[backup] 导入 scopes={r.get('imported_scopes')}")
    return r
