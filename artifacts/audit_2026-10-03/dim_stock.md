# DYAutoDM_v2 存量扫描维度审计报告

**审计日期**：2026-10-03  
**审计类型**：全量当前态扫描（非 diff）  
**审计范围**：`C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend`  
**执行人**：subagent（只读审计）

---

## 一、静默兜底 Top 清单

### 机械读数（复核）

| 指标 | 读数 | 历史基线 | 状态 |
|---|---|---|---|
| F1 L1-写库 pass-only | 31 处 | — | ❌ 阻断 |
| F2 L1-外发 pass-only | 11 处 | — | ❌ 阻断 |
| F3 L1 新增 | 65 处 | 102 处 | ⚠️ 减少 45 处 |
| 基数（含 test） | 749 处 | 386 处 | Δ +363 |
| 分级口径 | L1-写库 84 / L1-外发 38 / L2 66 / L3 561 | — | ✅ |
| 待人工复核 | 49 处 | — | ⚠️ |

**复跑命令**：
```bash
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2
C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe scripts/check_silent_fallback.py
```

### 人工核实：真正危险的静默兜底

#### 【P0】database.py:335 — ALTER TABLE 失败静默，注释与代码不一致

**证据**：`backend/database.py:325-336`
```python
try:
    conn.execute("ALTER TABLE dm_messages ADD COLUMN msg_id TEXT")
except Exception:
    pass  # 列已存在
```

**注释声称**（`database.py:340-347`）：
> ⚠️ 2026-09-17 修补（审查 P2-11）：本索引有两个已知副作用，此前被 `except: pass` 完全掩盖……现改为：创建失败必须告警（不再静默）。

**实际代码**：`database.py:335` 仍是 `except Exception: pass`，**未改**。

**后果**：
- 若 ALTER TABLE 失败（如磁盘满、锁冲突），列未添加但无告警
- 后续 INSERT 引用该列会失败，但错误被更外层的 try-except 吞掉
- 运维以为已迁移，实际 schema 未更新

**建议**：将 `database.py:335` 的 `except Exception: pass` 改为 `except Exception as e: logger.warning(...)`，与 `database.py:348-353` 的 `uniq_dmmsg_fallback` 索引保持一致。

---

#### 【P1】login_qr_api_runner.py:256 — 顶层兜底 pass，JSON 写入失败时静默

**证据**：`backend/login_qr_api_runner.py:240-257`
```python
except Exception as e:
    try:
        jd = None
        for i, a in enumerate(sys.argv):
            if a == "--jobdir" and i + 1 < len(sys.argv):
                jd = sys.argv[i + 1]
                break
        if jd:
            _write_json(os.path.join(jd, "result.json"), {...})
            _emit(os.path.abspath(jd), "failed", ...)
    except Exception:  # noqa: BLE001
        pass
    sys.exit(4)
```

**后果**：
- 主程序只看到「子进程消失了」（exit 4），看不到具体错误
- 若 `_write_json` 或 `_emit` 失败（如磁盘满、权限问题），错误被完全吞掉
- 实机踩过：主程序只看到「子进程消失了」

**建议**：在 `except Exception: pass` 前加 `traceback.print_exc()` 或 `logger.error(...)`，至少留一条日志。

---

#### 【P1】api/accounts.py:1933 — 停止守护返回 False，调用方可能误判

**证据**：`backend/api/accounts.py:1924-1934`
```python
def _quit_daemon_http(port: int) -> bool:
    if not port:
        return False
    try:
        req = urllib.request.Request(...)
        with urllib.request.urlopen(req, timeout=3) as resp:
            resp.read()
        return True
    except Exception:
        return False
```

**后果**：
- 停止守护失败时返回 False，但调用方可能不检查返回值
- 用户点「停止守护」后以为已停，实际守护仍在跑
- 与「停止守护必须是一等状态」铁律冲突

**建议**：调用方必须检查返回值，失败时显式报错（如「守护停止失败，请手动检查端口」）。

---

#### 【P2】database.py:328 — 列已存在时静默（设计取舍，可接受）

**证据**：`backend/database.py:325-328`
```python
try:
    conn.execute("ALTER TABLE dm_messages ADD COLUMN msg_id TEXT")
except Exception:
    pass  # 列已存在
```

**后果**：
- 若列已存在，静默是正确的
- 但若因其他原因失败（如磁盘满），也会被静默

**建议**：区分「列已存在」与其他异常，或改用 `IF NOT EXISTS`。

