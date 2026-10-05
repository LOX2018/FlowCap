"""报错代码查询路由

GET /api/errcodes            -> 全量代码表（domain/meaning/cause/action/file/line）
GET /api/errcodes?domain=BCC -> 按域过滤
GET /api/errcodes?q=漂移     -> 按 meaning/cause/action 关键词过滤
GET /api/errcodes/{code}     -> 单码详情
"""
from fastapi import APIRouter, Query

from errcode import DOMAIN_INFO, all_codes, lookup

router = APIRouter()


@router.get("")
async def list_codes(
    domain: str | None = Query(default=None, description="按域过滤，如 BCC"),
    q: str | None = Query(default=None, description="关键词过滤 meaning/cause/action"),
):
    codes = all_codes()
    if domain:
        codes = [c for c in codes if c["domain"] == domain.upper()]
    if q:
        k = q.lower()
        codes = [
            c for c in codes
            if k in c["meaning"].lower() or k in c["cause"].lower() or k in c["action"].lower()
        ]
    return {
        "ok": True,
        "total": len(codes),
        "domains": {d: v[0] for d, v in DOMAIN_INFO.items()},
        "codes": codes,
    }


@router.get("/{code}")
async def get_code(code: str):
    c = lookup(code.upper())
    if not c:
        return {"ok": False, "error": f"未知代码 {code}"}
    return {"ok": True, "code": c}
