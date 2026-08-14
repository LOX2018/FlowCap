"""设置路由

取代原版 WebBridge.saveConfig / _write_config_file。
关键改进：配置写回用 JSON 文件，不再字符串匹配 .py 源码。
"""
from fastapi import APIRouter

router = APIRouter()


@router.get("")
async def get_config():
    return {"ok": True, "config": {}}


@router.post("")
async def save_config(body: dict):
    """保存配置（写入 data/config.json，不写回 .py 源码）"""
    # TODO: 持久化
    return {"ok": True}
