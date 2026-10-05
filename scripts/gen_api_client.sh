# 从 OpenAPI schema 生成前端 TypeScript 客户端
# 用法: bash scripts/gen_api_client.sh
#
# 前置: 后端已启动 (uvicorn main:app --port 8000)

set -e

SCHEMA_FILE="frontend/src/api/schema.json"
OUT_FILE="frontend/src/api/schema.d.ts"

echo "==> 拉取 OpenAPI schema"
# 2026-09-17 修补（OCR 审查 HIGH —— curl 不因 HTTP 错误失败）：
# `curl -s` 对 404/500 也返回 0，会把错误页写进 schema.json，
# 随后 openapi-typescript 基于垃圾输入生成类型（或报错难定位）。
# 加 `-f`（HTTP 错误 → 非零退出）与 `--show-error`；配合 set -e 立即中止。
# 另加 `--max-time` 避免后端未启动时长时间挂起。
curl -fsS --max-time 30 http://127.0.0.1:8000/openapi.json > "$SCHEMA_FILE"
# 二次校验：必须是合法 JSON 且含 openapi 字段（防代理/网关返回 HTML）
python -c "import json,sys; d=json.load(open('$SCHEMA_FILE',encoding='utf-8')); sys.exit(0 if 'openapi' in d else 1)" \
  || { echo "!! schema.json 不含 openapi 字段（后端未就绪或返回非 schema）"; exit 1; }

echo "==> 生成 TypeScript 类型"
npx --workspace frontend openapi-typescript "$SCHEMA_FILE" -o "$OUT_FILE"

echo "==> 完成: $OUT_FILE"
echo "    前端可这样使用强类型客户端:"
echo "    import createClient from 'openapi-fetch'"
echo "    import type { paths } from './schema'"
echo "    const client = createClient<paths>()"
