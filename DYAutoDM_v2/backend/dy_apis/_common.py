# -*- coding: utf-8 -*-
"""平台接口层 —— 域模块公共导入头（mixin 复用）

## 为什么独立（2026-09-15 共享提取）

阶段1 把单体 `douyin_api.py` 按业务域机械拆成 9 个 mixin 模块时，每个文件
都复制了**同一份导入头**（实测 17 个 import 行 × 10 个文件 = 约 400 行冗余）：
  json / random / re / time / urllib / uuid / requests / BeautifulSoup / logger
  / google.protobuf.json_format.MessageToDict / static.Response_pb2
  / builder.{header,params,proto} / utils.{fingerprint,dy_util}
以及同一个 1 行的 `protobuf_to_dict` 辅助函数（10 份逐字节相同）。

按用户要求「所有能共享使用的全部共享」，集中到本模块；各域文件改为
`from dy_apis._common import *`，需要更精确控制的再显式引入。

## 语义
`MessageToDict` 以 `_message_to_dict` 别名导出（原各文件即如此命名），
`protobuf_to_dict` 保持 `preserving_proto_field_name=True` 的原行为。
"""
from __future__ import annotations

import json
import random
import re
import time
import urllib
import uuid

import requests
from bs4 import BeautifulSoup
from loguru import logger
from google.protobuf.json_format import MessageToDict as _message_to_dict

import static.Response_pb2 as ResponseProto
from builder.header import HeaderBuilder, HeaderType
from builder.params import Params
from builder.proto import ProtoBuilder
from utils.fingerprint import get_profile
from utils.dy_util import (
    splice_url, generate_a_bogus, generate_msToken, trans_cookies,
    generate_a_bogus_pure,
)


# 显式导出（`import *` 默认跳过下划线名，这里把 _message_to_dict 一并暴露，
# 使各域模块无需再单独 import google.protobuf.json_format）。
__all__ = [
    "json", "random", "re", "time", "urllib", "uuid", "requests",
    "BeautifulSoup", "logger", "_message_to_dict", "ResponseProto",
    "HeaderBuilder", "HeaderType", "Params", "ProtoBuilder", "get_profile",
    "splice_url", "generate_a_bogus", "generate_msToken", "trans_cookies",
    "generate_a_bogus_pure", "protobuf_to_dict", "safe_json",
]


def protobuf_to_dict(message):
    return _message_to_dict(message, preserving_proto_field_name=True)

def safe_json(resp, default=None):
    """安全解析响应 JSON —— 平台限流/风控时返回**空响应体**（非 JSON），
    直接 `resp.json()` 会抛 JSONDecodeError 并被上层当作「接口不可用」（502）。

    2026-09-15 加：统一把空响应/坏 JSON 降级为 `default`（默认空 dict），
    与「平台侧限制应优雅降级、不假装成功也不报故障」的项目约定一致。

    用法：`return safe_json(resp)` 代替 `return resp.json()`。
    """
    try:
        return resp.json()
    except Exception:
        # 空响应（content-length: 0）或 HTML 错误页 —— 记录状态码便于排查
        try:
            logger.warning(f"[IM-001] " + f"[im] 响应非 JSON（HTTP {resp.status_code}, {len(resp.content)} 字节）"
                f"→ 降级为 {type(default).__name__ if default is not None else 'None'}")
        except Exception:
            pass
        if default is not None:
            return default
        return {}

