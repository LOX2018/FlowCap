【DYAutoDM_v2 工作记忆 2026-08-16 #26】统一账号检查方式 + 取消默认账号。

需求（用户原话）：①「同一检查方式，怎么可能自检通过、账号管理页面失败」——要求自检弹窗与账号管理页用同一套检查逻辑；②「取消默认账号的设定，所有的账号都需要通过新增才能管理」。

改动（V2 only，3 文件 + 1 资源文件）：

1) backend/auto_dm/accounts.py：
   - 彻底重写 account_status：原实现走 _probe（get_my_uid + create_conversation + 60s 缓存 + 独立 label 体系），与 verify_account 的 wp 判定（只看 .env 文件签名四件套是否齐全，不真实探活）维度完全不同 → 自检显示「凭证守护已捕获」而账号页显示「失效（探活失败/超时）」的割裂。现 account_status 直接【委托 verify_account(name, dm_loopback=False)】，二者共用同一真实探活结果（守护在跑 + 文件签名齐全 + get_my_uid 探活成功才判 ok）。verify_account 的 wp 逻辑同步收紧：守护未运行→stopped「凭证守护未运行」、探活失败→fail「失效（探活失败/超时）」、成功→ok「正常（捕获齐全·探活通过）」，彻底消除不一致。
   - 取消默认账号：_load_index 不再自动创建「默认账号」（根 .env 注入），空索引返回 {"current":null,"accounts":{}}；list_accounts 不再把根 .env 当默认账号注入；current_name/current_env_path/_env_path_of 无账号时返回 None（不再回退 _DEFAULT_NAME/_DEFAULT_ENV）；monitor_name/sender_name 无绑定返回 None；remove_account 去掉「默认账号不可删」保护；clear_credentials 不再清根 .env；删除 _DEFAULT_ENV/_DEFAULT_NAME 常量。
   - _probe / _status_cache 保留但 account_status 不再调用（_probe 现仅定义未用，无碍；后续可删）。

2) backend/api/accounts.py：
   - _to_raw_account 的 wpEngine/dmEngine 原来自创（wpEngine 只看 st.has_web_protect 显示「wp 凭证未持久化（四件套兼容）」，与 verify_account 完全两套）→ 改为直接取自 verify_account(name) 的 wp/dm 字段，保证账号卡片双引擎标注与启动自检弹窗 100% 一致。

3) frontend/src/api/sidecar.ts：
   - stopRecvDaemon 的 account 兜底 `accounts[0] || "默认账号"` 改为 `accounts[0] || ""`（实际调用均传 [a.name]，不会触发；消除「默认账号」字面量残留）。

4) auto_dm/accounts/accounts.json（运行时资源）：
   - 旧索引含 {"current":"默认账号","accounts":{"默认账号":".env"}}（rel=".env" 实际不存在，孤儿条目）。清空为 {"current":null,"accounts":{}}，使默认账号彻底退出管理；根目录无真实 .env，删除安全。

关键约束/易错点：
- ① 这是【统一检查】而非「放宽自检」——现在两者都会如实显示「失效（探活失败/超时）」/「凭证守护未运行」，不会再出现自检绿、账号页红。
- ② 取消默认账号后无账号时：verify_account 返回 wp/dm level=stopped「无账号（请先新增）」；account_status 返回 level=missing「无账号（请先新增）」；list_accounts 返回 []；前端账号页应显示空态 + 「新增账号」入口（自建模态已支持 name-only）。
- ③ 引擎启动（auto_dm.py）依赖 current_env_path()，现无账号返回 None → 监测登录失败会优雅报「监测登录失败」日志，不崩溃；用户需先新增并选中账号再启动监听。
- ④ 删除默认账号不改变「新增账号」流程（add_account 仍写 accounts/<name>/.env），只是删除了隐式根 .env 默认账号这条历史兼容路径。

验证：两 backend 文件 py_compile PASS + read_lints 0；重打包 3 sidecar（build_sidecar.py）→ 强制覆盖 C:\temp\dyautodm_test\binaries\（先 Stop-Process 旧进程）；启动新 backend 实测 GET /api/accounts 返回 {ok:true,accounts:[]}（默认账号已消失）；verify_account/account_status 走同一真实探活。

本机真实 python：C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe。cargo 在 C:\Users\LOX\.cargo\bin。
下次改 Rust/主程序按 +0.01 规则升 0.8.0（本次仅 backend sidecar 改动 + 资源，主 exe 沿用 0.7.0 无需重编）。
