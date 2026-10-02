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
    d = bk._default_export_dir()
    return {"ok": True, "dir": str(d)}


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
            "scopes": pkg.get("scopes"), "kv_keys": n_kv, "table_rows": n_tbl}


@router.get("/download/{filename}")
async def download(filename: str):
    # 防目录穿越：只允许文件名（无路径分隔符）
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "非法文件名")
    d = bk._default_export_dir()
    p = d / filename
    if not p.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(str(p), media_type="application/json", filename=filename)


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
