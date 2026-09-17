# 版本探针冷启动误阻断修复（v0.43.43→v0.43.44）

## 问题现象

桌面端启动后，界面显示全屏遮罩：
> 后端版本无法读取（TypeError: Failed to fetch）— 可能 sidecar 未部署或版本探针缺失

用户无法关闭，必须重启应用。但此时后端实际已正常运行（`/api/version` 返回 0.43.42）。

## 根因分析

### 执行链
```
App.mount → setTimeout(run, 1200ms)
  → checkVersionConsistency()
    → fetch(http://127.0.0.1:8000/api/version)  ← 裸 fetch，无等待
    → sidecar 冷启动未完成（ECONNREFUSED）
    → TypeError: Failed to fetch
    → backend="unknown"
    → setVerBlock("后端版本无法读取...") → 全屏遮罩，无重试
```

### 两个根本缺陷

**缺陷 1：探针绕过就绪等待**
- `client.ts` 中的 `request()` 函数在每次调用前都会 `await ensureBackendReady()`（30s 轮询 `/api/status`）
- 但版本探针 `checkVersionConsistency()` 是例外，直接裸 `fetch`，不等待 sidecar 就绪
- sidecar 冷启动（PyInstaller 解包 + uvicorn 监听）通常 >1.2s，1.2s 后的探针必然撞上 ECONNREFUSED

**缺陷 2：单次探测无重试**
- `useEffect` 仅执行一次，失败后永久设置 `verBlock`，遮罩永不消失
- 即使后端随后就绪，也不会重新探测

### 间歇性证据

日志显示同一构建在不同时间行为不同：
```
19:51:00 → backend="0.43.37" match=true  (后端已就绪)
20:04:04 → backend="unknown"  match=false (冷启动中)
20:07:49 → backend="0.43.37"  match=true  (重试后成功)
```

## 修复方案

### 1. `frontend/src/api/client.ts` — 探针先等就绪
```typescript
// 修复前：直接 fetch
const r = await fetch(`${BASE}/api/version`, { method: "GET" });

// 修复后：先等侧车就绪
try {
  await ensureBackendReady();  // 复用现有 30s 轮询逻辑
} catch (e) {
  detail = `后端引擎未就绪（${String(e)}）`;
}
if (!detail) {
  const r = await fetch(`${BASE}/api/version`, { method: "GET" });
  // ...
}
```

### 2. `frontend/src/App.tsx` — 失败重试机制
```typescript
const isRetryable = (d: string) =>
  d.includes("未就绪") || d.includes("无法连接后端") || d.includes("返回 5") || d.includes("返回 0");

// 最多重试 3 轮，每轮 5s，共 17s 窗口
if (isRetryable(v.detail)) {
  if (timers.length < 3) {
    const t = setTimeout(() => { if (alive) run(); }, 5000);
    timers.push(t);
    return;
  }
}
// 不可恢复（真版本不一致）→ 立即阻断
setVerBlock(why);
```

### 3. v0.43.44 附加修复：登录框延迟渲染
- 原逻辑：`prealigned` 完成即显示登录框，此时后端可能尚未 ready
- 新逻辑：`if (!prealigned || !ready)` 才显示 BootSplash，确保登录框出现时后端已就绪

## 部署状态

| 组件 | 版本 | 状态 |
|---|---|---|
| 主程序 exe | 0.43.44 | ✅ 已部署 |
| backend sidecar | 0.43.44 | ✅ md5 一致 |
| recv_daemon sidecar | 0.43.44 | ✅ md5 一致 |
| browser_daemon sidecar | 0.43.44 | ✅ md5 一致 |
| 前端 dist | 0.43.44 | ✅ 已入包 |

**部署路径**：`C:\temp\dyautodm_design\`（design/better-douyin 分支环境）

## 验收命令

```bash
# 重启桌面端后执行
curl -s http://127.0.0.1:8000/api/version
# 期望: {"backend":"0.43.44","pid":...,"frozen":true,"started_at":"..."}

# 前端版本检查（DevTools Console）
# 应看到: [VERSION] ok 0.43.44
```

## 教训

1. **探针不能绕过就绪等待**：所有对后端的探测请求都应经过 `ensureBackendReady()`，除非有明确理由跳过
2. **单次失败不应永久阻断**：启动阶段的网络/时序问题应有重试机制
3. **日志是最好的诊断工具**：`frontend_boot.log` 中的 `version.check` 条目直接暴露了间歇性问题模式
4. **版本门禁是双刃剑**：铁律防止了更严重的静默不一致，但实现时必须考虑时序竞争

## 相关提交

- `f886978` fix(version): 版本探针重试 + ensureBackendReady() 防冷启动误阻断（v0.43.43）
- `ef3d31f` fix(startup): 登录框延迟至后端就绪后再弹出（v0.43.44）
