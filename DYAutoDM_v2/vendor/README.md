# 上游子系统（vendored）—— DO NOT EDIT

本目录是**第三方开源代码的原样副本**，**不是本项目代码**。

## 用途
ADR-017（IM 远程登录更新凭证）的 **API 备用路径**：当 RPA（主路径）被风控拦截时，
改走本模块的纯协议模拟。

## 来源与版本（Open-Source Provenance Law）
| 项 | 值 |
|---|---|
| 仓库 | https://github.com/cv-cat/DouYin_Spider |
| commit | `4479ea784bf3e63e75fcbe4ca985f84678d46b27` |
| date | 2026-09-20 01:36:53 +0800 |
| subject | Merge pull request #91 from cv-cat/feat/send-live-comment |
| 获取方式 | `git clone --depth 1`（浅克隆，不留历史） |

## 铁律
1. **禁止修改本目录内的任何文件** —— 改动会破坏与上游的溯源关系，
   且下次同步即被覆盖。需要适配请写在 `backend/auto_dm/login_api_vendor.py`
   之类的**适配层**里。
2. **依赖隔离**：本模块需要 `curl_cffi`（TLS/HTTP2 指纹冒充），
   已装到项目 Python（Python314），见 `backend/requirements.txt` 的补充说明。
3. **`sys.path` 注入方式**使用（不是安装为包）：
   ```python
   VENDOR = path/to/vendor/douyin_spider_upstream
   sys.path.insert(0, VENDOR)
   os.chdir(VENDOR)          # 上游以相对路径读 .env / 素材
   from dy_apis.login_api import DYLoginApi
   ```
4. **素材依赖**（上游警告的两项，需真实设备捕获）：
   - `DY_FPK1` / `DY_FPK2` → 否则用 FingerprintJS fixture 纯算（可能触发风控）
   - `DY_DTRAIT_BLOB` / `DY_SESSION_DTRAIT` → 混淆 SDK 无法纯算，必须从真机捕获

## 已实测结论（2026-09-26）
| 项 | 结果 |
|---|---|
| 13 个模块导入 | ✅ 13/13（在 Python314 + curl_cffi 0.16.3 下）|
| `bootstrap_auth()` | ✅ 2.0s，33 项 cookie，P-256 密钥自生成 |
| `get_qrcode(auth)` | ✅ 0.17s，`error_code=0`，返回真二维码（token + base64 PNG）|
| 与 RPA 对比 | 取二维码耗时 **2.2s vs 23.5s** |

**未验证**（需真人手机扫码）：`check_qrcode` 轮询 → 扫码确认 → `save_credential`。
