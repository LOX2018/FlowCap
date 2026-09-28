# A-2 施工报告：把 `features.py` 接成 API 层真实调用路径

- 日期：2026-09-28
- 仓库：`C:/Users/LOX/Desktop/DYchajian`
- 分支：`design/better-douyin`（`git rev-parse --abbrev-ref HEAD` 实测输出 `design/better-douyin`，全程未切换）
- 解释器：`C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe`（loguru 0.7.3 + fastapi 实测可导入）

---

## 1. 改了什么

### 1.1 `backend/api/crawl.py`（唯一被修改的存量文件，+15 / -3）

**改动点 A — `/search` 的 user 分支（原第 203 行附近）**

```diff
         else:
-            raw = await asyncio.to_thread(DouyinAPI.search_some_user, auth, q, num)
-            items = [_map_user(u) for u in (raw or [])]
+            # 经 features 基座封装层调用（返回 {"ok":bool,"data":...}）。
+            # ok=False 必须按原有 502 语义上抛，绝不把「采集失败」降级成「没有结果」。
+            import features
+            res = await asyncio.to_thread(features.search_user, auth, q, num)
+            if not res.get("ok"):
+                raise HTTPException(502, f"搜索失败: {res.get('error')}")
+            raw = res.get("data") or []
+            items = [_map_user(u) for u in raw]
```

**改动点 B — `/comments` 的 `_fetch()` 内循环（原第 238 行附近）**

```diff
         for _ in range(40):  # 硬上限 40 页 * 每页 ≤20 条
-            res = DouyinAPI.get_work_out_comment(auth, url, cursor)
+            # 经 features 基座封装层调用（返回 {"ok":bool,"data":...}）。
+            # ok=False 时上抛错误（外层转 502），绝不把失败当成「没有评论」。
+            import features
+            r = features.work_comments(auth, url, cursor)
+            if not r.get("ok"):
+                raise RuntimeError(f"评论采集失败: {r.get('error')}")
+            res = r.get("data")
             batch = res.get("comments") if isinstance(res, dict) else None
```

**行为等价性说明**
- `features.search_user(auth, query, num)` 内部即 `_safe("search_some_user", auth, query, num)`，参数逐位一致。
- `features.work_comments(auth, url, cursor)` 内部即 `_safe("get_work_out_comment", auth, url, cursor)`，参数逐位一致。
- `_safe` 成功时返回原返回值于 `data`；失败时 `{"ok": False, "error": ...}`。
- **失败不吞**：两处都在 `ok=False` 时抛错 —— user 分支直接 `HTTPException(502)`（命中既有 `except HTTPException: raise`）；评论分支抛 `RuntimeError`，由既有 `except Exception` 转 `HTTPException(502)`。二者状态码与改造前完全一致（原直连异常也是经同一分支转 502）。

**刻意未改**：`search_some_general_work`（video 分支）**未动**。该分支带 `filter_duration` 参数（`CrawlSearchRequest.filter_duration`），而 `features.search_work` 签名只有 `(auth, query, num, sort_type, publish_time)`，不支持时长筛选；改它会造成功能丢失。video 分支保留原直连，diff 中无任何该分支行变更。

### 1.2 `backend/test_features_wiring.py`（新增，8199 字节）

单模块 unittest 门禁，7 个用例，全程打桩、零真实抖音请求：
- **G1** `/search` user 分支必须经 `features.search_user`（调用计数=1 + 消费返回 data）
- **G2** `/comments` 必须经 `features.work_comments`（调用计数≥1 + 消费返回 data）
- **G3 / G3b** `features.*` 返回 `ok=False` ⇒ 端点必须抛 `HTTPException(502)`，且确证走的是 features（调用计数=1）
- **G4** `ok=True` 空 data ⇒ 返回空列表而非报错（防「接通了但读错字段」）
- **G5 / G6** 负控哨兵判别力自证：直连 `DouyinAPI.search_some_user` / `get_work_out_comment` 时，setUp 安装的哨兵必抛

---

## 2. 真实跑过的命令与输出

### 2.1 验收①：import 计数 0 → ≥1

改动前（原始仓库）：
```
$ cd DYAutoDM_v2/backend && grep -c 'from features import\|import features' api/crawl.py
0
```
> 旁证：全仓 `grep -rn "features" --include=*.py .` 在改动前 `api/crawl.py` 内零命中，`features.py` 除自身外无任何被 import 记录（仅 `errcode_data.py` 引用了其日志码 SYS-007 文本、`client_search.py` 有一条注释提及）。

