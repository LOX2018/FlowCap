# 案例 · P3-C 剩余 A 类静默异常降级 + 策略中心 timeout 配置（v0.44.50）

> 日期：2026-09-23　提交：`06af132`
> 来源：HC-07 §2.2 / §2.3

## 目标

1. 修复 10 处生产路径 A 类静默 `except Exception: pass`（core/auto_dm.py ×7 + auto_dm/accounts.py ×3 + 裸 except 降级 dy_util.py）
2. 在 app_config_schema.py general section 新增 3 项 timeout 热生效配置（BCC HTTP / 端口探活 / 通用 HTTP）

## 实施内容

| 文件 | 改动 |
|------|------|
| `core/auto_dm.py` | 7 处：member_ctx getmtime / set account_name / uid_probe / set sender acct_name / dispatch records_list / 重新扫描 ws close ×2 |
| `auto_dm/accounts.py` | 3 处：member_ctx accounts_root / os.replace bad->good / _rmtree_account_dir |
| `utils/dy_util.py` | `except:` → `except Exception:`（裸 except 归零） |
| `services/app_config_schema.py` | general 新增 `timeout_bcc_http`(15.0)、`timeout_fast_probe`(0.3)、`timeout_http_req`(30.0) |
| `services/probe.py` | 10 处静默异常加 debug 日志 |
| `services/dm_dispatch.py` | 3 处静默异常加 debug 日志 |

## 版本

v0.44.50（六处版本齐平）

## 验证

- `py_compile` 全部 6 文件通过 ✅
- `python -m unittest discover` → **623 全绿** ✅