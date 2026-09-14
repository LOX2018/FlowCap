# -*- coding: utf-8 -*-
"""浏览器指纹 —— HTTP 层出站指纹档案。

## 2026-09-14 重构（实测驱动的三项修正）

原实现有三个问题（均由实测发现，见 `工作记忆/02_指纹浏览器与凭证.md`）：

1. **版本号写死 150**，而内核实际是 148
   → 同一账号对抖音呈现「HTTP 说 Chrome150 / 浏览器说 Chrome148」。
2. **进程级单例缓存**（`_profile`），多账号场景所有账号共享同一份 UA/几何
   → 账号间无法隔离（同进程多账号会拿到完全相同的声明值）。
3. **值与浏览器真实值无关**（自己随机），与内核实测值必然不一致
   → `cpu_core_num=12` / `screen=随机预设` 对不上内核。

## 修正策略

- **版本号改为「读内核真实版本」**：从 `VB_CHROME_EXE` 的文件版本信息取，
  取不到再退回内核目录名解析，最后才用兜底常量。这样**内核升版即自动跟随**，
  不再需要人工同步（本次矛盾的根因）。
- **按账号派生**：`get_profile(account)` 返回账号级档案；不传 account 时
  按 `"default"` 派生（保持旧调用点可用，行为从「进程级固定」变为
  「按默认账号固定」，对单账号场景等价）。
- **保留原字段形状**（ua / sec_ch_ua / geo / webgl_* ...）不改契约，
  调用方（header.py / params.py / proto.py / mstoken.py）零改动即可受益。

⚠️ 仍未解决：HTTP 层与浏览器层是「两套独立派生」。彻底单源化（HTTP 层直接读
   浏览器真实值）需 189 处调用点接线，属独立任务。本模块先消除
   **版本号矛盾** 与 **账号间共享** 两个最刺眼的问题。
"""
import os
import random as _rnd
import re as _re
import threading as _threading

GEO_PRESETS = (
    (1920, 937, 1920, 1040, 1920, 1040, 1920, 1080),
    (1366, 637, 1366, 728, 1366, 728, 1366, 768),
    (1536, 737, 1536, 824, 1536, 824, 1536, 864),
    (1440, 773, 1440, 860, 1440, 860, 1440, 900),
    (1280, 593, 1280, 680, 1280, 680, 1280, 720),
)

GPU_PRESETS = (
    ("Google Inc. (NVIDIA)",
     "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)",
     "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002503) Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)",
     "ANGLE (Intel, Intel(R) UHD Graphics 770 (0x00004680) Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (AMD)",
     "ANGLE (AMD, AMD Radeon RX 6600 (0x000073FF) Direct3D11 vs_5_0 ps_5_0, D3D11)"),
)

# 兜底版本：取不到内核版本时使用（**不应常态命中**，命中会重新引入矛盾）
_FALLBACK_VERSION = "148.0.0.0"

_lock = _threading.RLock()
_cache: dict = {}          # account -> profile
_kernel_ver_cache: str | None = None
_kernel_ver_resolved = False


def _chrome_exe_path() -> str:
    """取指纹内核可执行文件路径（优先配置，其次扫 vb_chromium 目录）。"""
    try:
        from auto_dm import config as _cfg
        rel = (getattr(_cfg, "VB_CHROME_EXE", "") or "").strip()
        if rel:
            if os.path.isabs(rel):
                return rel
            root = os.environ.get("DY_APP_ROOT", "").strip().strip('"')
            if not root or not os.path.isdir(root):
                root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            p = os.path.join(root, rel)
            if os.path.isfile(p):
                return p
    except Exception:
        pass
    # 兜底：扫应用根下 vb_chromium/*/chrome.exe
    root = os.environ.get("DY_APP_ROOT", "").strip().strip('"')
    if not root or not os.path.isdir(root):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = os.path.join(root, "vb_chromium")
    try:
        for name in sorted(os.listdir(base)):
            p = os.path.join(base, name, "chrome.exe")
            if os.path.isfile(p):
                return p
    except Exception:
        pass
    return ""