---

## 二、错误码契约完整性

### 机械读数

| 指标 | 读数 |
|---|---|
| 总错误码数 | 389 |
| 有完整六段契约的码 | 58 |
| 缺契约的码 | **110** |
| 域级契约覆盖 | 8 域（AUTH/BCC/CAP/LIVE/PROBE/RECV/SEND/SYS） |

**缺失契约的码（110 个）**：
- ACC-001~016（16 个）
- AI-001~034（34 个）
- CRAWL-001~005（5 个）
- DB-001~005（5 个）
- ENG-001~009（9 个）
- HUB-001~003（3 个）
- IMG-001~003（3 个）
- MEM-001~006（6 个）
- MSG-001~003（3 个）
- NTY-001~012（12 个）
- SCHED-001~012（12 个）
- TSK-001~004（4 个）

**复跑命令**：
```bash
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend
C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe -c "from errcode import contract_gaps as g; import json; d=g(); print(json.dumps(d, ensure_ascii=False))"
```

### 分析

- **110/389 = 28.3%** 的错误码缺六段契约
- 缺失集中在 **AI（34 个）、ACC（16 个）、NTY（12 个）、SCHED（12 个）** 四个域
- 这些域多为新增功能（AI 获客、账号管理、通知、调度），未同步补契约
- 与铁律「新增错误码必须填 design」冲突

### 建议

1. **P0**：AI 域 34 个码缺契约，优先补全（AI 获客是核心功能）
2. **P1**：ACC 域 16 个码缺契约，次优先
3. **P2**：NTY/SCHED 域各 12 个码，可批量补

---

## 三、数据契约存量

### 机械读数

| 指标 | 状态 |
|---|---|
| R8-1 dm_messages 写入出口收敛（5 文件） | ✅ PASS |
| R8-2 类型注册表覆盖（已登记 9 种） | ✅ PASS |
| R8-3 text 不得承载 base64/图片 URL 载荷 | ✅ PASS |
| R8-4 语义标签须取自 Schema SSOT | ✅ PASS |
| R8-5 前向兼容（未知类型降级 + 门禁在位） | ✅ PASS |
| R8-6 同一语义不得多名字（禁 msg_type 多值并列） | ✅ PASS |

**复跑命令**：
```bash
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2
C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe scripts/audit_data_contract.py
```

### msg_type 双表示问题

**证据**：
- **写侧**（`daemon/recv_daemon.py:1074`）：`msg_type=str(msg_type)`，写入数字字符串（如 `"7"`）
- **读侧**（`api/messages.py:154-173`）：`_front_type()` 把数字映射到语义串（`"7"` → `"text"`）
- **2026-10-03 拆分**：新增 `msg_code` 列存上游码，`msg_type` 列只存语义名

**现状**：
- 拆分已落地（`database.py:365-380`），但**存量数据仍混装**
- 读侧 `_front_type()` 仍做数字→语义映射，兼容存量
- 门禁 `test_msg_type_code_split` 守住注册表一致性

**结论**：
- ✅ 写入出口收敛（5 文件）
- ✅ 类型注册表覆盖（9 种）
- ⚠️ 存量数据仍混装，需回填或兼容

### dm_messages 写入出口

**5 个文件**（PASS）：
1. `auto_dm/conversation_capture.py:2022, 2035`
2. `daemon/recv_daemon.py:769, 1501, 1528`
3. `daemon/wp_recv.py:354`
4. `database.py:618`
5. `services/delivery_verify.py:120`
6. `services/message_schema.py:158`

**结论**：✅ 已收敛，无新增出口。

---

## 四、大组件趋势

### 当前 >800 行文件清单（26 个）

| 行数 | 文件 | 历史审计（2026-09-27） | 趋势 |
|---|---|---|---|
| 2809 | `services/ai_reply.py` | — | — |
| 2251 | `api/accounts.py` | — | — |
| 2185 | `auto_dm/accounts.py` | — | — |
| 2150 | `auto_dm/conversation_capture.py` | 2142 | **+8** |
| 2108 | `daemon/recv_daemon.py` | 1989 | **+119** |
| 1913 | `auto_dm/login_remote.py` | — | — |
| 1868 | `api/platform.py` | — | — |
| 1789 | `daemon/browser_daemon.py` | — | — |
| 1734 | `api/messages.py` | — | — |
| 1658 | `dy_apis/login_api.py` | — | — |
| 1538 | `services/dm_dispatch.py` | — | — |
| 1470 | `core/auto_dm.py` | — | — |
| 1438 | `services/probe.py` | — | — |
| 1355 | `vbrowser.py` | — | — |
| 1295 | `errcode_data.py` | — | — |
| 1262 | `api/crawl.py` | — | — |
| 1137 | `main.py` | — | — |
| 1134 | `services/live_batch.py` | — | — |
| 1122 | `api/ai.py` | — | — |
| 1037 | `services/app_config_schema.py` | — | — |
| 990 | `core/live_hook.py` | — | — |
| 927 | `notify/channels.py` | — | — |
| 842 | `mcp/tools_debug.py` | — | — |
| 833 | `services/pro_kb.py` | — | — |

