"""构建前清理 frontend/dist（Windows 安全版）。

为什么需要这个脚本：
  vite.config.ts 的 emptyOutDir 必须为 true（否则旧 hash JS 永久堆积，
  导致「改了没生效」，见 08 §三十一）。但 Vite 自己清空 dist 时，
  可能触发 IDE（VS Code / Cursor）的 safe-delete 批量删除拦截而失败。

  解法：构建前**先用 cmd /c rmdir 主动清干净**（01 §七 铁律：
  大目录删除走 cmd /c rmdir /s /q，绕过 IDE safe-delete 拦截），
  这样 Vite 的 emptyOutDir 面对的是空目录，不会再触发拦截。

用法：
  python scripts/clean_dist.py            # 清理并打印统计
  python scripts/clean_dist.py --check    # 只报告，不删除
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "frontend", "dist")


def dist_stats():
    """返回 (文件数, js 数, 总字节数)。目录不存在返回 (0, 0, 0)。"""
    if not os.path.isdir(DIST):
        return 0, 0, 0
    n_files = n_js = n_bytes = 0
    for dirpath, _dirnames, filenames in os.walk(DIST):
        for fn in filenames:
            n_files += 1
            if fn.endswith(".js"):
                n_js += 1
            try:
                n_bytes += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    return n_files, n_js, n_bytes


def clean():
    before = dist_stats()
    if before[0] == 0:
        print(f"dist 不存在或已为空，跳过清理：{DIST}")
        return 0

    # Windows：用 cmd /c rmdir /s /q 绕过 IDE safe-delete 拦截
    if os.name == "nt":
        rc = subprocess.call(
            ["cmd", "/c", "rmdir", "/s", "/q", DIST],
            shell=False,
        )
        if rc != 0:
            print(f"[warn] rmdir 返回 {rc}，回退 Python shutil.rmtree")
            import shutil

            shutil.rmtree(DIST, ignore_errors=True)
    else:
        import shutil

        shutil.rmtree(DIST, ignore_errors=True)

    after = dist_stats()
    print(
        f"已清理 dist：{before[0]} 个文件（{before[1]} 个 JS，"
        f"{before[2] / 1024:.0f} KB） -> {after[0]} 个文件"
    )
    return before[0]


def main():
    args = sys.argv[1:]
    if "--check" in args:
        n_files, n_js, n_bytes = dist_stats()
        print(f"dist 现状：{n_files} 个文件（{n_js} 个 JS，{n_bytes / 1024:.0f} KB）")
        # 堆积判定：index.html 引用的入口 bundle 有几个。
        # 正常构建：1 个入口 index-XXXX.js + 若干分包（core-*.js / index-*.js 懒加载块），
        # 分包是 Vite code-splitting 的正常产物，不能按文件名计数，
        # 只有 index.html 里实际引用的入口数 > 1 才说明旧文件没被清掉。
        entry = os.path.join(DIST, "index.html")
        n_entry = 0
        if os.path.isfile(entry):
            import re

            with open(entry, encoding="utf-8", errors="replace") as fh:
                html = fh.read()
            # 只取 src/href 里指向 assets/*.js 的引用（排除 modulepreload 的 css）
            n_entry = len(
                set(re.findall(r'assets/(index-[A-Za-z0-9_\-]+\.js)', html))
            )
        if n_entry > 1:
            print(f"[warn] index.html 引用了 {n_entry} 个入口 JS，疑似旧 hash 堆积，建议清理")
        elif n_entry == 1:
            print("入口 JS: 1 个（正常，无堆积）")
        return
    clean()


if __name__ == "__main__":
    main()
