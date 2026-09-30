# -*- coding: utf-8 -*-
"""门禁 G1/G2 抓出的两处真实缺陷修复（2026-09-30）。

缺陷 ①（护栏漏判）：「内固定⇒等级」判据用「等级词 + 内固定」的**紧邻窗口**，
  实测故障原文里两者相隔 15+ 字（「能不能评级，得看诊断报告上的描述和有没有做
  内固定」）⇒ 漏判。改为按**必要条件的语序**判：标记词(得看/要看/取决/才能…) 之后
  跟 内固定/手术，或 否定词(没有/没做/无/没…) 之后跟 内固定 再出现评级词。
  窗口放到 16 字，且不跨句（字符类已排除句末标点与换行）。
缺陷 ②（硬编码兜底）：`fallback_reply` 的**末位默认话术**（无 fallback_pool 时）
  仍是「您好，我是…大概几级。」—— 缺留资钩子，且「大概几级」暗示等级承诺。
  与 _DEFAULT_CONFIG.fallback_pool 首条同源问题，一并按三段契约改。

⚠️ 本脚本不出现反斜杠转义字面量（CR/LF 用 chr()，正则里的 \\n 用 chr(92)+'n'）。
"""
CR = chr(13)
LF = chr(10)
CRLF = CR + LF
BS_N = chr(92) + 'n'
CLS = "[^。！？!?~'" + BS_N + ']'        # 不含句末标点/换行的字符类

# ── ① 重写 _RE_LIVE_FIXATION_BASIS 为两条（必要条件语序 / 否定语序）────────
P = 'backend/services/ai_reply.py'
b = open(P, 'rb').read()

start = b.find(b'_RE_LIVE_FIXATION_BASIS = re.compile(')
assert start != -1
end = b.find(CRLF.encode() + b')' + CRLF.encode(), start)
assert end != -1
end += len(CRLF.encode() + b')' + CRLF.encode())

MARKER = "(?:得看|要看|取决于|取决|需要看|必须看|才能|才算|才能有)"
NEG = "(?:没有|没做|未做|不做|无|没|未)"
GRADE = "(?:等级|评级|评上|评残|级别)"

lines = [
    '# ① 必要条件语序：「得看 X」里 X = 内固定/手术（X 本身错 ⇒ 即便语气是「得看」也拦）',
    '_RE_LIVE_FIX_NEED = re.compile(',
    '    r"' + MARKER + CLS + '{0,16}(?:内固定|手术)"',
    ')',
    '# ② 否定语序：「没有/没做…内固定」⇒ 跟不上「没等级/评不上」的结论',
    '_RE_LIVE_FIX_NEG = re.compile(',
    '    r"' + NEG + CLS + '{0,6}内固定' + CLS + '{0,16}' + GRADE + '|"',
    '    r"' + GRADE + CLS + '{0,16}' + NEG + CLS + '{0,6}内固定"',
    ')',
]
block = (CRLF.join(lines) + CRLF).encode('utf-8')
b = b[:start] + block + b[end:]
open(P, 'wb').write(b)
lines2 = b.split(LF.encode())
print('EOL bad:', [i for i, l in enumerate(lines2, 1) if l and not l.endswith(CR.encode())])

# ── ② 把新两条接进 _live_guard_violation ────────────────────────────────
old = ("                       (_RE_LIVE_FIXATION_BASIS, \"「内固定⇒等级」错误基准\")):"
       + CRLF).encode('utf-8')
assert b.count(old) == 1, b.count(old)
new = ("                       (_RE_LIVE_FIX_NEED, \"「得看内固定/手术」⇒等级 错误基准\")," + CRLF +
       "                       (_RE_LIVE_FIX_NEG, \"「无内固定⇒无等级」错误基准\")):" + CRLF).encode('utf-8')
b = b.replace(old, new, 1)

# ── ③ 修正 fallback_reply 的末位默认话术（三段契约）────────────────────
old2 = ('    return random.choice(pool) if pool else (' + CRLF +
        '        "您好，我是唐律工伤团队的理赔顾问。请补充受伤部位、诊断结论、"' + CRLF +
        '        "是否有劳动合同和社保，我帮您判断能否认定工伤、大概几级。")' + CRLF).encode('utf-8')
assert b.count(old2) == 1, b.count(old2)
new2 = ('    # 2026-09-30：末位默认话术按直播首触三段契约改 —— 补留资钩子，' + CRLF +
        '    # 去掉「大概几级」（首触无下等级的余地，属错误基准）。' + CRLF +
        '    return random.choice(pool) if pool else (' + CRLF +
        '        "我是唐律工伤团队的理赔顾问。请补充受伤部位、诊断结论、"' + CRLF +
        '        "是否有劳动合同和社保，我帮您判断能否认定工伤；算好清单留个手机号发您。")' + CRLF).encode('utf-8')
b = b.replace(old2, new2, 1)

open(P, 'wb').write(b)
lines3 = b.split(LF.encode())
print('EOL bad:', [i for i, l in enumerate(lines3, 1) if l and not l.endswith(CR.encode())])
print('CRLF=', b.count(CRLF.encode()), 'LF=', b.count(LF.encode()))