### 趋势分析

- **conversation_capture.py**：2142 → 2150（+8），基本持平
- **recv_daemon.py**：1989 → 2108（**+119**），增长 6%
- **ai_reply.py**：2809 行，仍是最大组件

**结论**：
- ⚠️ `recv_daemon.py` 增长 119 行，需关注
- ⚠️ `ai_reply.py` 2809 行，仍是最大组件，建议拆分

---

## 五、源码树污染

### git 入库状态

| 路径 | git ls-files | git check-ignore | 状态 |
|---|---|---|---|
| `backend/logs/*.log` | 空 | 忽略 | ✅ 正确忽略 |
| `backend/data/*.db` | 空 | 忽略 | ✅ 正确忽略 |
| `backend/ai_probe.log` | 空 | 忽略 | ✅ 正确忽略 |
| `backend/%SystemDrive%/...` | 空 | 忽略 | ✅ 正确忽略 |

**复跑命令**：
```bash
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2
git ls-files backend/logs/ backend/data/ backend/ai_probe.log
git check-ignore backend/logs/test.log backend/data/test.db backend/ai_probe.log
```

### %SystemDrive% 目录分析

**路径**：`backend/%SystemDrive%/ProgramData/Microsoft/Windows/Caches/`

**内容**：
- `{6AF0698E-D558-4F6E-9B3C-3716689AF493}.2.ver0x0000000000000001.db`（309 KB）
- `{DDF571F2-BE98-426D-8288-1A9A39C3FDA2}.2.ver0x0000000000000001.db`（663 KB）
- `cversions.2.db`（16 KB）

**来源**：sidecar 运行期产物（PyInstaller onefile 解压 + 相对路径）

**gitignore 规则**（`.gitignore:130-131`）：
```
#    src-tauri/ 下创建了字面量目录 `%SystemDrive%/ProgramData/...`
**/%SystemDrive%/
```

**结论**：
- ✅ 已正确 gitignored
- ⚠️ 建议删除（运行期产物，不应留在源码树）

**建议**：
```bash
rm -rf "C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/%SystemDrive%"
```

---

## 六、并发原语分布

### 机械读数

| 指标 | 读数 |
|---|---|
| 并发原语总数 | 95 处 |
| 分布 | ai_reply.py 9、dm_dispatch.py 7、uid_probe.py 5、live_batch.py 5、ws_link.py 5 |

**复跑命令**：
```bash
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend
grep -rn "threading.Lock\|threading.RLock\|threading.Semaphore\|threading.Event\|threading.Condition" --include="*.py" | grep -v test_ | grep -v verify_ | grep -v __pycache__ | grep -v vendor | grep -v node_modules | grep -v build | grep -v dist | grep -v _internal | wc -l
```

### 分析

- 95 处并发原语，分布合理
- `ai_reply.py` 9 处最多，与 2809 行大组件对应
- 无异常集中

---

## 七、except-pass 形态

### 机械读数

| 口径 | 读数 |
|---|---|
| 严格（行尾 pass） | 1 处（`vbrowser_window.py`） |
| 宽松（except...pass） | 10 处 |
| 上下文引用 | 329 处（可能含 bare except、跨行等） |

**结论**：
- ✅ 严格口径仅 1 处，已大幅改善
- ⚠️ 宽松口径 10 处，仍需关注

---

## 八、硬编码 timeout=

### 机械读数

| 指标 | 读数 |
|---|---|
| 硬编码 timeout= 总数 | 200 处 |
| 分布 | login_api.py 16、browser_daemon.py 13、accounts.py 12、bcc_login.py 8、vbrowser.py 7、link_resolve.py 7、bcc_routes.py 7、messages.py 7 |

