# NOTICE — 第三方依赖获取说明

## 为何本仓库不含 `DYAutoDM_v2/vendor/`

`DYAutoDM_v2/vendor/douyin_spider_upstream/` 是上游开源项目
**`cv-cat/DouYin_Spider`** 的原样副本，用作 ADR-017（IM 远程登录更新凭证）的
API 备用路径：当 RPA 主路径被风控拦截时，改走该模块的纯协议模拟。

**但本仓库不发布它。** 原因：

| 项 | 状态 |
|---|---|
| 上游仓库 | https://github.com/cv-cat/DouYin_Spider |
| 上游 `LICENSE` 文件 | **不存在**（GitHub API `GET /repos/cv-cat/Douyin_Spider/license` 返回 404，2026-10-06 确证） |
| README 许可声明 | 无 |
| 代码头部版权声明 | 无 |

按开源惯例，**无 LICENSE 意味着默认 `All rights reserved`**（参见
[Fedora 贡献指南](https://docs.fedoraproject.org/en-US/legal-guide/licensing/))，
即"保留所有权利"，任何再分发都需要事先获得上游授权。

因此本仓库选择**不发布**该副本 —— 这是保守的合规处置。上游代码仍在本地磁盘
（开发不受影响），只是不进公开仓库。

> ⚠️ 请注意：`DouYin_Spider` 上游 README 自述"仅供学习与技术研究使用"。
> 商业分发前请自行评估法律风险，并向上游确认授权。

---

## 本地开发如何获取 vendor

首次克隆后，需手动还原该目录：

```bash
cd DYAutoDM_v2/vendor

# 按 PROVENANCE.md 记录的 commit 浅克隆
git clone --depth 1 https://github.com/cv-cat/DouYin_Spider.git douyin_spider_upstream
cd douyin_spider_upstream
git checkout 4479ea784bf3e63e75fcbe4ca985f84678d46b27

# 安装其依赖
pip install -r requirements.txt
```

**同步上游**时按 `DYAutoDM_v2/vendor/README.md` 的流程：重新 clone 后覆盖，
**不要手动修改 vendor 内的任何文件**（会破坏溯源关系，下次同步即被覆盖；
需要适配请写在 `backend/auto_dm/login_api_vendor.py` 这类适配层里）。

---

## 其他已排除内容

以下目录同样**不在本仓库**，仅存于原作者本地工作区：

| 目录 | 性质 |
|---|---|
| `工作记忆/` | 开发过程笔记、交接卡、案例归档 |
| `artifacts/` | 审计产物、验收证据 |
| `_ext_repos/` | 克隆的第三方参考库（对照用途） |
| `DYAutoDM_v2/knowledge/` | 开发案例与知识库种子 |
| `DYAutoDM_v2/artifacts/` | 项目内审计产物 |
| `DYAutoDM_v2/.hermes/` | AI 工具本地状态 |
| `data/` · `accounts/` · `*.db` | 运行期数据与账号凭证（**.gitignore 排除**） |

本仓库只发布**产品代码**：Tauri 壳、前端、后端、构建脚本、文档。
