# -*- coding: utf-8 -*-
"""
一键封装脚本（PyInstaller）
------------------------------------------------------------------
把项目打包为「双击即运行」的可执行封装版，取消任何 .bat / python -m 脚本启动制。

产物：
    dist/DYAutoDM/DYAutoDM.exe      <- 统一入口（WebView 前端 / 守护 / 接收 均由此 exe 按 --mode 分流）
    dist/DYAutoDM/...              <- 浏览器内核 vb_chromium、profile、.env、logs 等运行时资源

用法（需在装有真实 Python 3.10+ 的机器上执行，本机为 Store 占位符无法运行）：
    pip install pyinstaller
    python build_exe.py
打包完成后，把 dist/DYAutoDM 整个目录发给测试人员，双击 DYAutoDM.exe 即启动 WebView 前端。

守护由 WebView 前端内部按需在浏览器/接收守护接口拉起，因此无需任何外部脚本。
"""
import os
import sys
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(HERE, "dist", "DYAutoDM")
BUILD_DIR = os.path.join(HERE, "build")
VERSION_FILE = os.path.join(HERE, "version.txt")

# 测试版(True) / 正式版(False)。
# 测试版保留控制台窗口(--noconsole 关闭)，运行 exe 时会带一个黑窗实时打印日志，方便 debug；
# 正式版无窗口、干净交付。当前用户需求：测试版。
TEST_BUILD = True

# 需随附的运行时资源（目录 -> 目标子目录名）
RUNTIME_DIRS = {
    "vb_chromium": "vb_chromium",
    "vb_profile_default": "vb_profile_default",
    "vb_profile_dm": "vb_profile_dm",
    "pw_profile_dm": "pw_profile_dm",
}

# 前端 web 资源（onefile 已通过 --add-data 打入 exe，这里额外随附便于查看/调试）
RUNTIME_WEB = "web"

# 需随附的单文件
RUNTIME_FILES = [".env", ".env.stray_bak"]


def bump_version():
    """读取 version.txt，自动 +1，写回并返回新版本串。

    支持两种格式：
      - 普通数字版本（如 0.1.6）：递增最后一个数字段 -> 0.1.7
      - CS 标识版本（如 CS0.01）：保留 CS 前缀，递增末两位 -> CS0.02
        这是给用户交付的"现场测试版"标识，便于追溯哪次打包。
    """
    ver = "CS0.01"
    if os.path.isfile(VERSION_FILE):
        try:
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                ver = f.read().strip() or ver
        except Exception:
            pass
    if ver.upper().startswith("CS"):
        # CS0.01 -> 前缀 "CS"，主号 "0"，次号 "01"
        body = ver[2:]
        parts = body.split(".")
        try:
            parts[-1] = f"{(int(parts[-1]) + 1):02d}"
            new_ver = "CS" + ".".join(parts)
        except ValueError:
            new_ver = ver + ".1"
    else:
        parts = ver.split(".")
        try:
            parts[-1] = str(int(parts[-1]) + 1)
        except ValueError:
            parts.append("1")
        new_ver = ".".join(parts)
    try:
        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            f.write(new_ver)
    except Exception:
        pass
    return new_ver


def clean():
    for d in (DIST_DIR, BUILD_DIR):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)



def collect_data():
    """手动拷贝运行时资源到 dist/DYAutoDM（PyInstaller --add-data 也可，但手动更直观可控）。"""
    os.makedirs(DIST_DIR, exist_ok=True)
    for src, dst in RUNTIME_DIRS.items():
        s = os.path.join(HERE, src)
        d = os.path.join(DIST_DIR, dst)
        if os.path.isdir(s):
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
            shutil.copytree(s, d)
            print(f"[copy] {src} -> {dst}")
    for f in RUNTIME_FILES:
        s = os.path.join(HERE, f)
        if os.path.isfile(s):
            shutil.copy2(s, os.path.join(DIST_DIR, f))
            print(f"[copy] {f}")
    # 前端 web 资源（随附便于查看/调试，运行期实际来自 exe 内部 --add-data）
    s = os.path.join(HERE, RUNTIME_WEB)
    d = os.path.join(DIST_DIR, RUNTIME_WEB)
    if os.path.isdir(s):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(s, d)
        print(f"[copy] {RUNTIME_WEB}")
    # logs 目录占位（运行期生成）
    os.makedirs(os.path.join(DIST_DIR, "logs"), exist_ok=True)


