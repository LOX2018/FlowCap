"""验证打包产物是否含本次改动（Rust 权限 + 前端 openExternal）。

2026-09-01：连续多轮出现「改了代码但界面没变」，故每次打包后
都要验证产物，而不是想当然地认为构建成功 = 改动生效。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # DYAutoDM_v2/ (was hardcoded abs path)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_paths import exe_path  # noqa: E402

exe_p = exe_path("release")
exe = exe_p.read_bytes()
print(f"exe: {len(exe):,} 字节\n")

print("=" * 60)
print("Rust 侧：capabilities 权限")
print("=" * 60)
# 注：Tauri 2 把 capabilities 编译成 ACL 结构，权限标识**不是明文**，
# 所以 exe 里 grep 不到 "shell:allow-open" 是正常现象，不能据此判断缺失。
# 改为验证：(a) capabilities 源文件有该权限 (b) schema 已注册。
import json as _json

cap_p = ROOT / "src-tauri/capabilities/default.json"
cap = _json.loads(cap_p.read_text(encoding="utf-8"))
perms = cap.get("permissions", [])
print(f"  源文件: {cap_p.name}")
for p in ["shell:allow-open", "shell:allow-execute", "shell:allow-kill"]:
    mark = "OK" if p in perms else "-- 缺失!"
    print(f"    {p:22s}: {mark}")

sch = ROOT / "src-tauri/gen/schemas/capabilities.json"
registered = b"shell:allow-open" in sch.read_bytes() if sch.exists() else False
print(f"  schema 已注册 allow-open: {'OK' if registered else '-- 未注册'}")

exe_ok = len(exe) > 3_000_000
print(f"  exe 大小 {len(exe):,} 字节: {'OK' if exe_ok else '-- 异常偏小!'}")

print()
print("=" * 60)
print("前端侧：openExternal 等是否打包进去")
print("=" * 60)
assets = ROOT / "frontend/dist/assets"
fs = sorted(assets.glob("index-*.js"))
print(f"  dist JS 文件数: {len(fs)}")
blob = b"".join(f.read_bytes() for f in fs)
for s in ["openExternal", "去抖音看原图", "可滚轮缩放", "0.33.1", "appver"]:
    n = blob.count(s.encode("utf-8"))
    print(f"  {s:14s}: {n}  {'OK' if n else '-- 缺失!'}")

print()
print("=" * 60)
print("检查是否残留 target=\"_blank\"（WebView 里无效）")
print("=" * 60)
# 注：源码注释里也会出现 target="_blank" 字样（说明为什么不能用它），
# 打包时会被保留，属于**误报**。真正的代码残留是 JSX 属性形式：
#   target:"_blank"   （编译后 JSX 属性）
# 而注释里的原文是 target=\"_blank\"
code_hits = blob.count(b'target:"_blank"')
comment_hits = blob.count(b'target=\\"_blank\\"') + blob.count(b"target=\"_blank\"")
print(f"  JSX 属性残留（真问题）: {code_hits}  {'-- 需修!' if code_hits else 'OK'}")
print(f"  注释文字（误报）    : {comment_hits}")

print()
ok = (
    "shell:allow-open" in perms
    and registered
    and exe_ok
    and blob.count(b"openExternal") > 0
    and code_hits == 0
)
print(">>> 构建验证:", "通过" if ok else "未通过，需排查")
