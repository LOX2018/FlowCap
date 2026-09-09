# -*- coding: utf-8 -*-
"""IM 网关：入站消息授权 / 权限组（v0.38.5）。

用户需求（2026-09-09）：「当前的模式很不友好，全让用户手动填写字段有门槛，
应该有一个默认网关来处理，刚建立时接收所有来源的消息，但拦截返回，
在设置中提示收到 XXX 的信息让用户甄别是否授权和授权范围
（全授权、仅查看、仅可操作某某单一功能等）这涉及权限组」。

范式对标（用户提供的教程）：OpenClaw devices pairing ——
陌生设备发消息 → pending 配对列表 → 管理员 approve → 按 allowlist 放行。

设计：
  - **网关模式** gateway_mode：
      pairing（默认）= 接收所有来源消息，但未授权者一律拦截；
        首条消息生成「待授权」条目并自动回复引导语；
        设置页审批（批准到某权限组 / 拉黑）。
      open = 不拦截（兼容旧行为 / 测试）。
  - **权限组**（固定三级 + 细粒度 allow）：
      admin     = 全授权（含在本机 UI 之外操作其他 sender 的授权？——否，
                  审批仍只在本机 UI 做，用户拍板「需要在本机 UI 点设为管理员」）
      operator  = 可执行任务操作：start/stop/create_task/recapture
      viewer    = 仅查看：query_status / help
      blocked   = 拉黑：不回复、不进待审列表（记录最近拦截时间）
    另支持 per-sender `allow_intents` 精确白名单（如只允许 query_status）。
  - **admin 初始授予**：只能在本机 UI 点「设为管理员」（用户拍板），不自动信任。
  - 持久化：notify_config.json 新增 `gateway` 节
    {mode, grants: {sender_key: {role, allow_intents, note, approved_at}}, pending: [...]}。
    sender_key = f"{channel_id}:{sender_id}"（渠道内唯一）。

QQ 官方 bot 的被动回复约束（botpy 实测）：发消息必须带 5 分钟内的 msg_id；
网关把拦截提示语作为**被动回复**发出（on_c2c/on_group 处理器里直接回）。
iLink 的拦截提示用 context_token 回推（收到消息即有 token）。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from loguru import logger

# 权限组 → 允许的意图。None = 不允许任何业务意图。
ROLE_INTENTS: dict[str, Optional[set[str]]] = {
    "admin": None,  # None = 不限制（全部允许）
    "operator": {"start_task", "stop_task", "create_task", "recapture",
                 "query_status", "help"},
    "viewer": {"query_status", "help"},
    "pending": set(),
    "blocked": set(),
}

VALID_ROLES = ("admin", "operator", "viewer", "blocked")


def sender_key(channel_id: str, sender_id: str) -> str:
    """渠道内唯一的发送者键。"""
    return f"{channel_id}:{sender_id}"


class Gateway:
    """入站消息网关。全部方法同步、纯内存 + json 落盘，零外部依赖。"""

    def __init__(self) -> None:
        # 由 api/notify.py 注入 get_config/save_config 的引用，避免反向依赖
        self._get_cfg = None
        self._save_cfg = None

    def bind_store(self, get_config, save_config) -> None:
        self._get_cfg = get_config
        self._save_cfg = save_config

    def ensure_bound(self) -> bool:
        """模块级自举绑定（v0.38.5）：避免「忘了调 init_notifier 就全空」。

        gateway 与 api/notify.py 双向依赖（gateway 不 import api，api import
        gateway），故此处**延迟** import—— 首个消费方触发时完成绑定。
        """
        if self._get_cfg is not None:
            return True
        try:
            from api.notify import load_config, save_config_file

            self.bind_store(load_config, save_config_file)
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---------------- 配置存取 ----------------

    def _gw(self) -> dict[str, Any]:
        if self._get_cfg is None:
            self.ensure_bound()
        if self._get_cfg is None:
            return {"mode": "pairing", "grants": {}, "pending": []}
        cfg = self._get_cfg() or {}
        gw = cfg.get("gateway") or {}
        gw.setdefault("mode", "pairing")
        gw.setdefault("grants", {})
        gw.setdefault("pending", [])
        return gw

    def _save_gw(self, gw: dict[str, Any]) -> None:
        if self._save_cfg is None:
            self.ensure_bound()
        if self._save_cfg is None:
            return
        cfg = self._get_cfg() or {}
        cfg["gateway"] = gw
        self._save_cfg(cfg)

    # ---------------- 审批入口（设置页 / API） ----------------

    def overview(self) -> dict[str, Any]:
        """设置页一次拉全：模式 + 已授权 + 待审。"""
        gw = self._gw()
        return {
            "mode": gw.get("mode", "pairing"),
            "grants": gw.get("grants", {}),
            "pending": gw.get("pending", []),
        }

    def set_mode(self, mode: str) -> dict[str, Any]:
        if mode not in ("pairing", "open"):
            return {"ok": False, "error": f"未知模式: {mode}"}
        gw = self._gw()
        gw["mode"] = mode
        self._save_gw(gw)
        logger.info(f"[gateway] 模式切换为 {mode}")
        return {"ok": True, "mode": mode}

    def approve(self, key: str, role: str, note: str = "") -> dict[str, Any]:
        """批准待审条目到某权限组；也可对已授权者改组。"""
        if role not in VALID_ROLES:
            return {"ok": False, "error": f"未知权限组: {role}"}
        gw = self._gw()
        entry = None
        rest: list[dict] = []
        for p in gw.get("pending", []):
            if p.get("key") == key:
                entry = p
            else:
                rest.append(p)
        gw["pending"] = rest
        grants = gw.setdefault("grants", {})
        if entry is None:
            # 允许直接对已知 sender 授权（如手动输入）
            channel_id, _, sender_id = key.partition(":")
            entry = {"key": key, "channel_id": channel_id,
                     "sender_id": sender_id, "first_text": ""}
        grants[key] = {
            "role": role,
            "allow_intents": [],
            "note": note or entry.get("first_text", "")[:50],
            "approved_at": int(time.time()),
            "channel_id": entry.get("channel_id", ""),
            # v0.38.5：kind（weixin_oc/qqofficial）—— 同 kind 不同实例 id
            # 视为同一来源（用户重复配置同一 bot 时的去重依据）
            "kind": entry.get("kind") or str(entry.get("channel_id", "")).split("_", 1)[0],
            "sender_id": entry.get("sender_id", ""),
        }
        self._save_gw(gw)
        logger.info(f"[gateway] 授权 {key} -> {role}")
        return {"ok": True, "grants": grants, "pending": gw["pending"]}

    def revoke(self, key: str) -> dict[str, Any]:
        gw = self._gw()
        gw.get("grants", {}).pop(key, None)
        gw["pending"] = [p for p in gw.get("pending", [])
                         if p.get("key") != key]
        self._save_gw(gw)
        return {"ok": True, "grants": gw.get("grants", {}),
                "pending": gw["pending"]}

    def set_allow_intents(self, key: str, intents: list[str]) -> dict[str, Any]:
        """细粒度意图白名单（在权限组之上进一步收窄；空 = 按权限组默认）。"""
        gw = self._gw()
        g = gw.get("grants", {}).get(key)
        if not g:
            return {"ok": False, "error": "sender 未授权"}
        g["allow_intents"] = [str(i) for i in intents]
        self._save_gw(gw)
        return {"ok": True, "grant": g}

    # ---------------- 网关核心：入站判定 ----------------

    def check(self, channel_id: str, sender_id: str, text: str,
              channel_kind: str = "") -> dict[str, Any]:
        """入站消息过网关。返回 decision:

        {action: allow|reject|pending, role, reason, reply_hint}
        - allow   = 已授权（或 open 模式），调用方继续解析执行
        - pending = 新来源，已记入待审列表；调用方回引导语
        - reject  = 拉黑 / 权限组无任何可用意图
        """
        gw = self._gw()
        mode = gw.get("mode", "pairing")
        key = sender_key(channel_id, sender_id)

        if mode == "open":
            return {"action": "allow", "role": "open", "reason": "open 模式"}

        grants = gw.get("grants", {})
        g = grants.get(key)
        if g is None:
            # 同 kind + sender 的其他实例 key 也算已授权（防重复配置导致重复待审）
            ck = channel_kind or channel_id.split("_", 1)[0]
            for k2, g2 in grants.items():
                if (str(g2.get("kind") or g2.get("channel_id", "")) == ck
                        and str(g2.get("sender_id")) == str(sender_id)):
                    g = g2
                    break
        if g:
            role = str(g.get("role", "viewer"))
            if role == "blocked":
                return {"action": "reject", "role": role,
                        "reason": "已拉黑"}
            return {"action": "allow", "role": role,
                    "allow_intents": g.get("allow_intents") or [],
                    "reason": "已授权"}

        # 未授权：进待审（去重，同一 sender 只留一条，更新最近消息与时间）
        # v0.38.5 补丁：同一 sender 经由**同 kind 的不同渠道实例**（用户重复配置
        # 同一 QQ bot 会产生多个实例 id）到达时，视为同一来源 —— 待审与授权都按
        # 「kind + sender_id」归并，避免同一人出现两条不同抬头的待审。
        pending = gw.setdefault("pending", [])
        now = int(time.time())
        channel_kind = channel_kind or channel_id.split("_", 1)[0]
        for p in pending:
            same = (
                p.get("key") == key
                or (str(p.get("kind") or p.get("channel_id", "")) == channel_kind
                    and str(p.get("sender_id")) == str(sender_id))
            )
            if same:
                if p.get("key") != key:
                    # 归并到首条：key 统一为已有条目（授权后对所有实例生效）
                    p.setdefault("alias_keys", []).append(key)
                p["last_text"] = (text or "")[:80]
                p["last_at"] = now
                p["msg_count"] = int(p.get("msg_count", 0)) + 1
                self._save_gw(gw)
                return {"action": "pending", "role": "pending",
                        "reason": "待授权（已存在待审条目）"}
        pending.append({
            "key": key,
            "channel_id": channel_id,
            "kind": channel_kind or channel_id.split("_", 1)[0],
            "sender_id": sender_id,
            "first_text": (text or "")[:80],
            "last_text": (text or "")[:80],
            "first_at": now,
            "last_at": now,
            "msg_count": 1,
        })
        # 待审列表上限保护
        if len(pending) > 100:
            gw["pending"] = sorted(pending, key=lambda p: -p["last_at"])[:100]
        self._save_gw(gw)
        logger.info(f"[gateway] 新来源待授权: {key} 首条: {(text or '')[:40]}")
        return {"action": "pending", "role": "pending",
                "reason": "新来源，已加入待审"}

    def check_intent(self, key: str, intent: str) -> dict[str, Any]:
        """已授权 sender 的意图级校验（权限组 + 细粒度白名单）。"""
        gw = self._gw()
        g = gw.get("grants", {}).get(key)
        if not g:
            return {"ok": False, "reason": "未授权"}
        role = str(g.get("role", "viewer"))
        if role == "blocked":
            return {"ok": False, "reason": "已拉黑"}
        allowed = ROLE_INTENTS.get(role)
        if allowed is None:  # admin
            fine = g.get("allow_intents") or []
            if fine and intent not in fine:
                return {"ok": False, "reason": f"白名单未包含 {intent}"}
            return {"ok": True, "role": role}
        if intent not in allowed:
            return {"ok": False,
                    "reason": f"权限组 {role} 不允许 {intent}"}
        fine = g.get("allow_intents") or []
        if fine and intent not in fine:
            return {"ok": False, "reason": f"白名单未包含 {intent}"}
        return {"ok": True, "role": role}


# 全局单例（api/notify.py bind 后使用）
gateway = Gateway()
