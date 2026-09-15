"""统一报错代码体系（errcode）—— 查询与格式化逻辑。

日志中每条 error/warning 都带 [域-序号] 代码前缀，如：
    14:32:01 | ERROR | [AUTH-050] 凭证失效

## 拆分说明（2026-09-15 大单文件打散）
原 `errcode.py` 为 673 行单文件，其中 570 行是**纯数据字典**
（DOMAIN_INFO / DOMAIN_DESIGN / ERRCODES / CODE_DESIGN / SPECIAL）。
按「数据与逻辑分离」抽出到 `errcode_data.py`，本文件只保留：
lookup / ec_err / ec_warn / ec_exc / all_codes / contract_gaps 等函数。
**纯搬移**——字典内容与函数逻辑逐字节不变。
"""
from __future__ import annotations

from loguru import logger

from errcode_data import (
    DOMAIN_INFO, DOMAIN_DESIGN, ERRCODES, CODE_DESIGN, SPECIAL,
)

def _fmt(code: str, message) -> str:
    return f"[{code}] {message}"

def ec_err(code: str, message, *args, **kwargs):
    """logger.error 的带码包装：ec_err("AUTH-003", f"[auth] 加载 {p} 失败: {e}")"""
    logger.error(_fmt(code, message))

def ec_warn(code: str, message, *args, **kwargs):
    logger.warning(_fmt(code, message))

def ec_exc(code: str, message, *args, **kwargs):
    """在 except 块内使用，保留完整 traceback"""
    logger.exception(_fmt(code, message))

def lookup(code: str):
    """查码：返回 设计契约 + 偏离 + 溯源链 + 实机判据（2026-09-13 升级）。

    使用顺序（对应调试六步闭环）：
      1. 读 design / contract —— 先回归设计理念，明确该模块本该做什么；
      2. 读 deviation —— 明确实际与预期的具体差异（不是"报错了"）；
      3. 沿 chain 从源头查，不要只盯终端现象；
      4. root 给出常见的断裂点（为什么在这一环断）；
      5. verify 是实机判据（禁止纯代码/静态分析定论）。
    域级 domain_design 提供该模块整体规划（intent/invariant/chain/verify）。
    """
    c = ERRCODES.get(code)
    if not c:
        return None
    dom = code.split("-")[0]
    d = DOMAIN_INFO.get(dom, ("", "", ""))
    sp = SPECIAL.get(code, ("", ""))
    dd = DOMAIN_DESIGN.get(dom, {})
    cd = CODE_DESIGN.get(code, {})
    return {
        "code": code, "domain": dom, "domain_name": d[0],
        "meaning": c["meaning"], "file": c["file"], "line": c["line"],
        "cause": sp[0] or d[1], "action": sp[1] or d[2],
        # ── Step1 设计契约（先回归设计理念，再看现状）──
        "design": cd.get("design") or dd.get("intent", ""),
        "contract": cd.get("contract") or dd.get("invariant", ""),
        # ── Step2/3/4 偏离 / 溯源链 / 断裂点 ──
        "deviation": cd.get("deviation", ""),
        "chain": cd.get("chain") or dd.get("chain", ""),
        "root": cd.get("root", ""),
        # ── Step5 实机判据 ──
        "verify": cd.get("verify") or dd.get("verify", ""),
        "domain_design": {
            "intent": dd.get("intent", ""),
            "invariant": dd.get("invariant", ""),
            "chain": dd.get("chain", ""),
            "verify": dd.get("verify", ""),
        } if dd else {},
        "has_contract": bool(cd.get("design") or dd.get("intent")),
    }

def all_codes() -> list:

    return [lookup(c) for c in sorted(ERRCODES)]


def contract_gaps() -> dict:
    """审计：哪些错误码缺设计契约（违反"新增码必须填 design"铁律）。

    用法：python -c "from errcode import contract_gaps as g; print(g()['missing'])"
    """
    missing = []
    for c in sorted(ERRCODES):
        dom = c.split("-")[0]
        cd = CODE_DESIGN.get(c, {})
        dd = DOMAIN_DESIGN.get(dom, {})
        if not (cd.get("design") or dd.get("intent")):
            missing.append(c)
    return {
        "total_codes": len(ERRCODES),
        "domain_ok": sorted(DOMAIN_DESIGN.keys()),
        "code_ok": sorted(CODE_DESIGN.keys()),
        "missing": missing,
    }
