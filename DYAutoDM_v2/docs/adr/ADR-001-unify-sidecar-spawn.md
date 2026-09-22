# ADR-001 · 统一 sidecar 启动入口（`_spawn_sidecar` 二义 → 单一实现）

| 项 | 值 |
|---|---|
| **状态** | **Accepted（决策已定）** |
| **日期** | 2026-09-21 提案 / 2026-09-22 决策补全 |
| **决策者** | 用户（LOX） |
| **来源** | 《架构审计报告》P0-1 / P0-2；本轮实测复核 |
| **影响面** | 守护进程启动路径（BCC / recv_daemon / backend 全部 sidecar） |

---

## 1. 背景（实测证据）

本项目存在**两个**同名函数 `_spawn_sidecar`，行为**不一致**：

| # | 位置 | 进程创建语义 | 环境隔离 | pid 登记 |
|---|---|---|---|---|
| A | `backend/auto_dm/daemon_launcher.py:81` | `CREATE_NO_WINDOW`（不弹窗，**同进程组**） | ✅ DY_* 白名单 | ✅ `_dreg.register` |
| B | `backend/main.py:144` | Windows `CREATE_NEW_PROCESS_GROUP` / POSIX `start_new_session`（**独立进程组**） | ✅ DY_* 白名单 | ✅ `_dreg.register` |

**行为差异的后果**：
- 走 A 启动的守护 **与 backend 同进程组** → backend 被 Ctrl+C / 信号组终止时，守护**一并被杀**（或反之，无法分别控制）。
- 走 B 启动的守护 **独立进程组** → backend 退出后守护**仍存活**。
- 同一套守护，**由谁拉起决定了它的生命周期语义**——这正是「无窗口 vs 独立进程组」这类差异不会在日志里暴露、却会造成"有时守护被带走、有时变孤儿"的**不可复现故障**。

**调用点**：`daemon_launcher.py:179`（recv）、`main.py:314`（BCC）、`main.py:336`（recv）—— 即**同一个 recv 守护存在两条启动路径**。

### 关联问题 P0-2：生命周期双主体

| 主体 | 位置 | 职责 |
|---|---|---|
| Python `daemon_registry` | `backend` | 登记 pid、退出时清扫 |
| Rust `SidecarManager` | `src-tauri/src/lib.rs` | Tauri 侧管理 |

两方均可管生命周期，**边界未定义**（未见 ADR 声明谁是权威）。

---

## 2. 关键事实（2026-09-22 实测，回答提案期的开放问题）

**Q1：哪些场景需要守护随 backend 一起终止？**

**A：没有。** 实测证据（`backend/daemon_registry.py` + `backend/main.py`）：

| 机制 | 位置 | 作用 |
|---|---|---|
| pid 台账 | `daemon_registry.register/unregister` | 所有 spawn 路径**共用同一份** |
| 退出清扫 | `main.py:228 _kill_spawned_daemons()` → `_dreg.kill_all(reason="shutdown")` | 按**登记 pid**逐个 `taskkill /F /T` |
| 触发点 | `main.py:578`（lifespan shutdown）+ `atexit`（`main.py:12`） | 双保险 |

⇒ 生命周期清扫是**显式、进程级**完成的（且只杀自己登记过的 pid，不误伤用户手起的实例），
**不依赖 OS 进程组级联**。故 `CREATE_NEW_PROCESS_GROUP` 在本项目**无消费方** ——
§1「行为差异的后果」里那段推测（「同组→被带走 / 独立组→变孤儿」）**被实测否证**。

**Q2：`CREATE_NO_WINDOW` 缺失是否是真问题？**

**A：是，且与历史抱怨吻合。** `main.py` 的实现**没有** `CREATE_NO_WINDOW`；
在 Tauri 宿主（无 console）下，PyInstaller console 子进程可能**新建 console 窗口** ——
这是用户反复抱怨的「**窗口快闪**」的可疑机理之一（另一处已确认的机理是「停止态未建模」，
见 dev-guards §〇·丁 规则 3）。

---

## 3. 决策（Accepted）

**采用方案 A：`daemon_launcher._spawn_sidecar` 提为唯一实现，`main.py` 改为 import 调用。**

理由：
1. `daemon_launcher` 语义上就是「守护启动器」，职责正确（Separation of Concerns）。
2. `main.py` 是应用入口，不应承载进程创建细节。
3. Q1 证明两处语义差异**无消费方**，收敛**预期零功能影响**，同时补上缺失的 `CREATE_NO_WINDOW`。

**参数化形态**（默认值 = A 的现行语义 + 补 `no_window`）：

```python
def _spawn_sidecar(binary: str, args: list, *,
                   no_window: bool = True,
                   own_process_group: bool = False) -> subprocess.Popen:
    """唯一 sidecar 启动入口。

    no_window=True（默认）：CREATE_NO_WINDOW，避免 Tauri 宿主下弹 console 窗口。
    own_process_group：默认 False —— 生命周期清扫已由 daemon_registry 显式负责
        （见 Q1），无需 OS 级隔离；保留参数仅为将来若确需「守护独立于 backend 存活」。
    """
```

### P0-2 一并裁决

✅ **选项 1：Python `daemon_registry` 为唯一权威。**
Rust `SidecarManager` 不管理 backend 自己 spawn 的守护（它只管 Tauri 直接 spawn 的进程）；
若 `lib.rs` 中存在对 daemon 的管理逻辑，**声明为死面**（不参与生命周期决策）。

## 3. 风险评估

| 风险 | 等级 | 说明 |
|---|---|---|
| 改错进程组语义 → 守护变孤儿或被误杀 | **高** | 表现为端口占用 / 守护静默消失，**只能实机验证** |
| 影响启动链 | **高** | 启动失败 = 全系统不可用 |
| 静默回归 | 中 | 需 `process` 级观测（pid、端口、创建标志） |

**为什么本次不实施**：该改动**必须实机验证**（启 backend → 观察守护 pid/端口/进程组 → 杀 backend → 观察守护是否存活），
属「有用户在场 + 可回滚 + 有观测」的场景。**静默实施违反实机验证铁律。**

---

## 4. 验证步骤（实施时按序执行）

```bash
# 1. 记录基线：现行两条路径分别拉起守护后的进程组归属
PY314 -c "import psutil,os;print([(p.pid,p.name(),p.ppid()) for p in psutil.process_iter()])" | grep -i daemon
# 2. 实施收敛
# 3. 实机：启 backend → 确认 3 个守护（backend/browser/recv）pid 正常、端口可连
# 4. 实机：终止 backend → 按 own_process_group 期望确认守护存活/退出
# 5. 回归：py314 -m unittest discover -s backend
# 6. 判据：grep -c "def _spawn_sidecar" backend → 1
```

---

## 5. 后果

- **实施后**：`grep -c "def _spawn_sidecar" backend` = 1（审计第 1 项判据达成）；进程生命周期语义**显式可配**而非"看谁拉起"。
- **不实施**：保留一个**不可复现**的生命周期故障源；P0-2 双主体问题继续悬空。