def kernel_version() -> str:
    """读**指纹内核的真实版本**（带缓存，进程内只解析一次）。

    三级来源（前面的取不到才退后）：
      1) 内核 exe 的 Windows 文件版本信息（实测可靠，如 148.0.7778.215）
      2) 内核目录名 / zip 名里的 `NNN.0.MMMM.PPP` 版本串
      3) 兜底常量 `_FALLBACK_VERSION`

    **设计意图**：让 HTTP 层声明的版本自动跟随内核，杜绝人工遗忘造成的
    「HTTP 说 vX / 浏览器说 vY」矛盾（本次问题的根因）。
    """
    global _kernel_ver_cache, _kernel_ver_resolved
    with _lock:
        if _kernel_ver_resolved:
            return _kernel_ver_cache or _FALLBACK_VERSION

        ver = ""
        exe = _chrome_exe_path()
        # 1) 文件版本信息（Windows）
        if exe and os.path.isfile(exe):
            try:
                import subprocess
                flags = 0x08000000  # CREATE_NO_WINDOW
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "(Get-Item '%s').VersionInfo.ProductVersion" % exe],
                    capture_output=True, timeout=15, creationflags=flags)
                txt = (out.stdout or b"").decode("utf-8", "replace").strip()
                if _re.match(r"^\d+\.\d+\.\d+", txt):
                    ver = txt
            except Exception:
                ver = ""
        # 2) 路径串里的版本号（目录名/文件名，如 ungoogled-chromium_148.0.7778.215-1.1）
        if not ver and exe:
            m = _re.search(r"(\d{2,3}\.\d+\.\d+\.\d+)", os.path.basename(os.path.dirname(exe)))
            if not m:
                m = _re.search(r"(\d{2,3}\.\d+\.\d+\.\d+)", exe)
            if m:
                ver = m.group(1)
        _kernel_ver_cache = ver or ""
        _kernel_ver_resolved = True
        return _kernel_ver_cache or _FALLBACK_VERSION


def _major(ver: str) -> str:
    """从完整版本取主版本号字符串（用于 UA / sec-ch-ua 的 `NNN.0.0.0` 形式）。"""
    m = _re.match(r"^(\d+)", (ver or "").strip())
    return m.group(1) if m else _FALLBACK_VERSION.split(".")[0]


def _seed_of(account: str | None):
    """账号级稳定种子 —— 复用浏览器指纹种子，保证两层同源（同一 crc32）。"""
    try:
        from vbrowser import fingerprint_seed_of
        return fingerprint_seed_of(account or "")
    except Exception:
        if not account:
            return None
        import zlib
        return zlib.crc32(account.encode("utf-8")) % 100000000


def get_profile(account: str | None = None):
    """指纹档案（**按账号**稳定；同账号跨调用/跨重启一致）。

    **本函数是兼容入口** —— 内部委托给唯一真源 `fingerprint_profile()`，
    避免「两套派生逻辑」再次分叉（本次矛盾的根因就是多源）。

    `account=None` 时按 "default" 派生 —— 兼容旧调用点，
    对单账号场景与原 `_profile` 进程级行为等价。
    """
    key = account or "default"
    with _lock:
        if key not in _cache:
            _cache[key] = fingerprint_profile(account)
        return _cache[key]



