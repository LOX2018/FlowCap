# -*- coding: utf-8 -*-
"""修复 live-shared.tsx 断裂的模板字符串，并把来源标签/10字预览收敛到单一真源。

⚠️ 教训（本任务已踩两次）：heredoc 与 write_file 的传输路径都会吃掉一层反斜杠转义
（`\\r\\n` 落到磁盘变成「反斜杠 + r + n」）。故本脚本**不出现任何反斜杠转义字面量**：
CR/LF 用 chr(13)/chr(10) 构造；源码里需要出现的 `\n`（两字符）用 chr(92)+'n' 构造。
"""
CR = chr(13)
LF = chr(10)
CRLF = CR + LF
BS_N = chr(92) + 'n'          # 源码中模板/正则里的反斜杠-n

# ── ① 修复 live-shared.tsx 断裂行 ───────────────────────────────────────
P = 'frontend/src/components/live/live-shared.tsx'
b = open(P, 'rb').read()
broken = ('      ? `${body}' + LF + '失败原因: ${r.reason}`' + CRLF).encode('utf-8')
fixed = ('      ? `${body}' + BS_N + '失败原因: ${r.reason}`' + CRLF).encode('utf-8')
print('broken count =', b.count(broken))
if b.count(broken) == 1:
    b = b.replace(broken, fixed, 1)
    open(P, 'wb').write(b)
    print('[live-shared] 断裂模板字符串已修复')
lines = b.split(LF.encode())
print('[live-shared] bad-EOL:', [i for i, l in enumerate(lines, 1) if l and not l.endswith(CR.encode())])

# ── ② 直播页改为调用 live-shared 单一真源 ──────────────────────────────
P2 = 'frontend/src/components/live/live-page.tsx'
b2 = open(P2, 'rb').read()

n1 = ('/**' + CRLF +
      ' * 2026-09-30：私信文案**来源**的呈现契约。' + CRLF +
      ' *' + CRLF +
      ' * 用户实测反馈：「发送私信的文本目前没有标识，根本不知道这个文本到底是词库，' + CRLF +
      ' * 还是 AI 生成，还是兜底文档」。标签由后端 content_source 直接映射，' + CRLF +
      ' * **前端不做推断**（无来源值 ⇒ 不显示标签，绝不猜）。' + CRLF +
      ' */' + CRLF +
      'const SOURCE_META: Record<string, { label: string; color: string }> = {' + CRLF +
      '  AI: { label: "AI", color: "var(--color-info)" },' + CRLF +
      '  词库: { label: "词库", color: "var(--color-accent)" },' + CRLF +
      '  原文: { label: "原文", color: "var(--color-warning)" },' + CRLF +
      '};' + CRLF +
      CRLF +
      '/** 发送内容**只展示前 10 字**（用户口径：「显示前 10 个字就行」）。 */' + CRLF +
      'const DM_PREVIEW_CHARS = 10;' + CRLF +
      CRLF).encode('utf-8')
print('local const block count =', b2.count(n1))
assert b2.count(n1) == 1
b2 = b2.replace(n1, b'', 1)

n2 = ('  LiveStream, TaskListResponse, RealAcct, FeedItem, Row, acctValid, toDmStatus, fmtTime, recordsToRows, Th, Td, displayStatus, isIssue,' + CRLF +
      '} from "./live-shared";' + CRLF).encode('utf-8')
assert b2.count(n2) == 1, 'import anchor'
r2 = ('  LiveStream, TaskListResponse, RealAcct, FeedItem, Row, acctValid, toDmStatus, fmtTime, recordsToRows, Th, Td, displayStatus, isIssue,' + CRLF +
      '  SOURCE_META, DM_PREVIEW_CHARS, sourceMetaOf, dmTitle,' + CRLF +
      '} from "./live-shared";' + CRLF).encode('utf-8')
b2 = b2.replace(n2, r2, 1)

n3 = ('                        title={' + CRLF +
      '                          (() => {' + CRLF +
      '                            const _src = SOURCE_META[r.contentSource || ""];' + CRLF +
      '                            const _tag = _src ? `【${_src.label}】` : "";' + CRLF +
      '                            const _body = r.dmText || "未发送";' + CRLF +
      '                            const _head =' + CRLF +
      '                              r.dmStatus === "fail" && r.reason' + CRLF +
      '                                ? `${_body}' + BS_N + '失败原因: ${r.reason}`' + CRLF +
      '                                : _body;' + CRLF +
      '                            return _tag ? _tag + _head : _head;' + CRLF +
      '                          })()' + CRLF +
      '                        }' + CRLF).encode('utf-8')
print('title block count =', b2.count(n3))
assert b2.count(n3) == 1
b2 = b2.replace(n3, ('                        title={dmTitle(r)}' + CRLF).encode('utf-8'), 1)

n4 = ('                            const _src = SOURCE_META[r.contentSource || ""];' + CRLF +
      '                            return _src ? (' + CRLF).encode('utf-8')
print('badge block count =', b2.count(n4))
assert b2.count(n4) == 1
r4 = ('                            const _src = sourceMetaOf(r.contentSource);' + CRLF +
      '                            return _src ? (' + CRLF).encode('utf-8')
b2 = b2.replace(n4, r4, 1)

open(P2, 'wb').write(b2)
lines2 = b2.split(LF.encode())
print('[live-page] bad-EOL:', [i for i, l in enumerate(lines2, 1) if l and not l.endswith(CR.encode())])
print('[live-page] CRLF=', b2.count(CRLF.encode()), 'LF=', b2.count(LF.encode()))
