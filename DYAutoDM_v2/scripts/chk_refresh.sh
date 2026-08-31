#!/bin/bash
# 查看本轮「更新会话」各阶段耗时
cd "C:/temp/dyautodm_test"
f=$(ls -t logs/run_*.log 2>/dev/null | head -1)
echo "=== $f ==="
echo
echo "--- 301 补全日志（前 22 条）---"
grep -E "301 补全" "$f" 2>/dev/null | head -22
echo
echo "补全总数: $(grep -c '301 补全' "$f" 2>/dev/null)"
echo
echo "--- 关键节点 ---"
grep -E "更新会话开始|长会话补全|BCC 截到昵称|写库完成|更新会话完成" "$f" 2>/dev/null | tail -8
