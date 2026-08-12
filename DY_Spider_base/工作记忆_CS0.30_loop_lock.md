# 工作记忆 2026-08-12 #8 — 修复 _loop 线程 RuntimeError: cannot wait on un-acquired lock

文件：DY_Spider_base/auto_dm/core.py。

现象（用户实测日志）：
```
Exception in thread Thread-720 (_loop):
  File "auto_dm\core.py", line 212, in _loop
  File "threading.py", line 361, in wait
RuntimeError: cannot wait on un-acquired lock
```
崩溃在 `self._cv.wait(wait)`（原 line 212）。

根因：
`self._cv = threading.Condition(self._lock)`（self._lock 是 RLock）。`Condition.wait()` 的契约是【调用线程必须已持有该锁】，wait 内部自动释放锁→挂起→被 notify 唤醒后重新获取锁。
原 `_loop` 结构错误：
```python
with self._lock:
    if self.stopped and not self._queue:
        break
# ← with 块到此结束，锁已释放
if self.on_idle: ...
    if self.paused:
        self._cv.wait(0.5)        # 锁不在手上 → 崩
    if self.reached_limit:
        self._cv.wait(0.5)        # 同上
    ready = ...
    if ready is None:
        self._cv.wait(wait)       # 同上
```
所有 `self._cv.wait()` 都跑在 `with self._lock:` 块【之外】（锁已释放）。之前没每次都崩是因为多数轮次走到 `ready` 命中或队列非空的其他分支，没触发 wait；本次（Thread-720）数值恰命中 wait 分支即崩。这是一个一直存在、只是偶发的时序 bug，与并发量/延迟抖动区间相关。

改动（core.py _loop 重构）：
把整个主循环体（停止判断、on_idle 一次触发、暂停 wait、达上限 wait+清队列、取最早到期项、无可发项 wait、从队列移除）全部纳入**同一个** `with self._lock:` 持锁块内；`to_send` 在锁内赋值、锁外调 `self._do_send`（避免发送阻塞整个调度，行为延续原设计）。`Condition.wait()` 在持锁下调用，自动释放/重获锁，满足契约。
关键不变式：① `_do_send` 仍在锁外（避免 send_target 网络 IO 阻塞调度）；② `notify_all` 由 stop/stop_keep_queue/pause/resume/set_max_target/submit 在各自持锁块内发出，唤醒本循环；③ to_send 每次 while 迭代重置为 None，锁外引用安全。

校验：core.py lint 0 错误；本机真实 python C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe py_compile 通过。

约束/易错点：
- Condition 的 wait 必须持锁，这是 Python 线程基础契约；重构时务必确保 `with self._lock:` 覆盖范围包含全部 wait 调用点。
- 不要为了“避免死锁”而把 wait 移到锁外——本场景是单 Condition 单 Lock，wait 内部已正确释放锁，持锁调用反而安全。
- 上次 _loop 的 UnboundLocalError（记忆 53903049）是同函数另一处 bug，本次修复的是锁边界问题，两者独立但同源（_loop 重构不彻底）。

下次打包自动 CS0.30。需用户实测：长时间运行（含暂停/恢复、达上限、延迟抖动区间到期等待）不再出现该 RuntimeError；私信按延迟正常发出。