def _build_profile(account: str | None):
    seed = _seed_of(account)
    rnd = _rnd.Random(seed) if seed is not None else _rnd.Random()
    geo = rnd.choice(GEO_PRESETS)
    gpu = rnd.choice(GPU_PRESETS)

    ver_full = kernel_version()          # 例：148.0.7778.215
    maj = _major(ver_full)               # 例：148
    v4 = f"{maj}.0.0.0"                  # UA/CH 里惯例用 NNN.0.0.0

    return {
        "ua": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               f"(KHTML, like Gecko) Chrome/{v4} Safari/537.36"),
        "sec_ch_ua": (f'"Not;A=Brand";v="8", "Chromium";v="{maj}", '
                      f'"Google Chrome";v="{maj}"'),
        "sec_ch_ua_platform": '"Windows"',
        "browser_name": "Chrome",
        "browser_version": v4,
        "engine_name": "Blink",
        "engine_version": v4,
        "os_name": "Windows",
        "os_version": "10",
        "platform": "Win32",
        "cpu_core_num": "12",
        "device_memory": "8",
        "geo": geo,
        "webgl_vendor": gpu[0],
        "webgl_renderer": gpu[1],
        "screen_width": str(geo[6]),
        "screen_height": str(geo[7]),
        # 诊断用（不参与出站契约）
        "_kernel_version": ver_full,
        "_seed": seed,
    }


# ---------------------------------------------------------------------------
# 单源化档案（Single Source of Truth）：浏览器层与 HTTP 层共用同一份账号档案
# ---------------------------------------------------------------------------

# 内核**已实测可用**的命令行开关（2026-09-14 实测，见工作记忆 02）：
#   --fingerprint-platform / --fingerprint-brand / --fingerprint-hardware-concurrency
#   --timezone / --lang / --accept-lang
# 注：--fingerprint-screen-width/height **实测无效**（属内部偏好名，非 CLI 开关），
#     屏幕尺寸只能通过启动参数 --window-size 控制，故不在此处映射。

_CORES_POOL = (2, 4, 6, 8, 10, 12, 14, 16, 20, 24, 32)   # 扩宽以降低账号间撞同概率
_PLATFORM_CHOICES = ("windows",)      # 本项目恒 Windows（改平台须同步 UA/CH，暂不放开）
_TZ_BY_PLATFORM = ("Asia/Shanghai",)


def launch_args(account: str | None = None) -> list:
    """账号指纹档案 → 完整 launch args（含**屏外窗口尺寸**修正）。

    ## 为何单独一个函数（2026-09-14 实测）

    `--window-size` 会被 Playwright 的 viewport 模拟覆盖（实测：设了
    `--window-size=1536,864`，但 page 里 `screen` 仍是 Playwright 默认
    1280x720）。因此**本函数不负责 screen** —— screen 由 Playwright 侧的
    `viewport` 参数决定，必须由调用方（vbrowser）一并传入，见
    `viewport_for(account)`。
    """
    p = fingerprint_profile(account)
    return [
        f"--fingerprint-platform={p['_platform_arg']}",
        f"--fingerprint-brand={p['_brand_arg']}",
        f"--fingerprint-hardware-concurrency={p['_cores']}",
        f"--timezone={p['_timezone_arg']}",
        f"--lang={p['_lang_arg']}",
        f"--accept-lang={p['_accept_lang_arg']}",
    ]


def viewport_for(account: str | None = None) -> dict:
    """返回该账号应设的 Playwright `viewport`（决定 page 里的 screen 值）。

    实测依据：Playwright 未设 viewport 时用默认 1280x720，且它**优先于**
    `--window-size`；只有显式传 viewport 才能让 `screen` 变成档案值。
    """
    p = fingerprint_profile(account)
    return {"width": int(p["_window_w"]), "height": int(p["_window_h"])}


def browser_args(account: str | None = None) -> list:
    """向后兼容入口 —— 等价于 `launch_args()`（不再含 --window-size）。"""
    return launch_args(account)



