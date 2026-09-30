# -*- coding: utf-8 -*-
"""直播「实时评论统计列表」表格重设计（2026-09-30）。

用户要求：
  1. 「发送内容不需要完整的全部展示，显示前10个字就行，不要破坏表格结构」
  2. 「发送私信的文本目前没有标识，根本不知道这个文本到底是词库，还是 AI
     生成，还是兜底文档」→ 来源标签（AI / 词库 / 原文）

设计约束（表格结构不动）：
  - 列数与列序**不变**（发送时间 / 发言人 / 评论内容 / 私信状态 / 私信文案 / 私信时间）；
  - 只改「私信文案」单元格的内容呈现：来源徽标 + 前 10 字 + 悬浮显示全文；
  - 表头加列宽（w-%）防抖动，不改 colSpan / 空态行。

为什么前端再截一次（后端已下发全文）：
  截图实测该列文案长到把行撑高、列被挤变形（用户原话「不要破坏表格结构」）。
  `truncate` 按**容器宽度**截，宽度随窗口变化 ⇒ 字数不固定；用户要的是**固定 10 字**。
  故用 JS 截断（与 CSS 截断并存：JS 定上限字数，CSS 兜住剩余溢出）。

行尾：本项目 core.autocrlf=true + 文件为 CRLF，故用 `\r\n` 写入，避免整文件行尾翻转。
"""
P = 'frontend/src/components/live/live-page.tsx'
b = open(P, 'rb').read()

# ── ① 来源标签的呈现映射（常量放在组件文件顶部，紧邻其它常量）─────────────
n1 = 'const sidOf = (c: RoomConfig): string => String(c.id || c.room_id || "");\r\n'.encode('utf-8')
assert b.count(n1) == 1, f'n1={b.count(n1)}'
r1 = (
    'const sidOf = (c: RoomConfig): string => String(c.id || c.room_id || "");\r\n'
    '\r\n'
    '/**\r\n'
    ' * 2026-09-30：私信文案**来源**的呈现契约。\r\n'
    ' *\r\n'
    ' * 用户实测反馈：「发送私信的文本目前没有标识，根本不知道这个文本到底是词库，\r\n'
    ' * 还是 AI 生成，还是兜底文档」。标签由后端 content_source 直接映射，\r\n'
    ' * **前端不做推断**（无来源值 ⇒ 不显示标签，绝不猜）。\r\n'
    ' */\r\n'
    'const SOURCE_META: Record<string, { label: string; color: string }> = {\r\n'
    '  AI: { label: "AI", color: "var(--color-info)" },\r\n'
    '  词库: { label: "词库", color: "var(--color-accent)" },\r\n'
    '  原文: { label: "原文", color: "var(--color-warning)" },\r\n'
    '};\r\n'
    '\r\n'
    '/** 发送内容**只展示前 10 字**（用户口径：「显示前 10 个字就行」）。 */\r\n'
    'const DM_PREVIEW_CHARS = 10;\r\n'
).encode('utf-8')
b = b.replace(n1, r1, 1)

# ── ② 表头加列宽（不改列数/列序，防长文案抖动）────────────────────────────
n2 = (
    '                      <Th>发送时间</Th>\r\n'
    '                      <Th>发言人</Th>\r\n'
    '                      <Th>评论内容</Th>\r\n'
    '                      <Th>私信状态</Th>\r\n'
    '                      <Th>私信文案</Th>\r\n'
    '                      <Th>私信时间</Th>\r\n'
).encode('utf-8')
assert b.count(n2) == 1, f'n2={b.count(n2)}'
r2 = (
    '                      <Th width={88}>发送时间</Th>\r\n'
    '                      <Th width={150}>发言人</Th>\r\n'
    '                      <Th width={240}>评论内容</Th>\r\n'
    '                      <Th width={96}>私信状态</Th>\r\n'
    '                      <Th width={210}>私信文案</Th>\r\n'
    '                      <Th width={88}>私信时间</Th>\r\n'
).encode('utf-8')
b = b.replace(n2, r2, 1)

