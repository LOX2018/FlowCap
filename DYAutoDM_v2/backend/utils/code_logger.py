# -*- coding: utf-8 -*-
"""错误码日志补丁：修复 loguru 吞掉「码后描述」的问题。

背景（2026-09-13 用户反馈「运行日志只有报错代码，没有描述说明」）：
项目里有 345 处按「统一报错代码体系」写成：

    logger.warning(f"[BCC-006] " + f"[bcc] context/page 失活，重启: {e}")

但 loguru 会把**第一个位置参数当作格式模板**（等价于 str.format 的模板），
第二个参数只作为 `{}` 占位符的填充值。由于 "BCC-006" 里没有 `{}`，
描述文案被**静默丢弃**，运行日志里只剩一行 `BCC-006` —— 完全失去排查价值
（用户和我此前都被迫只能看到代码，看不到什么异常）。

修法（零侵入、一处生效）：包装 logger 的各级方法，检测
「第一个参数是纯错误码（如 BCC-006）且后面还有描述」时，
自动合并成 `CODE | 描述` 再走原方法。**不修改 345 处调用点**，
`{}` 占位符的正常用法不受影响，多次调用不会重复包装。

用法：在 logger.add(...) 之前调用 install_code_logger_patch()（幂等）。
"""
from __future__ import annotations

import re

# 统一报错代码：2-5 个大写字母 + '-' + 3 位数字（见 errcode.py / 知识库 14 号）
_CODE_RE = re.compile(r"^[A-Z]{2,5}-\d{3}$")

_LEVELS = ("debug", "info", "success", "warning", "error", "critical", "exception")


def install_code_logger_patch() -> bool:
    """给 loguru logger 装上「错误码 + 描述」兼容层。幂等，可重复调用。"""
    try:
        from loguru import logger
    except Exception:
        return False

    installed = False
    for lvl in _LEVELS:
        orig = getattr(logger, lvl, None)
        if orig is None or getattr(orig, "_code_patched", False):
            continue

        def _make(orig_):
            def wrapper(message, *args, **kwargs):
                # 仅处理「纯错误码 + 后续描述」这一种形态，其余原样透传
                try:
                    if isinstance(message, str) and _CODE_RE.match(message.strip()):
                        rest = list(args)          # tuple 不可变，必须转 list
                        desc = ""
                        if rest and isinstance(rest[0], str) and rest[0].strip():
                            desc = rest.pop(0)
                        extra = ""
                        if rest:
                            extra = " " + " ".join(str(a) for a in rest)
                        msg = message.strip()
                        if desc:
                            msg += " | " + desc
                        msg += extra
                        return orig_(msg, **kwargs)
                except Exception:
                    pass                            # 补丁绝不影响主日志链路
                return orig_(message, *args, **kwargs)

            wrapper._code_patched = True
            return wrapper

        try:
            setattr(logger, lvl, _make(orig))
            installed = True
        except Exception:
            continue
    return installed
