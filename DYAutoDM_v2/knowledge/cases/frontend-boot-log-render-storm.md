# 案例：诊断日志「渲染风暴」+ 只 append 不轮转 —— 31 MB / 28 万行噪声

- **日期**：2026-09-28
- **发现方式**：**部署后巡检**（用户未报障）—— 上轮部署 v0.45.81 时清点运行日志，
  发现 `logs/frontend_boot.log` 达 **32,116,532 B / 281,595 行**
- **版本**：v0.45.82
- **门禁**：`backend/test_ui_boot_log_no_render_storm.py`（4 项，含 1 负控）

---

## 1. 现象

`C:\temp\dyautodm_design\logs\frontend_boot.log` 体积 31 MB、28 万行，内容几乎全是
同一行的重复：

```
[render] memberName=LOX2018 memberChecked=false prealigned=false ready=true overviewEverOk=true
[overview.OK] {"running":false,...}
```

其中 `[render]` 一行重复 **217,254 次**。日志目录总计 67 MB。

## 2. 排查链（Execution-Chain Traceability）

```
写日志频率异常（28 万行）
   ↓ 定位写入者：frontend_boot.log 只有 write_boot_log 会写
   ↓ grep 调用点 → App.tsx:119（[render] 诊断）、App.tsx:251/261（[overview.OK/ERROR]）
   ↓ 读 App.tsx:114-125
   ↓   useEffect(() => {
   ↓     ...invoke("write_boot_log", { text: `[render] ...` })
   ↓   });                     ← ★ 断点：无依赖数组
   ↓ React 语义：无依赖数组的 useEffect 在**每次渲染后**都执行
   ↓ ⇒ 每渲染 1 次 = 写 1 行
   ↓ 再读写入端 rust lib.rs::write_boot_log → OpenOptions.append(true)
   ↓ 只 append、**从不轮转** ⇒ 累积无上限
   ⇒ 两个独立缺陷叠加：上游写得太多 × 下游没有上限
```

**为什么噪声会"淹没真实日志"**：该文件是排查启动问题的第一现场；28 万行重复
让「哪一次启动、哪个门失败了」几乎不可读。

## 3. 根因（两层，缺一不可）

1. **调用点（上游）**：诊断 effect **漏了依赖数组**。诊断的语义本是「记录门状态
   **变迁**」，却实现成「记录每一次渲染」——意图与实现不符。
2. **写入端（下游）**：`write_boot_log` 只 `append`，没有任何体积上限或轮转 ⇒
   任何上游的高频写法都会无界累积。（后端 loguru 早已配 `rotation="20 MB"`，
   前端这条通道**游离在该惯例之外**。）

## 4. 修复

| 层 | 文件 | 改动 |
|---|---|---|
| 上游 | `frontend/src/App.tsx` | 诊断 effect 补依赖数组 `[memberName, memberChecked, prealigned, ready, overviewEverOk]` ⇒ 只在门状态**变迁**时写 |
| 下游 | `src-tauri/src/lib.rs` | `write_boot_log` 补**单文件轮转**：写入前若 ≥10 MB，先轮为 `frontend_boot.log.1`（覆盖旧备份）⇒ 占用有界（≤2×阈值），与后端 `rotation="20 MB"` 同一惯例 |

## 5. ⚠️ 修复过程中自己踩到的陷阱（**最有价值的一节**）

给 effect 加依赖数组时，**差点引入一个白屏级新缺陷**：

- **第一版**：在原位置直接补 `}, [memberName, ...])`。
  但**依赖数组在渲染期求值**，而 `memberName` 等 `useState` 声明在该 effect **下方**
  ⇒ const 的 **TDZ** ⇒ 抛 `Cannot access 'memberName' before initialization`（白屏）。
- **第二版**：把 effect 移到那 4 个 `useState` 之后。
- **第三版**：`tsc -b` 报 **TS2448** —— 还有第 5 个依赖 **`ready`**，
  它由更下方的 `useQuery({... isSuccess: ready })` 解构而来 ⇒ 仍在其上方。
  最终把 effect 移到**全部 5 个依赖声明之后**。
- **教训**：`npx tsc -b` 把它**机械抓出来了**（TS2448）。若只靠肉眼与"看起来对"，
  这一版就是**上线即白屏**。

## 6. 验证

| 判据 | 实测 |
|---|---|
| 门禁 C1（调用点带依赖数组） | PASS |
| 门禁 C2（effect 晚于**全部**依赖声明，含 `ready`） | PASS ← 专锁上面那个陷阱 |
| 门禁 C3（写入端有界：阈值 + 轮转） | PASS |
| 门禁 C4（负控：删掉依赖数组 ⇒ C1 判据不再命中） | PASS |
| `npx tsc -b` | exit 0，无 error（TS2448 已消除） |
| 运行期验证 | ⏳ 需构建部署后复测（本修复跨前端 + Rust 两侧） |

## 7. 可迁移判据（跨项目）

1. **`useEffect` 缺依赖数组 = 每次渲染都执行**：这对「副作用」是灾难（写文件、
   发请求、打日志）。凡以「记录变化」为目的的 effect，**必须**给依赖数组。
2. **日志写入端必须有界**：任何 append 型日志通道都要配体积上限或轮转。
   项目内已有惯例（loguru `rotation="20 MB"`）——**新通道要并入同一惯例**，
   否则它就是一个必然泄漏的洞。
3. **依赖数组在渲染期求值 ⇒ effect 必须晚于其全部依赖声明**：这是 React
   hooks 的硬规则，且**加上数组那一刻才会暴露**（不加数组时不求值，所以原代码
   一直"没事"）。把裸的 `useEffect(() => {...})` 改成带数组时，**必须同时检查
   声明顺序**。
4. **`tsc` 是这类陷阱的机械门禁**：TDZ 在 hooks 依赖数组上的表现会被 TS2448
   抓到，改前端 hooks 后跑 `tsc -b` 应作为强制步骤。
5. **巡检也是缺陷来源**：本案例用户未报障，是**部署后清点日志体积**发现的。
   把「日志体积/行数」纳入部署后巡检清单，可低成本发现这类静默泄漏。
