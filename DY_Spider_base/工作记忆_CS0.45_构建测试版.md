# 工作记忆 CS0.45：构建测试版封装（前端子页面拆分版交付）

## 文件
- `build_exe.py`（复用，未改）、`version.txt`（CS0.44 → CS0.45）、`dist/DYAutoDM/DYAutoDM_CS0.45.exe`（新产物）

## 改动内容
1. 用本机真实 python 跑 `build_exe.py --no-clean` 重新打包测试版（TEST_BUILD=True 带控制台窗口）。
2. 版本自动 bump CS0.44 → CS0.45，产物改名 `DYAutoDM_CS0.45.exe`（onefile + 控制台）。
3. `--no-clean` 保留上一轮 dist 随附资源（vb_chromium 167 文件 / web / 各 profile / .env / logs / auto_dm 目录）；collect_data 的 vb_chromium 拷贝因 159 文件 > 50 阈值被 safe-delete 拦截，但 `--no-clean` 已保留旧资源故无影响。

## 校验（均通过）
- 打包日志：`web_bridge.py changed` 触发 PYZ 重编译、`index.html changed` + `_input_datas changed` 触发 web 资源重打包；EXE 构建成功。
- CArchiveReader 验证 exe 内嵌 web 资源 23 项 ALL_INCLUDE：含 `web/framework.js`、`web/app.js`、`web/pages/{overview,crawl,live,messages,accounts,tasks,settings}.js`、vendor（react/react-dom 生产版 + framer-motion + babel.min）。即上一轮「前端拆分为子页面 + 框架固定强缓存」（CS0.44）的成果已正确打进 exe。
- 冒烟测试：双击启动 `DYAutoDM_CS0.45.exe`，进程正常运行（pid=12708），无崩溃 / 无 traceback，6s 后主动清理进程。
- 随附资源目录齐全（vb_chromium/vb_profile_*/pw_profile_dm/web/logs/auto_dm/.env 全部 OK）。

## 关键约束/易错点
- 本机真实 python：`C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe`（Store 别名不可用，全程绝对路径）。
- safe-delete 拦截 vb_chromium 拷贝（159>50）：用 `--no-clean` 规避，旧随附资源保留；若曾执行 clean 需手动 `_res_copy_*.ps1` 补拷。
- 前端子页面拆分（CS0.44）后，index.html 改外链 7 个 page + framework.js + app.js；exe 内嵌验证证明拆分版已生效。
- 测试版保留控制台窗口方便 debug；正式版需改 `TEST_BUILD=False` 加 `--noconsole`。

## 用户实测建议
双击 `dist/DYAutoDM/DYAutoDM_CS0.45.exe` 启动 WebView 前端，验证：
1. 框架层强缓存生效（framework.js/vendor 首次拉取后刷新不再重传）；
2. 7 Tab 切换无崩溃（拆分版已 playwright MCP 实测 0 errors）；
3. 私信链路/账号引擎校验等与 CS0.44 行为一致。

下次打包自动 CS0.46。