改动后：
```
$ cd DYAutoDM_v2/backend && grep -c 'from features import\|import features' api/crawl.py
2
```
（`import features` 出现 2 处，user 分支 + 评论分支；文件顶部未加全局 import，避免动其它分支。）

### 2.2 验收②：新测试全绿

```
$ cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend
$ TMPROOT=$(mktemp -d) && DY_APP_ROOT="$TMPROOT" <python314> -m unittest test_features_wiring -v
```
真实输出（末尾）：
```
test_g1_search_user_goes_through_features ... ok
test_g2_comments_go_through_features ... ok
test_g3_search_failure_is_not_swallowed ... ok
test_g3b_comments_failure_is_not_swallowed ... ok
test_g4_ok_true_empty_data_is_empty_not_error ... ok
test_g5_direct_search_sentinel_is_in_discriminating ... ok
test_g6_direct_comments_sentinel_is_in_discriminating ... ok

----------------------------------------------------------------------
Ran 7 tests in 11.156s

OK
REAL_EXIT=0
```
（`DY_APP_ROOT` 指向 `mktemp -d` 新建临时目录，日志实测落库路径为 `...\Temp\dyautodm_a2feat_*\data\dyautodm.db`；仓库 `backend/data/` 未被写，见第 4 节。）

### 2.3 验收③：负控 —— 改回直连 ⇒ 必变红；还原 ⇒ 复绿

**负控操作**：对 `api/crawl.py` 的两处临时 patch 回直连 `DouyinAPI.*`（补丁前已 `cp api/crawl.py /tmp/crawl_a2_backup.py` 备份）。

负控后 `grep -c` 与测试结果（真实输出）：
```
$ grep -c 'from features import\|import features' api/crawl.py
0
$ ... <python314> -m unittest test_features_wiring -v
...
AssertionError: [负控] 直连 DouyinAPI.search_some_user 被调用 —— features 封装层被绕过
...
FAIL: test_g3_search_failure_is_not_swallowed
AssertionError: 0 != 1 : 未走 features.search_user（负控点）
FAIL: test_g3b_comments_failure_is_not_swallowed
AssertionError: 0 != 1 : 未走 features.work_comments（负控点）
----------------------------------------------------------------------
Ran 7 tests in 12.451s

FAILED (failures=2, errors=3)
```
**负控结论：7 个用例里 5 个变红（2 failures + 3 errors），门禁确实抓得住「绕过封装层」的回归，非恒绿。**

**还原**：`cp /tmp/crawl_a2_backup.py api/crawl.py`，随后
```
$ grep -c 'from features import\|import features' api/crawl.py
2
$ git diff --stat -- api/crawl.py
 DYAutoDM_v2/backend/api/crawl.py | 18 +++++++++++++++---
 1 file changed, 15 insertions(+), 3 deletions(-)
```
复绿复跑：见 2.2（`Ran 7 tests ... OK`，`REAL_EXIT=0`）。

---

## 3. 验收对照

| 验收项 | 判据 | 实测 | 结论 |
|---|---|---|---|
| ① | `grep -c` 由 0 → ≥1 | 0 → 2 | ✅ 通过 |
| ② | 新测试全绿 | `Ran 7 tests ... OK`，exit 0 | ✅ 通过 |
| ③ | 负控改回直连必红、还原复绿 | 负控 `FAILED (failures=2, errors=3)`；还原后 `OK` | ✅ 通过 |

---

## 4. 边界核查

- `git rev-parse --abbrev-ref HEAD` → `design/better-douyin`（未切分支）。
- `git status --porcelain -- DYAutoDM_v2/backend/data` → **无输出**（仓库 `data/` 未被污染）。
- 仅改/建点名文件：`api/crawl.py`（M）、`test_features_wiring.py`（??，新增）。未触碰任何版本文件、未 git add/commit/checkout/stash、未跑全量测试。
- `features.py` 只读，未改。
- video 分支（`search_some_general_work` + `filter_duration`）零改动。

## 5. 未核验项

- 未做真实抖音网络调用（本任务门禁全部打桩，符合「零真实请求」约束）；`features.search_user` / `work_comments` 与基座 `DouyinAPI` 的真实线上等价性**未核验**（依赖 `features.py` 的 `_safe` 直接透传，属静态一致性，未做真机联调）。
- `ResourceWarning: unclosed database` 为测试进程退出时 sqlite 连接未显式关闭的既有告警，不影响用例结论，非本次改动引入。
