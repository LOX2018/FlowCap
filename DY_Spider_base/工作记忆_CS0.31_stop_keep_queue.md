# 工作记忆 CS0.31：直播监听私信全不发 + 软停止后存量私信静默丢失

## 现象（用户实测日志 run_20260812_205627.log）
点「开启自动私信」监听直播间，捕获 21 条评论（日志有 `[调度] 已捕获…将在 Xs 后发送私信` 共 21 条），
但一条 `[进度] 已发送` / `[私信] 发送失败` 都没有。点停止后日志直接结束（21 条延迟私信全废）。
GUI 实时统计显示"待发送"（来自 records status=已捕获 + queue_size），但私信从未真正发出。

## 两个根因

### 根因A：用户跑的是旧 exe（CS0.29，锁 bug 版）
`_loop` 在第一次 `self._cv.wait()` 时抛 `RuntimeError: cannot wait on un-acquired lock` 线程崩溃，
队列永不消费 → 全部"待发送"永远发不出。该 bug 已在 CS0.30 修复（core.py `_loop` 整个主循环体纳入 `with self._lock`）。
但 dist/DYAutoDM/ 下**同时存在 CS0.29.exe 与 CS0.30.exe**，用户 20:56 那次极可能双击的是 CS0.29。
→ 已重打包 CS0.31（含锁修复）并要求用户只运行 CS0.31。

### 根因B：stop_keep_queue 与 _do_send 的 stopped 语义冲突（真实代码 bug，本次修复核心）
原 core.py：
- `stop_keep_queue()`（软停止，run.py stop() 调用）把 `self.stopped = True`；
- `_do_send` 第一行 `if self.stopped: return`（硬停止语义）；
- `_loop` 在软停止后（stopped=True 且队列非空）仍会把到期项从队列移除（line 219）并调 `_do_send`，
  但 `_do_send` 看到 `stopped=True` 直接 return —— **这些存量私信被静默丢弃（既没发也不在队列，且 return 在打日志之前，连"发送失败"都没有）**。
设计矛盾：`stop_keep_queue` 意图"保留队列发完"，却置了会拦截发送的 `stopped=True`。

## 修复（auto_dm/core.py）
1) __init__ 新增 `self.hard_stopped`（硬停止才拦截 _do_send）、`self.no_new`（软停止：只挡 submit 接收新目标）。
2) `stop()`（硬停止）：置 `self.hard_stopped = True` + `self.stopped = True` + 清空队列（行为不变，force_stop_all 用）。
3) `stop_keep_queue()`（软停止）：**只置 `self.no_new = True`，不置 stopped/hard_stopped** —— 已入队延迟私信照常发完。
4) `submit`：拦截条件 `if self.stopped` → `if self.hard_stopped`（硬停）/`elif self.no_new`（软停忽略新目标，存量照发）。
5) `_loop` 退出条件：`if self.stopped and not self._queue` → `if (self.hard_stopped or self.no_new) and not self._queue`（软停止发空后干净退出线程，不空则继续发）。
6) `_do_send`：`if self.stopped: return` → `if self.hard_stopped: return` + 加 warning 日志（被硬停止丢弃时也留痕）。软停止(no_new)不再静默丢存量。

## 联动修复（auto_dm/run.py）
`is_running()`：`not self.dispatch.stopped` 语义已废 → 改为 `not self.dispatch.hard_stopped and self.dispatch.queue_size() > 0`（软停止且队列空后正确返回 False）。

## 验证
core.py/run.py lint 0 错误；本机真实 python(Python314) py_compile 通过；build_exe.py --no-clean 自动 bump CS0.30→CS0.31，
日志确认 `Building because auto_dm\core.py changed` + `auto_dm\run.py changed`（PYZ 重编译，修复打入 DYAutoDM_CS0.31.exe）。
dist/DYAutoDM/ 同时存在 CS0.29/CS0.30/CS0.31 三个 exe，须告知用户只双击 CS0.31。

## 用户实测要点（下次）
双击 DYAutoDM_CS0.31.exe → 监听直播间 → logs/run_*.log 应依次出现：
1) `[调度] 已捕获…将在 40~65s 后发送私信`
2) 约 40~65s 后 `[进度] 已发送 1/N（目标「昵称」）` 或 `[私信] 发送失败「昵称」: 原因`
若停止后存量仍发不完，说明在主进程退出前 _loop 被 daemon 线程机制 kill（进程退出时机问题），需另行处理。
缓存的 CS0.29 建议删除避免误点。
