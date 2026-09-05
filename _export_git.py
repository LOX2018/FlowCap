import subprocess, os, datetime

GIT = r"C:/Users/LOX/Desktop/DYchajian"
OUT = r"C:/Users/LOX/Desktop/DYchajian/_git_history_export.md"
env = dict(os.environ)
env["LC_ALL"] = "C.UTF-8"
env["LANG"] = "C.UTF-8"

def run(args):
    p = subprocess.run(["git", "-C", GIT] + args, capture_output=True, env=env)
    return p.stdout.decode("utf-8", "replace")

lines = []
lines.append("# DYchajian 项目 Git 提交历史")
lines.append("")
lines.append("> 由 Hermes 于 %s 自动导出" % datetime.date.today().isoformat())
lines.append("> 仓库根: C:/Users/LOX/Desktop/DYchajian (含 DYAutoDM_v2 子项目)")
lines.append("")
lines.append("## 提交记录 (oneline, 最近 25 条)")
lines.append("")
lines.append(run(["log", "--oneline", "-25"]).strip())
lines.append("")
lines.append("## 完整提交详情 (最近 8 条)")
lines.append("")
lines.append(run(["log", "-8", "--format=### %h — %s%n%n%b%n"]).strip())
lines.append("")
lines.append("## 当前工作区状态 (未提交改动, 注意: 这些不在 git 历史中)")
lines.append("")
lines.append(run(["status", "--short"]).strip())
lines.append("")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print("WROTE", OUT)
print("BYTES", os.path.getsize(OUT))