# ── ③ 私信文案单元格：来源徽标 + 前 10 字 + title 全文 ─────────────────────
n3 = (
    '                      <Td\r\n'
    '                        title={\r\n'
    '                          r.dmStatus === "fail" && r.reason\r\n'
    '                            ? `${r.dmText}\\n失败原因: ${r.reason}`\r\n'
    '                            : r.dmText\r\n'
    '                        }\r\n'
    '                      >\r\n'
    '                        <span\r\n'
    '                          className={cn(\r\n'
    '                            "block truncate",\r\n'
    '                            r.dmText\r\n'
    '                              ? "text-[var(--color-text)]"\r\n'
    '                              : "text-[var(--color-text-muted)]"\r\n'
    '                          )}\r\n'
    '                        >\r\n'
    '                          {r.dmText || "未发送"}\r\n'
    '                        </span>\r\n'
    '                      </Td>\r\n'
).encode('utf-8')
assert b.count(n3) == 1, f'n3={b.count(n3)}'
r3 = (
    '                      {/* 2026-09-30（用户实测反馈）：\r\n'
    '                          ① 「发送内容不需要完整的全部展示，显示前 10 个字就行」→ 正文只渲\r\n'
    '                             前 10 字（JS 截断，字数固定；CSS `truncate` 兜住窄列溢出）；\r\n'
    '                          ② 「不知道这个文本到底是词库、AI 生成还是兜底文档」→ 前置来源\r\n'
    '                             徽标（来自后端 content_source，前端不推断）；\r\n'
    '                          ③ 「不要破坏表格结构」→ 列数/列序/行结构逐字不变，仅单元格内部\r\n'
    '                             布局调整；全文经 `title` 悬浮可见（信息不丢）。 */}\r\n'
    '                      <Td\r\n'
    '                        title={\r\n'
    '                          (() => {\r\n'
    '                            const _src = SOURCE_META[r.contentSource || ""];\r\n'
    '                            const _tag = _src ? `【${_src.label}】` : "";\r\n'
    '                            const _body = r.dmText || "未发送";\r\n'
    '                            const _head =\r\n'
    '                              r.dmStatus === "fail" && r.reason\r\n'
    '                                ? `${_body}\\n失败原因: ${r.reason}`\r\n'
    '                                : _body;\r\n'
    '                            return _tag ? _tag + _head : _head;\r\n'
    '                          })()\r\n'
    '                        }\r\n'
    '                      >\r\n'
    '                        <span className="flex min-w-0 items-center gap-1.5">\r\n'
    '                          {(() => {\r\n'
    '                            const _src = SOURCE_META[r.contentSource || ""];\r\n'
    '                            return _src ? (\r\n'
    '                              <span\r\n'
    '                                className="shrink-0 rounded-[4px] border px-1\r\n'
    '                                           font-mono text-[0.66rem]"\r\n'
    '                                style={{\r\n'
    '                                  color: _src.color,\r\n'
    '                                  borderColor: `color-mix(in srgb, ${_src.color} 40%, transparent)`,\r\n'
    '                                }}\r\n'
    '                                title={`文案来源：${_src.label}`}\r\n'
    '                              >\r\n'
    '                                {_src.label}\r\n'
    '                              </span>\r\n'
    '                            ) : null;\r\n'
    '                          })()}\r\n'
    '                          <span\r\n'
    '                            className={cn(\r\n'
    '                              "block min-w-0 flex-1 truncate",\r\n'
    '                              r.dmText\r\n'
    '                                ? "text-[var(--color-text)]"\r\n'
    '                                : "text-[var(--color-text-muted)]"\r\n'
    '                            )}\r\n'
    '                          >\r\n'
    '                            {r.dmText\r\n'
    '                              ? r.dmText.slice(0, DM_PREVIEW_CHARS)\r\n'
    '                              : "未发送"}\r\n'
    '                          </span>\r\n'
    '                        </span>\r\n'
    '                      </Td>\r\n'
).encode('utf-8')
b = b.replace(n3, r3, 1)

open(P, 'wb').write(b)
lines = b.split(b'\n')
bad = [i for i, l in enumerate(lines, 1) if l and not l.endswith(b'\r')]
print('bad-EOL lines:', bad)
print('CRLF=', b.count(b'\r\n'), 'LF=', b.count(b'\n'))
