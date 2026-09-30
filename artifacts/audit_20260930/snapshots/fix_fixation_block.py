# -*- coding: utf-8 -*-
"""修复 ai_reply.py 中 _RE_LIVE_FIXATION_BASIS 块的换行转义污染。

背景：用 heredoc 传含 `\n` 的正则字面量时，转义层把正则里的换行类拆成了真换行，
导致 SyntaxError: unterminated string literal。本脚本用 chr(92)+"n" 构造，
不含任何反斜杠-n 字面量，从根上免疫转义层。
"""
import sys

P = 'backend/services/ai_reply.py'
b = open(P, 'rb').read()

start_tok = b'_RE_LIVE_FIXATION_BASIS = re.compile('
start = b.find(start_tok)
assert start != -1, 'start not found'
end_tok = b'\r\n)\r\n'
end = b.find(end_tok, start)
assert end != -1, 'end not found'
end += len(end_tok)

BS_N = chr(92) + 'n'          # 正则里的 \n（两个字符）
CLS = "[^。！？!?~'" + BS_N + ']'

lines = [
    '_RE_LIVE_FIXATION_BASIS = re.compile(',
    '    r"(?:没做|没有做|未做|不做|没有|无|未)' + CLS + '{0,6}内固定"',
    '    r"' + CLS + '{0,12}(?:等级|评级|评上|评残|级别)"',
    '    r"|(?:等级|评级|评上|评残|级别)' + CLS + '{0,12}"',
    '    r"(?:看|要|得看|取决于)' + CLS + '{0,6}(?:内固定|手术)"',
    ')',
]
block = ('\r\n'.join(lines) + '\r\n').encode('utf-8')

b = b[:start] + block + b[end:]
open(P, 'wb').write(b)

lines2 = b.split(b'\n')
bad = [i for i, l in enumerate(lines2, 1) if l and not l.endswith(b'\r')]
print('bad lines:', bad)
print('CRLF=', b.count(b'\r\n'), 'LF=', b.count(b'\n'))
