# -*- coding: utf-8 -*-
"""私信 IM 协议 2026 版守卫（v0.43.97 新增）。

为什么需要
----------
2026 抖音 PC IM 从「web_protect 旧信封 + body 顶层签名」切到
「bd-ticket-guard HTTP 头 + 空 token + 无 ts_sign/sdk_cert + identity_security_token」。
本项目曾长期停留在旧信封，导致 `create_conversation` 第一步就 INVALID_REQUEST，
**所有私信发送（手动 + AI 自动回复）全废**，且现象只在运行时暴露、静态看不出来。

本脚本把这些不变量编码成**可机械检查**的守卫，防后续编辑把协议改回旧形态。

用法：
    python scripts/verify_im_protocol_2026.py            # 全量校验，失败 exit 1
    python scripts/verify_im_protocol_2026.py --verbose

校验项（每条对应一处历史缺陷）：
  1. build_normal_request 不设顶层 token（旧版 `request.token = auth.ticket` 致 INVALID_REQUEST）
  2. build_normal_request 不设 ts_sign / sdk_cert（旧 web_protect 残留）
  3. sdk_version == "0.1.8"、build_number 含 "pc-im"、version_code == "360000"
  4. build_send_message_request 不生成顶层 reuqest_sign
  5. build_send_message_request 接受 identity_security_token/device_id 并写 header map
  6. client_im.send_msg 调用 headers.with_bd('/v1/message/send', ...)
  7. client_im 存在 get_identity_security_token，且 send_msg 调用了它
  8. utils.bd_ticket 存在 ticket_guard_version（with_bd 依赖）
  9. conv_identity.infer_my_uid_from_conv_ids 不再用「位置数 <= 1」的旧判据
 10. build_sidecar.py 声明了 qrcode hidden-import
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

FAILS: list[str] = []
PASSES: list[str] = []


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        PASSES.append(name)
    else:
        FAILS.append(f"{name}{(' — ' + detail) if detail else ''}")


def main() -> int:
    verbose = "--verbose" in sys.argv

    proto = _read("backend/builder/proto.py")
    # 1) 顶层 token 不设
    check("I1 normal_request 不设顶层 token",
          not re.search(r"request\.token\s*=", proto),
          "发现 request.token 赋值（2026 版必须留空）")
    # 2) 不设 ts_sign / sdk_cert
    check("I2 normal_request 不设 ts_sign/sdk_cert",
          not re.search(r"request\.ts_sign\s*=", proto) and not re.search(r"request\.sdk_cert\s*=", proto),
          "发现 request.ts_sign 或 request.sdk_cert 赋值")
    # 3) 2026 常量
    check("I3 sdk_version=0.1.8", 'SDK_VERSION = "0.1.8"' in proto, "sdk_version 不是 0.1.8")
    check("I3b build_number 含 pc-im", "pc-im" in proto, "build_number 未含 pc-im")
    check("I3c version_code=360000", 'VERSION_CODE = "360000"' in proto, "version_code 不是 360000")
    # 4) send 无顶层 reuqest_sign（只看代码行，忽略说明性注释；范围限定到 send 函数体）
    send_start = proto.find("def build_send_message_request")
    send_end = proto.find("\n    @staticmethod", send_start + 1)
    send_block = proto[send_start: send_end if send_end != -1 else len(proto)] \
        if send_start != -1 else ""
    code_lines = [ln for ln in send_block.splitlines()
                  if ln.strip() and not ln.strip().startswith("#")]
    send_code = "\n".join(code_lines)
    check("I4 send 不生成顶层 reuqest_sign",
          "reuqest_sign" not in send_code and "request_sign" not in send_code,
          "send 信封内仍生成顶层签名（2026 版应无）")
    # 5) identity token 参数 + header map
    check("I5 send 接受 identity_security_token",
          "identity_security_token" in send_block and "identity_security_device_id" in send_block,
          "send 未接受 identity_security 参数")
    check("I5b send 写入 identity header map",
          "identity_security_token" in send_block and "request.headers[" in send_block,
          "send 未把 identity token 写进 header map")

    cim = _read("backend/dy_apis/client_im.py")
    # 6) with_bd 调用
    check("I6 send_msg 调 with_bd('/v1/message/send')",
          "with_bd('/v1/message/send'" in cim or 'with_bd("/v1/message/send"' in cim,
          "send_msg 未调用 with_bd")
    # 7) get_identity_security_token 存在且被 send 调用
    check("I7 get_identity_security_token 已定义",
          "def get_identity_security_token" in cim, "未定义 get_identity_security_token")
    check("I7b send_msg 调用 get_identity_security_token",
          "get_identity_security_token(auth)" in cim, "send_msg 未调用 get_identity_security_token")

    bd = _read("backend/utils/bd_ticket.py")
    # 8) ticket_guard_version
    check("I8 bd_ticket.ticket_guard_version 存在",
          "def ticket_guard_version" in bd, "未定义 ticket_guard_version")

    ci = _read("backend/services/conv_identity.py")
    # 9) 旧判据已移除
    check("I9 conv_identity 不再用「位置数<=1」旧判据",
          "len(pos.get(best" not in ci,
          "仍存在 len(pos.get(best...)) <= 1 旧判据（会把本号 uid 误判为空）")

    bs = _read("scripts/build_sidecar.py")
    # 10) qrcode hidden-import
    check("I10 build_sidecar 声明 qrcode hidden-import",
          '"qrcode"' in bs, "未声明 qrcode hidden-import")

    if verbose:
        for p in PASSES:
            print("  [OK  ]", p)
    for f in FAILS:
        print("  [FAIL]", f)
    print(f"\n{'✓ 全部通过' if not FAILS else '✗ 存在失败'}：{len(PASSES)} 通过 / {len(FAILS)} 失败")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
