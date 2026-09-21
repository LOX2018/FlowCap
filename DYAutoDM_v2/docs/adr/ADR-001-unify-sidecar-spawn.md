# ADR-001 · 统一 sidecar 启动入口（`_spawn_sidecar` 二义 → 单一实现）

| 项 | 值 |
|---|---|
| **状态** | **Proposed（待用户批准）** —— 未实施 |
| **日期** | 2026-09-21 |
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

## 2. 决策（待批）

**采用方案 A：`daemon_launcher._spawn_sidecar` 提为唯一实现，`main.py` 改为 import 调用。**

理由：
1. `daemon_launcher` 语义上就是「守护启动器」，职责正确（Separation of Concerns）。
2. `main.py` 是应用入口，不应承载进程创建细节。

**必须同时做的参数化**（否则会丢行为）：

```python
def _spawn_sidecar(binary, args, *, own_process_group: bool = True) -> subprocess.Popen:
    """own_process_group：True=独立进程组（backend 退出后守护存活）；
    False=同组（随 backend 一起终止）。"""
```

| 调用场景 | `own_process_group` | 依据 |
|---|---|---|
| 正常服务启动（守护应长驻） | `True` | B 的现行语义 |
| 需要随 backend 同生共死（若有） | `False` | A 的现行语义 |

> ⚠️ **必须先回答**：**哪些场景需要守护随 backend 一起死？**
> 若答案是「没有」，则统一为 `True`，并删除 `daemon_launcher` 的 `CREATE_NO_WINDOW` 分支改由参数控制。
> **此问题未答清前不得实施**——它决定「backend 退出后守护该不该活」。

### P0-2 一并裁决（二选一，必须显式声明）

- [ ] **选项 1**：Python `daemon_registry` 为唯一权威，Rust 侧删除管理逻辑（或在 ADR 声明为死面）。
- [ ] **选项 2**：Rust `SidecarManager` 为唯一权威，Python 侧降为「仅登记 pid 用于日志」。

---

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