def fingerprint_profile(account: str | None = None) -> dict:
    """**账号级指纹真源** —— 浏览器层与 HTTP 层都从这里取值。

    ## 为什么要它（2026-09-14 实测驱动）

    原架构是「两层各自随机」：内核靠 `--fingerprint=SEED` 自行派生 cores/GPU，
    HTTP 层靠 `random.choice` 自选 screen/GPU → 两层必然对不上
    （实测：HTTP 声称 cores=12，内核实际 24/32/8）。

    正解 = **显式钉死内核值 + 两层共用同一档案**：
      1. 本函数按账号派生一份完整档案（同账号跨重启恒定）；
      2. 浏览器启动时把档案值经 `--fingerprint-*` 开关钉进内核；
      3. HTTP 层从同一档案取值填入请求头/查询参数。
    ⇒ 两层同源、账号间隔离、且**不再依赖内核的随机派生**。

    返回字段（键名与既有 profile 兼容，另加内核开关映射）：
      cores / screen_width / screen_height / platform / brand / timezone / lang
      + `browser_args()`：可直接拼进 launch args 的开关列表
    """
    seed = _seed_of(account)
    rnd = _rnd.Random(seed) if seed is not None else _rnd.Random()

    geo = rnd.choice(GEO_PRESETS)
    cores = rnd.choice(_CORES_POOL)
    platform = rnd.choice(_PLATFORM_CHOICES)
    timezone = rnd.choice(_TZ_BY_PLATFORM)

    ver_full = kernel_version()
    maj = _major(ver_full)
    v4 = f"{maj}.0.0.0"

    # Chromium 内核 UA 无品牌后缀；显式声明 Chrome 品牌更贴近真实用户环境。
    # ⚠️ 注意：--fingerprint-brand=Chrome 会给 UA 加 Google Chrome 标识，
    #    与 sec-ch-ua 里的 "Google Chrome" 自洽（此前内核 UA 无品牌但
    #    sec-ch-ua 却声明 Google Chrome，属另一处不自洽）。
    brand = "Chrome"

    prof = {
        "account": account or "default",
        "_seed": seed,
        "_kernel_version": ver_full,
        # —— HTTP 层用（出站头/查询参数）——
        "ua": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               f"(KHTML, like Gecko) Chrome/{v4} Safari/537.36"),
        "sec_ch_ua": (f'"Not;A=Brand";v="8", "Chromium";v="{maj}", '
                      f'"Google Chrome";v="{maj}"'),
        "sec_ch_ua_platform": '"Windows"',
        "browser_name": "Chrome",
        "browser_version": v4,
        "engine_name": "Blink",
        "engine_version": v4,
        "os_name": "Windows",
        "os_version": "10",
        "cpu_core_num": str(cores),
        "device_memory": "8",
        "screen_width": str(geo[6]),
        "screen_height": str(geo[7]),
        # ⚠️ 兼容键：`utils/strdata_pure.build_fingerprint()` 需要 8 元素元组形式的
        # 屏幕几何（innerW/H, outerW/H, availW/H, sizeW/H）。单源化改造时只保留了
        # screen_width/height 两个标量，导致该消费方 `prof["geo"]` 抛 KeyError
        # → mstoken 构造失败 → 探活 AUTH-051（2026-09-14 实测定位）。
        # 这里直接透传同一份 geo，保证「同一档案」既满足标量消费方也满足元组消费方。
        "geo": geo,
        # —— 浏览器层用（内核开关值）——
        "_cores": cores,
        "_platform_arg": platform,
        "_timezone_arg": timezone,
        "_lang_arg": "zh-CN",
        "_accept_lang_arg": "zh-CN,zh",
        "_brand_arg": brand,
        # 窗口尺寸（screen 只能靠启动参数控制，见上方注释）
        "_window_w": geo[6],
        "_window_h": geo[7],
    }
    return prof


def browser_args(account: str | None = None) -> list:
    """把账号档案转成**内核命令行开关**（供 vbrowser 拼接 launch args）。

    只输出**实测生效**的开关；屏幕尺寸输出为 `--window-size`（非内核指纹开关）。
    """
    p = fingerprint_profile(account)
    args = [
        f"--fingerprint-platform={p['_platform_arg']}",
        f"--fingerprint-brand={p['_brand_arg']}",
        f"--fingerprint-hardware-concurrency={p['_cores']}",
        f"--timezone={p['_timezone_arg']}",
        f"--lang={p['_lang_arg']}",
        f"--accept-lang={p['_accept_lang_arg']}",
    ]
    if p.get("_window_w") and p.get("_window_h"):
        args.append(f"--window-size={p['_window_w']},{p['_window_h']}")
    return args