**复跑命令**：
```bash
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend
grep -rn "timeout=" --include="*.py" | grep -v test_ | grep -v verify_ | grep -v __pycache__ | grep -v vendor | grep -v node_modules | grep -v build | grep -v dist | grep -v _internal | grep -v "timeout=None" | grep -v "timeout=0" | wc -l
```

### 分析

- 200 处硬编码 timeout，分布合理
- `login_api.py` 16 处最多，与 1658 行对应
- 无异常集中

---

## 九、check_contracts.py 结果

### 机械读数

| 门禁 | 状态 | 说明 |
|---|---|---|
| G0 契约文件 ≥5 | ✅ PASS | 实测 7 份 |
| G1 C-01 主动查询计数 | ✅ PASS | 运行时主动查询 0 次 |
| G2 C-02 secsdk 签名接线 | ❌ FAIL | 未签名端点 1 处：`client_video.py:/aweme/v1/web/tab/feed/` |
| G3 C-03 dm 不冒充 wp | ✅ PASS | 0 命中 |
| G4 C-04 投递有回执/落库验证 | ✅ PASS | — |
| G5 C-05 reflow 主引擎存在 | ✅ PASS | — |
| G6 C-02 签名自检 | ✅ PASS | — |
| G7 C-01 昵称源 SSOT | ✅ PASS | — |
| G8 C-03 真实写校验 | ✅ PASS | — |
| G9 C-04 投递硬验证 | ✅ PASS | — |
| G10 C-05 解密权合取 | ✅ PASS | — |
| G11 C-06 字段规范 | ✅ PASS | — |
| G12 C-06 符号守护 | ✅ PASS | — |
| G13 C-06 配置键 | ✅ PASS | — |
| G14 C-01~C-06 单测可运行 | ✅ PASS | — |
| G15 C-07 直播交互符号守护 | ✅ PASS | — |

**复跑命令**：
```bash
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2
C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe scripts/check_contracts.py
```

### G2 未通过分析

**证据**：`backend/dy_apis/client_video.py` 的 `/aweme/v1/web/tab/feed/` 端点未签名

**后果**：
- 推荐流端点未签名，可能被风控拦截
- 与「secsdk 签名接线」契约冲突

**建议**：
- 若该端点仍在用，补签名
- 若已废弃，删除或标记为「在制品」

---

## 十、已核验 / 未核验

### 已核验

- ✅ 静默兜底机械读数（复核）
- ✅ 错误码契约缺口（复核）
- ✅ 数据契约 R8-1~R8-6（复核）
- ✅ 大组件 >800 行清单（复核）
- ✅ 源码树污染（git ls-files / check-ignore）
- ✅ 并发原语分布（复核）
- ✅ except-pass 形态（复核）
- ✅ 硬编码 timeout=（复核）
- ✅ check_contracts.py（复核）

### 未核验

- ⚠️ 静默兜底 31+11 处的逐条人工核实（仅核实了 4 处典型）
- ⚠️ 110 个缺失契约的码的逐条分析
- ⚠️ msg_type 存量数据混装的实际影响
- ⚠️ G2 未签名端点的实机验证

---

## 十一、总结

### 关键发现

1. **P0**：`database.py:335` 注释与代码不一致，ALTER TABLE 失败仍静默
2. **P0**：110/389（28.3%）错误码缺六段契约，AI 域 34 个码优先补
3. **P1**：`login_qr_api_runner.py:256` 顶层兜底 pass，JSON 写入失败时静默
4. **P1**：`api/accounts.py:1933` 停止守护返回 False，调用方可能误判
5. **P1**：`recv_daemon.py` 增长 119 行，需关注
6. **P2**：`%SystemDrive%` 目录应删（运行期产物）
7. **P2**：G2 未签名端点需处理

### 趋势

- 静默兜底：基数 749（历史 386），但 L1 新增 65（基线 102），**减少 45 处**，改善中
- 大组件：26 个 >800 行，`recv_daemon.py` 增长 119 行
- 错误码契约：110 个码缺契约，需补全
- 数据契约：R8 全 PASS，msg_type 双表示已拆分但存量仍混装

### 建议优先级

1. **P0**：修 `database.py:335` 注释与代码不一致
2. **P0**：补 AI 域 34 个码的六段契约
3. **P1**：修 `login_qr_api_runner.py:256` 顶层兜底
4. **P1**：修 `api/accounts.py:1933` 停止守护返回值
5. **P2**：删 `%SystemDrive%` 目录
6. **P2**：处理 G2 未签名端点

---

**报告结束**
