# 从 OpenAPI schema 生成前端 TypeScript 客户端
# 用法: bash scripts/gen_api_client.sh
#
# 前置: 后端已启动 (uvicorn main:app --port 8000)

set -e

SCHEMA_FILE="frontend/src/api/schema.json"
OUT_FILE="frontend/src/api/schema.d.ts"

echo "==> 拉取 OpenAPI schema"
curl -s http://127.0.0.1:8000/openapi.json > "$SCHEMA_FILE"

echo "==> 生成 TypeScript 类型"
npx --workspace frontend openapi-typescript "$SCHEMA_FILE" -o "$OUT_FILE"

echo "==> 完成: $OUT_FILE"
echo "    前端可这样使用强类型客户端:"
echo "    import createClient from 'openapi-fetch'"
echo "    import type { paths } from './schema'"
echo "    const client = createClient<paths>()"
