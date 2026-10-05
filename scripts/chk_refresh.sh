#!/bin/bash
# 查看本轮「更新会话」各阶段耗时
#
# 2026-09-17 修补（OCR 审查 HIGH，两处）：
#   ① `cd "C:/temp/flowcap_test"` 失败被静默忽略 → 后续在**当前目录**找
#      logs/，永远找不到（或找到别的仓库的日志），输出误导。
#      且该路径是**主分支环境** —— 本脚本随 design/better-douyin 分支走，
#      指向主分支环境违反分支隔离铁律。改为可由 FLOWCAP_APP_ROOT 指定，
#      默认落到 design 分支的部署目录。
#   ② `$f` 在无日志文件时为空字符串 → 传成 `grep <pattern> ""`，
#      空参数被当作待搜索文件/模式，行为不可预期。现显式判空退出。
set -u

# 环境目录：优先环境变量（对齐「显式配置」原则），默认 design 分支
ROOT="${FLOWCAP_APP_ROOT:-C:/temp/flowcap_design}"
if [ ! -d "$ROOT" ]; then
  echo "!! 目录不存在: $ROOT（可用 FLOWCAP_APP_ROOT=<路径> 指定）" >&2
  exit 1
fi
cd "$ROOT" || { echo "!! 无法进入 $ROOT" >&2; exit 1; }

f=$(ls -t logs/run_*.log 2>/dev/null | head -1)
if [ -z "$f" ]; then
  echo "!! $ROOT/logs/ 下没有 run_*.log（守护未运行或日志已轮转）" >&2
  exit 1
fi

echo "=== $f ==="
echo
echo "--- 301 补全日志（前 22 条）---"
grep -E "301 补全" "$f" 2>/dev/null | head -22
echo
echo "补全总数: $(grep -c '301 补全' "$f" 2>/dev/null)"
echo
echo "--- 关键节点 ---"
grep -E "更新会话开始|长会话补全|BCC 截到昵称|写库完成|更新会话完成" "$f" 2>/dev/null | tail -8
