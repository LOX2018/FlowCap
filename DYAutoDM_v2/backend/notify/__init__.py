"""IM 通知与指令模块（从 AstrBot 提取组件思路，独立轻量实现）。

channels.py  : 五渠道出站推送客户端（iLink/企微/钉钉/飞书/QQ）
               —— 协议实测取自 AstrBotDevs/AstrBot，只取出站层，不搬事件框架
cmd_parser.py: LLM 自然语言指令解析 + 规则兜底
notifier.py  : 事件中枢（节流去重 / 级别路由 / 后台派发）
"""

from .notifier import Notifier, notifier

__all__ = ["Notifier", "notifier"]
