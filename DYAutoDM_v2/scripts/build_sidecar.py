"""
PyInstaller 打包脚本：把 backend 打包成 Tauri sidecar 二进制

产出三个二进制（放到 src-tauri/binaries/）：
- dyautodm-backend.exe           (FastAPI 主后端)
- dyautodm-browser-daemon.exe    (凭证守护)
- dyautodm-recv-daemon.exe       (私信接收守护)

Tauri 2 的 externalBin 要求文件名带 target-triple 后缀，例如
dyautodm-backend-x86_64-pc-windows-msvc.exe。本脚本在打包后自动为每个
二进制创建带后缀的硬链接（同盘符不占额外空间），链接失败则退化为复制。
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
BINARIES = ROOT / "src-tauri" / "binaries"

EXT = ".exe" if platform.system() == "Windows" else ""

# 测试白名单注入标记（build_sidecar.inject/restore 与守卫测试共用，避免字面量漂移）
_WS_START = "# ---DM_TEST_WHITELIST_INJECT_START---"
_WS_END = "# ---DM_TEST_WHITELIST_INJECT_END---"


def _app_version() -> str:
    """从 tauri.conf.json 读当前版本（唯一真源），供构建时注入 sidecar。

    2026-09-13：解决「前端新版本 / 后端旧版本」无校验的缺口。
    构建时把版本写进 backend/version.json（随源码打包进 sidecar），
    后端 /api/version 读它，桌面端启动时比对，不一致即在 UI 上显式告警。
    """
    try:
        import json as _json
        conf = ROOT / "src-tauri" / "tauri.conf.json"
        with open(conf, encoding="utf-8") as f:
            return str((_json.load(f) or {}).get("version") or "unknown")
    except Exception:
        return "unknown"


def _write_version_file(build_kind: str = "debug") -> str:
    """把版本 + **构建类型**固化进 backend/_build_version.py（编译期常量）。

    2026-09-13：不用 version.json 外部文件 —— 该文件不会被 PyInstaller 打进
    sidecar，Frozen 后 __file__ 指向解压目录，运行时根本读不到，版本会退化成
    unknown。改成生成一个 Python 模块，随源码一起编译进 sidecar，100% 可靠。

    2026-09-20（用户铁律）：**默认 debug 版**，除非显式 --release。
    构建类型同时写入 BUILD_KIND，供运行时/部署脚本识别，避免"打了正式版却
    以为是 debug"这类无声偏差。
    """
    v = _app_version()
    kind = "release" if str(build_kind).lower() == "release" else "debug"
    try:
        fp = BACKEND / "_build_version.py"
        fp.write_text(
            '"""构建期生成的版本常量（勿手改；由 scripts/build_sidecar.py 写入）。"""\n'
            f'BUILD_VERSION = "{v}"\n'
            f'BUILD_KIND = "{kind}"   # debug = 测试版（默认）；release = 正式发布\n',
            encoding="utf-8")
        print(f"[版本] backend/_build_version.py = {v} ({kind})")
    except Exception as e:
        print(f"[版本] 写入失败: {e}")
    return v


def _target_triple() -> str:
    """返回当前平台的 Rust target triple（与 Tauri externalBin 命名一致）。"""
    sys_name = platform.system()
    machine = platform.machine().lower()
    if sys_name == "Windows":
        arch = "aarch64" if "arm" in machine or "aarch" in machine else "x86_64"
        return f"{arch}-pc-windows-msvc"
    if sys_name == "Darwin":
        arch = "aarch64" if machine == "arm64" else "x86_64"
        return f"{arch}-apple-darwin"
    arch = "aarch64" if "arm" in machine or "aarch" in machine else "x86_64"
    return f"{arch}-unknown-linux-gnu"


def _link_target_triple(src: Path) -> None:
    """不再创建无 triple 别名：Tauri externalBin 要求文件名带 triple 后缀，
    无 triple 的同名 .exe 会与 triple 文件冲突导致 externalBin 嵌入失败。
    本函数保留为空操作（兼容旧调用），实际产物名已由 build_one 直接带 triple。"""
    return


def _runtime_resources() -> list[str]:
    """返回需要随 sidecar 一起 --add-data 打包的运行时资源。

    重要：PyInstaller onefile 的 --add-data 会把资源解压到 sys._MEIPASS
    临时目录，而 vbrowser.app_root() 在打包态返回 dirname(sys.executable)
    （exe 旁边）。两者不一致，--add-data 的资源按 app_root() 解析不到。

    因此 vb_chromium / vb_profile_* / pw_profile_dm / .env / logs 等
    运行时资源【不应打进 sidecar】，而应放 sidecar exe 旁边（由 Tauri
    bundle.resources 分发或 build 后手动复制）。这里返回空列表。
    """
    return []


def build_one(entry: str, name: str, mode: str = "onefile") -> None:
    """打包单个 sidecar，产物名直接带 target-triple 后缀（Tauri externalBin 要求）。

    mode="onefile"：单 exe（旧模式，每次启动解包 ~114MB 到 %TEMP%，慢）。
    mode="onedir" ：目录模式（免解压，秒级启动）。产物是
        src-tauri/binaries/<full>/ 目录（内含 <full>.exe + _internal/ 依赖），
        Tauri 侧 resolve_sidecar 会优先探测该目录形态。
    """
    triple = _target_triple()
    full = f"{name}-{triple}"
    print(f"\n=== 打包 {name} (mode={mode} -> {full}{EXT}) ===")
    BINARIES.mkdir(parents=True, exist_ok=True)
    out = BINARIES / f"{full}{EXT}"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", full,
        "--distpath", str(BINARIES),
        "--workpath", str(BACKEND / "build" / name),
        "--specpath", str(BACKEND / "build" / name),
        "--clean", "--noconfirm",
    ]
    if mode == "onedir":
        cmd.append("--onedir")
        # 排除内容目录（_internal 是 PyInstaller 6 的默认布局，无需额外参数），
        # 产物为 BINARIES/<full>/<full>.exe —— sidecar.rs 与 daemon_launcher
        # 均已支持目录形态探测。
    else:
        cmd.append("--onefile")
        out = BINARIES / f"{full}{EXT}"
    cmd += _runtime_resources()
    # 2026-09-05：wp_recv 是 main.py 里 asyncio 动态导入的模块
    # （from daemon.wp_recv import run_wp_recv_loop 写在 lifespan 内部），
    # PyInstaller 静态分析扫不到，必须显式 hidden-import，
    # 否则打包后 WP 通道启动失败（ModuleNotFoundError）。
    # 2026-09-13：版本常量模块由本脚本构建期生成（backend/_build_version.py），
    # 三份 sidecar 都要能 import 到它（backend 目录已在 paths 里），
    # 显式 hidden-import 防静态分析漏打 —— 版本校验依赖它。
    cmd += ["--hidden-import", "_build_version"]
    if entry == "main.py":
        cmd += ["--hidden-import", "daemon.wp_recv"]
        # 2026-09-07：AI 知识库文件导入用了 UploadFile/Form，python-multipart
        # 是隐式依赖（PyInstaller 扫不到），缺失时 backend 启动即崩
        # （RuntimeError: Form data requires "python-multipart"）。
        cmd += ["--hidden-import", "multipart"]
        cmd += ["--hidden-import", "python_multipart"]
        # 2026-09-08：IM 通知模块（backend/notify 新包）。main.py 在
        # try/except 里 import api.notify（挂载失败要降级而非崩溃），
        # 这种「容错 import」易被静态分析判为可选依赖而漏打，故显式声明；
        # notify 内部还按渠道动态 import aiohttp，一并兜底。
        for _m in (
            "notify",
            "notify.channels",
            "notify.cmd_parser",
            "notify.notifier",
            "api.notify",
            "aiohttp",
            # 2026-09-08 追加：events 是业务侧（core/sender.py、core/auto_dm.py）
            # 在**函数体内**动态 import 的（`from notify import events as _ev`），
            # 静态分析扫不到，必须显式声明，否则打包后事件告警静默失效。
            "notify.events",
            # 2026-09-09 追加：IM 网关 + 入站通道（v0.38.5）。gateway 在
            # api/notify.py 顶部 import，但 inbound 在函数体内 import
            # （init_notifier / save_config），botpy 由 inbound 内部
            # 条件 import（未装也能启动主流程）—— 均需显式声明。
            "notify.gateway",
            "notify.inbound",
            "botpy",
            "botpy.message",
            "botpy.gateway",
        ):
            cmd += ["--hidden-import", _m]
        # 2026-09-09：会员体系（v0.37.0）。api/member.py 与 services/member_ctx
        # 都在 main.py 内「函数体内 import」（登录后初始化），静态分析扫不到
        # 必须显式声明，缺失时登录直接 500。
        for _m in (
            "api.member",
            "services.member_store",
            "services.member_ctx",
            "cryptography",
            "cryptography.fernet",
            # 2026-09-13：patchright（反检测版 Playwright，接管 CDP 协议层泄漏）。
            # vbrowser.pw_async_api/pw_sync_api 在**函数体内** import，静态分析扫不到，
            # 必须显式声明，否则打包后回退原生 Playwright（等于没接）。
            "patchright",
            "patchright.async_api",
            "patchright.sync_api",
            "patchright._impl",
            "patchright._impl._browser_type",
            "patchright._impl._browser",
            # 2026-09-13：出口 IP 探测支持 socks5 节点（urllib 原生不支持
            # socks://，需 PySocks 的 SocksiPyHandler）。这两个模块在
            # vbrowser.probe_egress_ip_direct 里函数体内 import，静态分析
            # 扫不到，必须显式声明，否则代理连接测试的 socks5 模式失效。
            "socks",
            "sockshandler",
            # 2026-09-20：Camoufox（Firefox 内核，C++ 层指纹注入）。
            # vbrowser_camoufox 在 launch_async/launch_sync 内**函数体 import**，
            # 静态分析扫不到，必须显式声明；否则 DY_BROWSER_KERNEL=camoufox
            # 在打包产物里恒走 BCC-058 回退分支（换内核无效且无声）。
            "vbrowser_camoufox",
            "camoufox_capture",
            "camoufox",
            "camoufox.sync_api",
            "camoufox.async_api",
            # 2026-09-20 v0.44.12：Camoufox 成为唯一内核后，内核可用性校验与指纹版本
            # 解析改从 camoufox.pkgman 取（installed_verstr / launch_path），且这两处在
            # **函数体内** import（vbrowser.should_use_vb / utils/fingerprint._chrome_exe_path）
            # → 静态分析扫不到，必须显式声明；漏打则打包态抛 BCC-070。
            "camoufox.pkgman",
            "browserforge",
            "orjson",
            # 2026-09-20 实测事故：仅声明 "orjson" 时，PyInstaller 打入了
            # orjson/__init__.py，却**漏掉原生扩展 orjson.cp314-win_amd64.pyd**
            # → 运行时 `import orjson.orjson` 报 ModuleNotFoundError。
            # 该模块被 camoufox/browserforge（函数体内 import）间接依赖，
            # 故必须以 --collect-all 带齐二进制（见下方循环）。
            "geoip2",
            "maxminddb",
            # 2026-09-17：TLS 原生 OS 信任库（backend/utils/tls_policy.py）。
            # main.py 在 lifespan 内函数体导入 + truststore.inject_into_ssl()，
            # 静态分析扫不到 → 缺失则打包后无 OS 信任库（自签证书环境仍会失败）。
            # 未装时 tls_policy 自动降级，故此声明是「有则更好」而非硬依赖。
            "truststore",
            "certifi",
        ):
            cmd += ["--hidden-import", _m]
    # 2026-09-15：browser_daemon.py 的 JS 常量模块（daemon/browser_daemon_js.py）。
    # 它是**入口脚本的同级模块**，入口模式下用
    #   `from browser_daemon_js import ...`（绝对导入 fallback）加载，
    # PyInstaller 对入口脚本的**同级模块**不做包内分析 → 扫不到 → 打包后
    #   `ModuleNotFoundError: No module named 'browser_daemon_js'`（实测崩）。
    # 必须显式声明；同时把 `daemon.browser_daemon_js` 一并声明以覆盖包内模式。
    if entry in ("daemon/browser_daemon.py", "daemon/recv_daemon.py"):
        for _m in ("browser_daemon_js", "daemon.browser_daemon_js"):
            cmd += ["--hidden-import", _m]

    # 2026-09-20：Camoufox 依赖链的原生扩展必须以 --collect-all 带齐。
    # 仅 --hidden-import 只会打入 .py，**漏掉 .pyd**（orjson.cp314-win_amd64.pyd），
    # 表现为运行时 `No module named 'orjson.orjson'`（源码态正常、打包态必挂）。
    # 这是「必须实机验证打包产物」的典型案例：源码态全绿，打包态才暴露。
    # 覆盖**整个依赖链**（一次修完，不逐个补——同类问题已出现 2 次）：
    #   orjson          → 原生扩展 .pyd（ModuleNotFoundError: orjson.orjson）
    #   browserforge    → 指纹/请求头生成器（含 bayesian 网络数据）
    #   apify_fingerprint_datapoints → data/*.zip 指纹库数据文件
    #     （实测缺 input-network-definition.zip 等 5 个数据文件，报 FileNotFoundError）
    #   camoufox        → 顶层 *.json 预设（fingerprint-presets.json / fonts.json /
    #                     voices.json）——同为 data 文件，不在模块内，必须一并收集
    # 判据：--collect-all 同时收集 .py / .pyd / data 文件与包元数据。
    for _pkg in (
        # ════════════════════════════════════════════════════════════════
        # 仅收集**确实含非 .py 资源**的包（实测逐个扫描得出，勿凭猜测增删）。
        # 判据命令：
        #   python -c "import os;d=r'<site-packages>\\<pkg>';print([f for r,_,fs in
        #   os.walk(d) for f in fs if not f.endswith(('.py','.pyc','.pyi'))])"
        # 只含 py.typed（空标记）的包不收集，避免产物无谓膨胀。
        # ════════════════════════════════════════════════════════════════
        "orjson",                      # orjson.cp314-win_amd64.pyd
        "maxminddb",                   # extension.cp314-win_amd64.pyd
        "camoufox",                    # *.json 预设（fingerprint-presets/fonts/voices）
        "browserforge",                # 指纹/请求头生成器数据
        "apify_fingerprint_datapoints",# data/*.zip 指纹库（5 个数据文件）
        "language_tags",               # ua_parser 依赖：data/json/*.json（11 个）
        # playwright：Camoufox 走标准 Playwright 协议，启动浏览器需要
        # <playwright>/driver/node.exe（Node driver 进程）。
        # 实测事故：产物里只有 playwright-*.dist-info 而**无 driver/node.exe**
        # → 启动时 subprocess 找不到可执行文件 → [WinError 2] 系统找不到指定的文件，
        # 表现为「Camoufox 内核启动失败」（而 patchright 因早已被静态追到而正常）。
        "playwright",
        "patchright",
        # 仅为 py.typed（0 字节类型标记，运行时无影响）—— 补齐以保持自检干净，
        # 避免真缺失被噪音掩盖。
        "geoip2",
        "screeninfo",
        "ua_parser",
    ):
        cmd += ["--collect-all", _pkg]
    # 2026-09-16 v0.43.36：WS 稳态治理模块（daemon/ws_link.py）。
    # RecvChannel._make_link / _catchup_after_reconnect 在**函数体内**
    # `from daemon.ws_link import WSLink` —— 与上面两条完全同类的坑
    # （函数体动态 import 静态分析扫不到）。缺失时 recv_daemon 打包后启动即
    # ModuleNotFoundError，WS 通道整体不可用。必须显式声明。
    if entry == "daemon/recv_daemon.py":
        for _m in ("daemon.ws_link", "ws_link"):
            cmd += ["--hidden-import", _m]
    # 2026-09-16 v0.43.39：会话身份解析单一真相源（services/conv_identity.py）。
    # recv_daemon / wp_recv / browser_daemon 均在**函数体内**延迟导入
    # （`from services import conv_identity`），PyInstaller 静态分析扫不到 —
    # 与 ws_link 完全同类的坑，缺失则打包后 ModuleNotFoundError。
    # 一并声明它内部引用的 services.uid_probe。
    if entry in ("daemon/recv_daemon.py", "daemon/browser_daemon.py",
                 "daemon/wp_recv.py", "main.py"):
        for _m in ("services.conv_identity", "conv_identity",
                   "services.uid_probe", "uid_probe",
                   "services.ttl_cache", "ttl_cache"):
            cmd += ["--hidden-import", _m]
    # 2026-09-18 v0.43.87：账号环境基线（services/env_baseline.py）。
    # browser_daemon 在 run_keepalive / scan_login 内**函数体延迟导入**
    # （`from services.env_baseline import ...`）——函数体动态 import
    # 静态分析扫不到（与 ws_link / conv_identity 完全同类的坑），
    # 缺失则打包后环境基线比对静默失效（try/except 吞掉，无报错）。
    # 它内部引用 services.kv_store（顶层 import，静态分析可追，但一并声明保险）。
    # 2026-09-18 v0.43.88：环境泄漏监测（services/env_audit.py）同坑同修——
    # browser_daemon 在 env_audit_snapshot 内函数体延迟导入。
    if entry in ("daemon/browser_daemon.py", "main.py"):
        for _m in ("services.env_baseline", "env_baseline",
                   "services.kv_store", "kv_store",
                   "services.env_audit", "env_audit"):
            cmd += ["--hidden-import", _m]
    # 2026-09-18 v0.43.91：🔴 **函数体延迟导入的本地模块**（结构性漏打，实测致
    # 「直播/采集私信一条都发不出」）。事故：`core/dispatch.py:350` 在函数体内
    # `from services.dm_dispatch import get_dispatcher`；`services/dm_dispatch.py`
    # 在**任何应用模块里都没有顶层 import**（只有 test_*/_*.py 有，而那些不进产物），
    # 于是 PyInstaller 静态分析与「函数体 import 也能被扫到」的既有认知**双双落空**
    # → 打包后 backend 侧 `No module named 'services.dm_dispatch'`，日志仅一行
    # `SEND-037 dm_dispatch 接入失败，已放弃发送`，其余功能全正常，极难发现。
    #
    # 判据（不要靠推测）：`scripts/diag/list_archive_modules.py <exe> <模块名>`
    # 直接读产物 PYZ 归档核对；`scripts/diag/scan_lazy_imports.py --check` 做静态守卫。
    # 实测三份 sidecar 中真缺失的只有本模块（automation_engine 无生产调用路径，
    # image_sender 在 recv-daemon 里已被静态追到）。
    #
    # ⚠️ 加入内存调节/自动化模块（services.automation_engine 等大依赖）前必须
    # 用 list_archive_modules 复核体积与收录，本批刻意不加入（不扩大交付面）。
    for _m in (
        "services.dm_dispatch", "dm_dispatch",
        # 发送闸门/配额裁决的依赖面（dm_dispatch 内部延迟导入，保持自洽）
        "services.config_tag",
    ):
        cmd += ["--hidden-import", _m]
    # 2026-09-19 v0.43.97：uid_probe 探活依赖 qrcode（扫码登录）。
    # services/uid_probe.py 在函数体内 `import qrcode`（生成二维码用），
    # 静态分析扫不到 → 打包后 `No module named 'qrcode'`，探活整体失败，
    # 使 my_uid 失去「探活回退」来源（叠加 conv_id 推断失败时 my_uid 归空，
    # 导致私信 peer 解析退化、发送异常）。requirements.txt:21 已声明，此处补齐。
    if entry in ("daemon/recv_daemon.py", "daemon/browser_daemon.py",
                 "daemon/wp_recv.py", "main.py"):
        for _m in ("qrcode", "qrcode.image.pil", "qrcode.image.base"):
            cmd += ["--hidden-import", _m]

    cmd += [str(BACKEND / entry)]
    print(" ".join(cmd))
    subprocess.check_call(cmd, cwd=str(BACKEND))
    print(f"产出: {out}")


def build_whitelist_block(WL: dict) -> tuple[str, str]:
    """生成注入体（纯函数，便于单测）。返回 (body, 完整注入块)。

    ## 🔴 为什么必须独立可测（v0.43.91 事故）

    原实现直接内联拼字符串，**空 WL 时产出 `_TEST_WHITELIST = {\\n,\\n}`** ——
    语法非法。而注入发生在 PyInstaller 分析**之前** ⇒ ModuleGraph 解析
    `services/dm_dispatch.py` 时 SyntaxError ⇒ 该模块被**静默丢弃**（日志连
    `Analyzing hidden import 'services.dm_dispatch'` 都不打印），打包后
    backend 调 dm_dispatch 时 `No module named 'services.dm_dispatch'`
    ⇒ 直播/采集**私信一条都发不出**，而其余功能全正常。
    构建末尾 `restore_whitelist()` 又把源码还原成合法空壳 → 源码态 import 正常，
    问题被**完全掩盖**，只有真跑打包产物才暴露。

    契约：任何 WL（含空 dict）产出的注入块都必须 `compile()` 通过。
    """
    lines = ",\n".join(f'    "{k}": {{"{v}"}}' for k, v in (WL or {}).items())
    if WL:
        body = f'_TEST_WHITELIST = {{\n{lines},\n}}\nTEST_WHITELIST_ON = True\n'
    else:
        # 空映射绝不能留裸逗号 —— 那正是 SyntaxError 的来源
        body = "_TEST_WHITELIST = {}\nTEST_WHITELIST_ON = False\n"
    block = (_WS_START + "\n" + body + _WS_END + "\n")
    return body, block


def inject_test_whitelist() -> None:
    """【调试版专用】把测试账号白名单注入 services/dm_dispatch.py。

    仅在显式传 `--debug-whitelist` 时执行。产出的二进制**禁止对外发布**：
    它限制私信只能发给白名单账号，是测试期防误发真人的护栏。

    正式版：**不调用本函数** → 模块内 _TEST_WHITELIST 保持空壳、
    TEST_WHITELIST_ON 恒 False → 白名单逻辑物理不执行。
    """
    import re
    from pathlib import Path

    # 两个测试账号的**私信会话 uid**（不是探活 uid！）。
    # 实测：同账号「探活 uid」与「会话 uid」**可能不一致**（历史事故：
    # 日志持续报 uid 漂移）。白名单比对的是**发送目标的 peer_uid**
    # （来自 conv_id，属会话体系），故此处必须用会话 uid，用探活 uid
    # 会误拒（2026-09-07 真机踩坑）。
    #
    # 语义：WL[账号] = 该账号**允许发送的目标 peer_uid**（对方会话 uid）。
    #
    # 2026-09-16 v0.43.39：账号名与 uid 一律从环境变量读，**绝不硬编码** ——
    # 本软件是通用产品，写死某台机器的账号/uid 会让其他用户无法构建调试版。
    # 格式：DY_DM_TEST_WHITELIST="账号A=对方uidA;账号B=对方uidB"
    # 未设置时注入空白名单（调试版也不限制），并打印提示。
    _raw = os.environ.get("DY_DM_TEST_WHITELIST", "").strip()
    WL: dict = {}
    for _pair in _raw.split(";"):
        if "=" in _pair:
            _k, _v = _pair.split("=", 1)
            if _k.strip() and _v.strip():
                WL[_k.strip()] = _v.strip()
    if not WL:
        print("[warn] 未设 DY_DM_TEST_WHITELIST，注入空白名单（调试版不限制目标）")
    target = Path(__file__).resolve().parent.parent / "backend" / "services" / "dm_dispatch.py"
    start = _WS_START
    end = _WS_END
    src = target.read_text(encoding="utf-8")
    if start not in src or end not in src:
        print("[warn] 未找到注入标记，跳过白名单注入")
        return
    # 直接按 WL 的语义生成，不做任何对称推导
    # （2026-09-07 踩坑：旧代码用 WL[对方名] 取值生成，导致收发关系反转）
    # 2026-09-18 v0.43.91：🔴 生成逻辑抽到 `build_whitelist_block`（可单测），
    # 原实现在 WL 为空时产出 `_TEST_WHITELIST = {\n,\n}` 这种**语法非法**代码，
    # 注入后 PyInstaller 解析该模块即 SyntaxError → **静默丢弃整个模块** →
    # 打包后 `No module named 'services.dm_dispatch'`（SEND-037）。
    _body, _block = build_whitelist_block(WL)
    new = re.sub(re.escape(start) + r".*?" + re.escape(end),
                 lambda _m: _block.rstrip("\n"), src, flags=re.S)
    # 语法自证：注入态必须能被 compile —— 否则 PyInstaller 会静默丢模块
    try:
        compile(new, str(target), "exec")
    except SyntaxError as e:
        raise SystemExit(
            f"[fatal] 白名单注入产生语法错误，打包将静默丢失 services.dm_dispatch："
            f"{e.msg} @ line {e.lineno}: {e.text!r}")
    target.write_text(new, encoding="utf-8")
    print(f"[debug] 已注入测试白名单（仅调试版）: {WL or '（空 = 不限制目标）'}")


def restore_whitelist() -> None:
    """打包后把注入区还原为**空壳**（避免污染工作区源码 / 混入提交）。

    2026-09-15 修复：原实现用 `git checkout -- backend/services/dm_dispatch.py`。
    一旦注入态**被提交进版本库**（实测已发生），该命令还原到的就是**污染版 HEAD**
    —— 自我循环，永远还原不掉，最终正式构建也带白名单（SEND-028 硬拒非白名单发送）。
    现改为**内容级确定性还原**：把标记行之间的内容改回空壳，不依赖 git 状态。
    """
    import re as _re
    start = "# ---DM_TEST_WHITELIST_INJECT_START---"
    end = "# ---DM_TEST_WHITELIST_INJECT_END---"
    target = Path(__file__).resolve().parent.parent / "backend" / "services" / "dm_dispatch.py"
    try:
        src = target.read_text(encoding="utf-8")
        if start not in src or end not in src:
            print("[warn] 未找到注入标记，跳过还原")
            return
        empty = start + "\n_TEST_WHITELIST = {}\nTEST_WHITELIST_ON = False\n" + end
        new = _re.sub(_re.escape(start) + r".*?" + _re.escape(end),
                      lambda _m: empty, src, flags=_re.S)
        target.write_text(new, encoding="utf-8")
        # 自证：**只看注入区内**必须为空壳（注释里出现的同名文字不算）
        check = target.read_text(encoding="utf-8")
        m = _re.search(_re.escape(start) + r"(.*?)" + _re.escape(end), check, _re.S)
        region = (m.group(1) if m else "")
        ok = ("TEST_WHITELIST_ON = False" in region) and ("On = True" not in region)
        print(f"[debug] 已还原 dm_dispatch.py 注入区为空壳（自证 {'通过' if ok else '失败'})")
    except Exception as e:
        print(f"[warn] 还原失败（请手动检查注入区）: {e}")


def _dedupe_internal() -> dict:
    """【性能主线】把三份重复的 `_internal` 收敛为一份共享目录。

    ## 背景（2026-09-14 实测）

    三份 sidecar 各自带一份 `_internal`，实测三者**内容完全相同**：
        files=6514 / 246.3MB，三者交集 6514、各自独有 0
    抽样 43 文件 sha256：42 个一致；唯一差异 `base_library.zip` 解包后
    155 个内部条目**逐条 sha256 全部一致**（仅 zip 容器时间戳元数据不同）。
    ⇒ 功能等价，可安全共享。

    ## 落地形态（零代码改动）

        <binaries>/_internal/                      ← 共享依赖（唯一一份）
        <binaries>/dyautodm-backend-<triple>.exe   ← 三个 exe 平铺
        <binaries>/dyautodm-browser-daemon-<triple>.exe
        <binaries>/dyautodm-recv-daemon-<triple>.exe

    该布局正好命中既有 `resolve_sidecar` 候选 2（`<root>/<full>.exe`），
    `main.py` / `daemon_launcher` / `accounts.py` 亦同 —— **无需改任何解析代码**。

    ## 实测验证（决定性）

    用 NTFS junction 构造上述布局实测：三个 exe **全部依赖加载成功**
    （backend 启动后常驻、两个 daemon 走到参数校验）。

    ## 返回

    {"moved": 保留的共享目录, "removed": [被删的重复目录], "saved_mb": 省下的体积}
    """
    import shutil

    triple = _target_triple()
    names = ["dyautodm-backend", "dyautodm-browser-daemon", "dyautodm-recv-daemon"]
    dirs = [BINARIES / f"{n}-{triple}" for n in names]

    present = [d for d in dirs if d.is_dir()]
    if not present:
        return {"moved": None, "removed": [], "saved_mb": 0}

    shared = BINARIES / "_internal"
    # 1) 选定共享源：**必须无条件使用本次构建的新 _internal**。
    #
    # 2026-09-20 实测事故（陈旧缓存静默污染）：
    #   原实现是「shared 已存在则跳过」，而 binaries/_internal 一旦生成就长期
    #   存在（实测停留 9月14日 的旧副本）→ 每次打包都把旧依赖当共享源，
    #   本次新增的依赖（如 --collect-all orjson 带来的 orjson/*.pyd）**永远进不去**。
    #   表现：源码态全绿、逐 exe 目录里有新依赖，唯独部署产物缺失，
    #         且全程无报错（shared.is_dir() 恒为 True 使分支静默跳过）。
    #   修复：每轮都从「本次构建产出的目录」里取新 _internal 覆盖 shared。
    fresh = None
    for _d in present:
        if (_d / "_internal").is_dir():
            fresh = _d / "_internal"
            break
    if fresh is None:
        if not shared.is_dir():
            return {"moved": None, "removed": [], "saved_mb": 0}
    else:
        # 先摘掉旧共享目录（可能是 junction/符号链接），再顶上新目录
        if shared.exists() or shared.is_symlink():
            if _is_link_or_junction(shared):
                try:
                    os.rmdir(str(shared))
                except OSError:
                    pass
            else:
                shutil.rmtree(str(shared), ignore_errors=True)
        if not shared.is_dir():
            try:
                shutil.move(str(fresh), str(shared))
            except Exception as e:  # noqa: BLE001
                print(f"  ⚠️ 刷新共享 _internal 失败: {e}")


    saved = 0
    removed = []
    for d in present:
        exe = d / f"{d.name}.exe"
        if not exe.is_file():
            continue
        # 2) exe 平铺到 binaries 根（命中既有候选 2）
        dst_exe = BINARIES / exe.name
        try:
            if dst_exe.exists():
                dst_exe.unlink()
            shutil.move(str(exe), str(dst_exe))
        except Exception as e:
            print(f"  ⚠️ 平铺 {exe.name} 失败: {e}")
            continue
        # 3) 删除该目录里的重复 _internal 与空壳目录
        #    ⚠️ _internal 可能是 junction / 目录符号链接（实测构造场景）：
        #    shutil.rmtree 对其处理不当且会被 ignore_errors 静默吞掉 → 残留。
        #    对 reparse point 必须用 os.rmdir（只删链接本身，不跟随删除目标）。
        inner = d / "_internal"
        if inner.exists() or inner.is_symlink():
            is_link = _is_link_or_junction(inner)
            if not is_link:
                # 仅有实体目录才计入「省下的体积」（junction 不占空间）
                try:
                    saved += sum(f.stat().st_size
                                 for f in inner.rglob("*") if f.is_file())
                except Exception:
                    pass
            _remove_link_or_tree(inner)
        try:
            leftover = list(d.iterdir())
            if not leftover:
                d.rmdir()
                removed.append(str(d.name))
            else:
                removed.append(f"{d.name}(余{len(leftover)}项)")
        except Exception:
            pass

    return {"moved": str(shared), "removed": removed,
            "saved_mb": round(saved / 1048576, 1)}


def _is_link_or_junction(p) -> bool:
    """判断是否为 junction / 符号链接（不跟随）。"""
    try:
        if p.is_symlink():
            return True
        # Windows: FILE_ATTRIBUTE_REPARSE_POINT
        attrs = os.lstat(str(p)).st_file_attributes  # type: ignore[attr-defined]
        return bool(attrs & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except Exception:
        return False


def _remove_link_or_tree(p) -> bool:
    """删除目录或链接；**对 reparse point 只删链接本身**。

    返回是否删除成功。目录含 junction 时逐个先摘链接，再清实体目录。
    """
    import shutil as _sh
    try:
        if not p.exists() and not p.is_symlink():
            return True
        if _is_link_or_junction(p):
            try:
                os.rmdir(str(p))          # 只删链接，不跟随
            except OSError:
                os.unlink(str(p))
            return True
        # 实体目录：先递归摘掉内部链接，再整体删除
        for r, dirs, _files in os.walk(str(p)):
            for name in list(dirs):
                child = p.__class__(os.path.join(r, name))
                if _is_link_or_junction(child):
                    try:
                        os.rmdir(str(child))
                    except OSError:
                        pass
                    dirs.remove(name)
        _sh.rmtree(str(p), ignore_errors=True)
        return not p.exists()
    except Exception:
        return False


def main() -> None:
    import sys
    # ════════════════════════════════════════════════════════════════════
    # 构建类型铁律（2026-09-20 用户明确，选 A 方案）：
    #   **默认 debug 版**；只有显式传 --release 才构建正式版。
    #   理由：测试阶段永远不需要正式版，忘加参数就等于误发正式包 ——
    #   把「默认值」本身变成铁律，而不是靠人记得加 --debug-whitelist。
    # ════════════════════════════════════════════════════════════════════
    is_release = "--release" in sys.argv
    build_kind = "release" if is_release else "debug"
    debug_wl = build_kind == "debug"
    # --onedir：免解压目录模式（启动提速重点）。三份 sidecar 全部切换。
    # 回退：不带 --onedir 参数即恢复 onefile。
    mode = "onedir" if "--onedir" in sys.argv else "onefile"

    # ── 醒目横幅（用户要求：不能被 PyInstaller 刷屏淹没）──
    _kind_cn = "DEBUG 测试版（非正式发布）" if build_kind == "debug" else "RELEASE 正式版"
    print("=" * 72)
    print(f"  构建类型：{_kind_cn}")
    print(f"  版本    ：{_app_version()}")
    print(f"  形态    ：{mode}")
    if build_kind == "debug":
        print("  ⚠️  本产物禁止对外发布（含测试白名单机制）")
        print("      正式发布请显式：python scripts/build_sidecar.py --onedir --release")
    print("=" * 72)

    if debug_wl:
        inject_test_whitelist()
    _v = _write_version_file(build_kind)   # 固化版本 + 构建类型
    print(f"[版本] 本次构建版本 = {_v} ({build_kind})")
    try:
        # ════════════════════════════════════════════════════════════════
        # 2026-09-20：三份 sidecar **并行**构建（用户要求「打包这么慢，有并发吗」）。
        #
        # 为什么可并行：三者互不依赖；PyInstaller 的 --workpath/--specpath
        #   （各自 backend/build/<name>/）与 --name 完全独立，产物不同名
        #   → 无共享可写状态。
        # 前提：inject_test_whitelist() / _write_version_file() 均在此之前
        #   串行完成（它们改共享源码，不可并行）。
        # 实测收益：串行 ~9 分钟 → 并行约等于**最慢的那一个**（~3.5 分钟）。
        # 回退开关：加 --no-parallel 即恢复串行（排查构建期诡异问题时用）。
        # ════════════════════════════════════════════════════════════════
        _targets = [
            ("main.py", "dyautodm-backend"),
            ("daemon/browser_daemon.py", "dyautodm-browser-daemon"),
            ("daemon/recv_daemon.py", "dyautodm-recv-daemon"),
        ]
        _parallel = "--no-parallel" not in sys.argv and len(_targets) > 1
        if _parallel:
            import concurrent.futures as _cf
            print(f"\n[并行] 同时构建 {len(_targets)} 份 sidecar …")
            _errs = []
            with _cf.ThreadPoolExecutor(max_workers=len(_targets)) as _ex:
                _futs = {_ex.submit(build_one, e, n, mode): n for e, n in _targets}
                for _f in _cf.as_completed(_futs):
                    _n = _futs[_f]
                    try:
                        _f.result()
                        print(f"  ✅ {_n} 完成")
                    except Exception as _e:  # noqa: BLE001
                        _errs.append((_n, _e))
                        print(f"  ❌ {_n} 失败: {_e}")
            if _errs:
                raise RuntimeError(
                    "并行构建存在失败项: " + "; ".join(f"{n}: {e}" for n, e in _errs))
        else:
            for _e_, _n_ in _targets:
                build_one(_e_, _n_, mode=mode)
        # 性能主线（2026-09-14）：三份 _internal 内容实测完全相同
        # → 收敛为一份共享目录，三个 exe 平铺（命中既有解析候选 2，零代码改动）。
        # 不带 --no-dedupe 时默认执行。
        if mode == "onedir" and "--no-dedupe" not in sys.argv:
            print("\n[性能] 合并三份重复的 _internal …")
            info = _dedupe_internal()
            if info.get("moved"):
                print(f"  共享依赖 → {info['moved']}")
                print(f"  已移除重复目录: {info['removed']}")
                print(f"  省下体积: {info['saved_mb']} MB")
        # ════════════════════════════════════════════════════════════════
        # 2026-09-20 事故后加固：打包产物**机械自检**（不再靠人记住）。
        #
        # 背景：Camoufox 接入时连续 6 个「只在打包态暴露、源码态全绿」的
        # 资源缺失被逐个试错发现，每次重打 15 分钟 → 白等 1 小时。
        # 判据来源：迭代止损律 —— 同类问题第 2 次就该全链扫清，而非挤牙膏。
        #
        # 本自检对「源码态可 import 的每个包」逐个比对非 .py 资源是否已入产物，
        # 缺失即打印清单并以非零状态提示（不中断构建，避免掩盖其他产物）。
        # ════════════════════════════════════════════════════════════════
        try:
            _diag = ROOT / "scripts" / "diag" / "check_packed_resources.py"
            if _diag.is_file():
                import subprocess as _sp
                print("\n[自检] 校验打包产物运行时资源 …")
                _r = _sp.run([sys.executable, str(_diag), str(BINARIES)],
                             capture_output=True, text=True)
                _out = (_r.stdout or "") + (_r.stderr or "")
                # 只打印结论段，避免刷屏
                for _ln in _out.splitlines():
                    if _ln.strip():
                        print("  " + _ln.strip())
                if _r.returncode != 0:
                    print("\n[自检] ⚠️ 存在缺失项（见上）。修复：把缺失包名加入本脚本的 "
                          "--collect-all 列表后重打。")
        except Exception as _e:  # noqa: BLE001
            print(f"[自检] 跳过（{_e}）")

        print(f"\n全部打包完成（mode={mode}），产物位于:", BINARIES)
        if debug_wl:
            print("[警告] 本次为**调试版**构建（含测试白名单限制），"
                  "禁止对外发布！")
    finally:
        if debug_wl:
            restore_whitelist()


if __name__ == "__main__":
    main()

