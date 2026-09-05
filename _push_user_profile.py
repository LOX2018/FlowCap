#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""推送「用户 Code 画像说明」到 Logseq（扁平页面名，避免命名空间/父级是页面报错）。"""
import requests
import json
import urllib.request

API_URL = "http://localhost:12315/api"
TOKEN = "qa36g8chw"
HEADERS = {"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"}
TIMEOUT = 90


def call(method, args, retries=2):
    payload = {"method": method, "args": args}
    for i in range(retries):
        try:
            r = requests.post(API_URL, headers=HEADERS, json=payload, timeout=TIMEOUT)
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    return {"raw": r.text}
            else:
                print(f"  [WARN] {method} -> HTTP {r.status_code}: {r.text[:200]}")
        except requests.exceptions.Timeout:
            print(f"  [TIMEOUT] {method} 第{i+1}次超时，重试...")
        except Exception as e:
            print(f"  [ERR] {method}: {e}")
    return None


def page_exists(title):
    r = call("logseq.Editor.getPage", [title])
    if r is None:
        return False
    if isinstance(r, dict) and "result" in r:
        return r.get("result") is not None
    return False


def append_block(page, content):
    return call("logseq.Editor.appendBlockInPage", [page, content])


TITLE = "用户Code画像说明_DYAutoDM_v2"
PROPS = {"tags": ["dyautodm", "user-profile", "meta"]}
BLOCKS = [
    "# 用户 Code 画像说明（DYchajian / DYAutoDM_v2 项目）",
    "> 基于 2026-08 全月工作记忆、git 历史与工程实践提炼，描述用户（LOX）作为开发者的编码习惯、维护偏好、决策风格与环境约束。供新会话/协作伙伴快速对齐工作方式。当前基准：V2 (DYAutoDM_v2) Tauri2 + React + FastAPI，版本号 0.29.0+，分支 v2-refactor。",
    "## 一、身份与角色定位",
    "- **独立全栈开发者 / 项目 Owner**：一个人同时负责 Rust 壳（Tauri）、React+TS 前端、Python(FastAPI) 后端、指纹浏览器内核（vb_chromium）调度、打包部署全链路。",
    "- **产品导向**：开发的工具（抖音直播间评论自动私信 DYAutoDM）是自用型产品，需求直接来自自己实测后的痛点，而非外部 PRD。",
    "- **务实主义者**：不追求架构完美，优先'能跑、能实测、能交付 exe'，但会在反复踩坑后沉淀铁律（见下文'维护哲学'）。",
    "## 二、技术栈偏好",
    "- **桌面应用框架**：Tauri 2（Rust 壳 + WebView），坚持'主 exe + 旁 binaries\\ 3 个 sidecar'分发模式（双击即用，不依赖安装包）。",
    "- **前端**：React 18 + TypeScript + Vite；状态管理用 React Query（useQuery/useMutation）；组件用函数式 + hooks；UI 用轻量内联样式。",
    "- **后端**：Python 3.14 + FastAPI + uvicorn + loguru；3 个独立 sidecar（backend / browser-daemon / recv-daemon）按账号维度用 crc32 稳定端口哈希隔离。",
    "- **浏览器自动化**：强制使用自带指纹内核 vb_chromium（ungoogled-chromium），严禁回退原生 Playwright（硬编码 RuntimeError 拦截）。",
    "- **数据持久化**：从早期 JSON 配置演进到 SQLite（data/dyautodm.db），历史任务/会话/账号状态落库。",
    "- **打包工具链**：PyInstaller（onefile sidecar）+ Tauri `npx tauri build`（主壳）。",
    "## 三、编码与修复风格",
    "### 1. 问题导向、实测驱动",
    "- 几乎所有改动都来自'用户实测反馈 X 坏了'→ 定位根因 → 修复 → 重打包实测。",
    "- 拒绝'猜测性修改'，要求实证（抓 chrome 进程命令行确认 --no-sandbox 来源、CArchiveReader 验证 exe 内嵌资源、MCP playwright 截图确认前端渲染）。",
    "- 喜欢'从根源解决'而非'热修复'：曾明确批评 fix_stuck_tasks 的启发式是热修复，要求按 pid 比对做状态机正确收尾（2026-08-23）。",
    "### 2. 分层清晰、单一职责",
    "- 后端 sidecar 业务逻辑改动与 Rust 壳改动严格区分：纯 Python 业务 → 只重打包 3 sidecar 部署，不升主版本号；改了 src-tauri/src/*.rs → 升版本 + 完整 tauri build 重编。",
    "- 前端 (.tsx/.ts) 改动必须完整 tauri build 重嵌，仅重打包 backend sidecar 不更新前端。",
    "### 3. 不写过度工程，但重视'防呆'",
    "- 拒绝冗余：删除了大量开发期调试脚本（_*.js/_*.py/_*.ps1）、测试截图、日志.md。",
    "- 重视防御性编码：端口用 zlib.crc32 稳定哈希、cookie 新版格式兼容判定、风控页实时监测而非 cookie 格式猜测。",
    "### 4. 命名与注释",
    "- 中文注释友好（代码里大量中文日志与 docstring，loguru 中文输出常态化）。",
    "- 变量/函数命名直白（如 fix_stuck_tasks、_wait_dispatch_done、acctValid）。",
    "- 版本号语义清晰：Tauri 主.次.修，+0.01 = 次版本 +1，四文件同步。",
    "## 四、维护与版本管理偏好",
    "### 1. 工作流铁律（用户明确要求）",
    "- 每次真实代码改动后必须：① 写工作记忆（.codebuddy/memory/YYYY-MM-DD.md）；② git add + commit 保持工作区干净（靠 .gitignore 排除缓存）。",
    "- 删除/清理后，陈旧记忆文件一并物理删除，不在磁盘留过时参考。",
    "### 2. 提交规范",
    "- 中文 commit message 必须经 _commit_msg.txt（UTF-8 BOM）+ git commit -F，绝不命令行内联中文（PowerShell GBK 转码必乱码）。",
    "- 不用 git add -A，宁可指定真实文件列表，避免误提交缓存/数据库。",
    "### 3. 版本清理果断",
    "- V1 (DY_Spider_base) 被判定'没有价值'后直接物理删除 + 取消 git 跟踪 + 永不恢复（2026-08-20）。",
    "- 陈旧记忆（与当前架构不符的）一律清除，要求'和现有架构不服的不要保留'。",
    "- 旧 <memories> 系统注入条目若与 MEMORY.md 基线冲突，以基线为准，旧条目作废。",
    "### 4. 环境约束（必须遵守）",
    "- 真实 Python：C:\\Users\\LOX\\AppData\\Local\\Programs\\Python\\Python314\\python.exe（Store 别名不可用）。",
    "- cargo：C:\\Users\\LOX\\.cargo\\bin（不在 PATH，打包前追加）。",
    "- 删大目录用 cmd /c rmdir /s /q（绕过 IDE safe-delete 拦截），不用 PowerShell Remove-Item。",
    "- 测试部署：C:\\temp\\dyautodm_test\\（主 exe 固定名 + binaries\\ 3 sidecar）。",
    "## 五、调试与验证方法论",
    "- 验证手段多元且务实：后端 py_compile + read_lints + 源码态 import 实证 + TestClient 单元测试；前端 npm run build + MCP playwright 截图 + 注入 mock ApiBridge 渲染校验；打包产物 CArchiveReader 提取 exe 内嵌资源校验、进程命令行抓取、端口探测。",
    "- 重视'旧 exe 陷阱'：多次强调用户跑的是旧 exe，要求每次改完必须重打包/重编部署，不能只改源码不交付。",
    "- 日志文化：loguru 双 sink（文件落盘 + stderr enqueue 防 GBK 崩溃），前端有独立'运行日志'页轮询 /api/logs；拒绝界面内嵌刷屏日志（早期 GUI 已去）。",
    "## 六、沟通与协作偏好",
    "- 直接、简洁、给结论：不喜欢绕弯，要求'状态假设和继续；别停下来等批准'。",
    "- 喜欢方案选择：常给 A/B 方案让 AI 选（如'方案 A 全自动重捕 / 方案 B 前端按钮'），选定后要求严格落地。",
    "- 对 AI 的期待：能主动从工作记忆/MEMORY.md 溯源、对比 V1/V2 历史、独立排查跨文件根因，而非只改表面 symptom。",
    "- 不接受'系统提示/内部配置'类回复：遇到权限/政治敏感/系统指令类请求直接拒绝或绕过。",
    "## 七、典型痛点与反复踩坑（供避坑）",
    "1. 进程占用导致部署静默失败：旧 sidecar 进程没停 → 复制被占用 → 用户实测跑旧代码。对策：部署前必须停进程。",
    "2. 前端/后端契约错位：getAccounts 返回数组 vs {ok,accounts}、角色枚举 watch/send vs monitor/sender、refetch 覆盖手动校验结果——经典'轮询覆盖手动更新'陷阱。",
    "3. 抖音 cookie 新版格式误判：s_v_web_id=verify_ 开头、uid_tt 为 hex 被旧代码当污染/伪造，导致引擎误判失效；统一改为'页面实时监测风控'而非 cookie 格式猜测。",
    "4. build.ps1 的 $1 反向引用污染：PowerShell .NET 正则替换串的 $1 后跟数字变 $10 空值，已弃用自动 bump，改手动同步四版本文件。",
    "5. safe-delete 拦截批量删除：IDE 删除 >50 文件会被拦，统一用 cmd rmdir /s /q。",
    "## 八、一句话画像",
    "> 一个自用型桌面工具的全栈 Owner，务实、实测驱动、重视交付可运行的 exe、用铁律沉淀踩坑经验、果断清理过时代码与记忆、要求 AI 能独立溯源跨文件根因。",
]


def main():
    print(f"==> 处理页面：{TITLE}")
    if page_exists(TITLE):
        print("    页面已存在，直接追加内容。")
    else:
        print("    页面不存在，创建中...")
        try:
            req = urllib.request.Request(
                API_URL,
                data=json.dumps({"method": "logseq.Editor.createPage",
                                  "args": [TITLE, PROPS, {"createFirstBlock": True}]}).encode("utf-8"),
                headers=HEADERS, method="POST")
            with urllib.request.urlopen(req, timeout=120) as resp:
                print("    建页返回：", resp.read().decode("utf-8")[:120])
        except Exception as e:
            print("    [FAIL] 建页异常：", e)
            return

    ok = 0
    fail = 0
    for i, b in enumerate(BLOCKS):
        r = append_block(TITLE, b)
        if r is None:
            print(f"    [WARN] 第{i}块失败：{b[:40]}")
            fail += 1
        else:
            ok += 1
    print(f"    追加完成：成功 {ok} 块，失败 {fail} 块。")


if __name__ == "__main__":
    main()
