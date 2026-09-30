# -*- coding: utf-8 -*-
"""修复 live-shared.tsx 中被转义层拆断的模板字符串（`\\n` 变成真换行）。

heredoc 传含 `\n` 的代码时转义层不可靠；改用本脚本（用 chr(92)+'n' 构造，
不含任何反斜杠-n 字面量）。顺带做直播页 / 查阅模式的表格渲染改造。
"""
BS_N = chr(92) + 'n'

# ── ① 先修 live-shared.tsx 的断裂行 ─────────────────────────────────────
P = 'frontend/src/components/live/live-shared.tsx'
b = open(P, 'rb').read()
broken = ('      ? `${body}`\r\n'
          '失败原因: ${r.reason}`\r\n').encode('utf-8')
fixed = ('      ? `${body}' + BS_N + '失败原因: ${r.reason}`\r\n').encode('utf-8')
if b.count(broken) == 1:
    b = b.replace(broken, fixed, 1)
    print('[live-shared] 断裂模板字符串已修复')
else:
    print('[live-shared] 未命中断裂行（count=%d）' % b.count(broken))
open(P, 'wb').write(b)
lines = b.split(b'\n')
print('[live-shared] bad-EOL:', [i for i, l in enumerate(lines, 1) if l and not l.endswith(b'\r')])

# ── ② 直播页：改为调用 live-shared 的单一真源 ────────────────────────────
P2 = 'frontend/src/components/live/live-page.tsx'
b2 = open(P2, 'rb').read()

n1 = ('/**\r\n'
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
      '\r\n').encode('utf-8')
assert b2.count(n1) == 1, 'local-const block not found: %d' % b2.count(n1)
b2 = b2.replace(n1, '', 1)

# import 单一真源
n2 = ('  LiveStream, TaskListResponse, RealAcct, FeedItem, Row, acctValid, toDmStatus, fmtTime, recordsToRows, Th, Td, displayStatus, isIssue,\r\n'
      '} from "./live-shared";\r\n').encode('utf-8')
assert b2.count(n2) == 1, n2
r2 = ('  LiveStream, TaskListResponse, RealAcct, FeedItem, Row, acctValid, toDmStatus, fmtTime, recordsToRows, Th, Td, displayStatus, isIssue,\r\n'
      '  SOURCE_META, DM_PREVIEW_CHARS, sourceMetaOf, dmTitle,\r\n'
      '} from "./live-shared";\r\n').encode('utf-8')
b2 = b2.replace(n2, r2, 1)

# title 与徽标改用单一真源
n3 = ('                        title={\r\n'
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
      '                        }\r\n').encode('utf-8')
assert b2.count(n3) == 1, 'title block: %d' % b2.count(n3)
r3 = ('                        title={dmTitle(r)}\r\n').encode('utf-8')
b2 = b2.replace(n3, r3, 1)

n4 = ('                            const _src = SOURCE_META[r.contentSource || ""];\r\n'
      '                            return _src ? (\r\n').encode('utf-8')
assert b2.count(n4) == 1, 'badge block: %d' % b2.count(n4)
r4 = ('                            const _src = sourceMetaOf(r.contentSource);\r\n'
      '                            return _src ? (\r\n').encode('utf-8')
b2 = b2.replace(n4, r4, 1)

open(P2, 'wb').write(b2)
lines2 = b2.split(b'\n')
print('[live-page] bad-EOL:', [i for i, l in enumerate(lines2, 1) if l and not l.endswith(b'\r')])
print('[live-page] CRLF=', b2.count(b'\r\n'), 'LF=', b2.count(b'\n'))