def build(version):
    entry = os.path.join(HERE, "auto_dm", "launcher.py")
    # 测试版保留控制台窗口方便 debug；正式版加 --noconsole 无黑窗。
    console_flag = [] if TEST_BUILD else ["--noconsole"]
    build_tag = f"TEST-{version}" if TEST_BUILD else version
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", "DYAutoDM",
        "--onefile",
        *console_flag,
        f"--version-file={_make_version_info(version)}",
        "--paths", HERE,
        "--hidden-import", "auto_dm.browser_daemon",
        "--hidden-import", "auto_dm.recv_daemon",
        "--hidden-import", "auto_dm.features",
        "--hidden-import", "auto_dm.web_bridge",
        "--hidden-import", "webview",
        "--hidden-import", "urllib.parse",
        "--collect-submodules", "dy_apis",
        "--collect-submodules", "auto_dm",
        "--collect-submodules", "utils",
        "--add-data", os.path.join(HERE, "web") + ";web",
        entry,
    ]
    print(f"[build] 运行 PyInstaller ...  版本={build_tag}  测试版={TEST_BUILD}")
    subprocess.check_call(cmd)
    # onefile 产物在 dist/DYAutoDM.exe；按现场测试版标识改名（如 DYAutoDM_CS0.01.exe）
    onefile = os.path.join(HERE, "dist", "DYAutoDM.exe")
    if os.path.isfile(onefile):
        os.makedirs(DIST_DIR, exist_ok=True)
        target_name = f"DYAutoDM_{version}.exe"
        shutil.move(onefile, os.path.join(DIST_DIR, target_name))
        print(f"[build] 已生成 {os.path.join(DIST_DIR, target_name)}")


def _make_version_info(version):
    """生成 PyInstaller 版本信息资源文件（写入版本号/测试版标识），返回路径。"""
    import tempfile
    tag = "TEST" if TEST_BUILD else "RELEASE"
    # 版本号转 4 段 (a,b,c,0)
    # 版本号资源需要 4 段数字。CS 标识（如 CS0.01）含字母，需转成派生数字：
    #   CS0.01 -> 取数字段 [0, 1] 补足 4 段 -> (0, 0, 1, 0)
    raw = version.upper().replace("CS", "") if version.upper().startswith("CS") else version
    nums = []
    for seg in raw.split("."):
        try:
            nums.append(int(seg))
        except ValueError:
            nums.append(0)
    while len(nums) < 4:
        nums.append(0)
    nums = (nums + [0] * 4)[:4]
    vi = f'''# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({nums[0]}, {nums[1]}, {nums[2]}, {nums[3]}),
    prodvers=({nums[0]}, {nums[1]}, {nums[2]}, {nums[3]}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([StringTable(
      u'040904B0',
      [StringStruct(u'FileVersion', u'{version}'),
       StringStruct(u'ProductVersion', u'{version}'),
       StringStruct(u'ProductName', u'DYAutoDM {version}'),
       StringStruct(u'FileDescription', u'DYAutoDM {version} {tag}')])]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
'''
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="ver_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(vi)
    return path


def main(no_clean=False, no_bump=False):
    version = "" if no_bump else bump_version()
    if not version:
        # --no-bump 时不递增，直接读当前版本号
        version = "CS0.01"
        if os.path.isfile(VERSION_FILE):
            try:
                with open(VERSION_FILE, "r", encoding="utf-8") as f:
                    version = f.read().strip() or version
            except Exception:
                pass
        print(f"[build] --no-bump：保持版本 {version}（不递增）")
    if not no_clean:
        clean()
    build(version)
    collect_data()
    print("\n[done] 封装完成：", DIST_DIR)
    print(f"       版本={version}  测试版={TEST_BUILD}")
    print("       双击 DYAutoDM.exe 启动 WebView 前端；守护由前端内部自动拉起。")


if __name__ == "__main__":
    import sys
    _no_clean = "--no-clean" in sys.argv
    _no_bump = "--no-bump" in sys.argv
    main(no_clean=_no_clean, no_bump=_no_bump)
