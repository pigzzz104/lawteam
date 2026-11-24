#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
TMP_OPENAPI=""
TMP_TS=""

cleanup() {
  rm -f "${TMP_OPENAPI:-}" "${TMP_TS:-}"
}
trap cleanup EXIT

echo "[1/4] Running backend Ruff + Pyright..."
(
  cd "$ROOT_DIR/backend"
  uv run ruff check app/ tests/
  uv run pyright app/
)

echo "[2/4] Running backend pytest (isolated lawteam_test DB)..."
(
  cd "$ROOT_DIR/backend"
  uv run pytest -q
)

echo "[3/4] Checking OpenAPI -> TypeScript contract drift + frontend build..."
TMP_OPENAPI="$(mktemp)"
TMP_TS="$(mktemp)"
(cd "$ROOT_DIR/backend" && uv run python scripts/export_openapi.py) > "$TMP_OPENAPI"
(cd "$ROOT_DIR/frontend" && npx openapi-typescript "$TMP_OPENAPI" -o "$TMP_TS")
if ! diff -u "$ROOT_DIR/frontend/src/types/openapi.generated.ts" "$TMP_TS"; then
  echo "ERROR: OpenAPI TypeScript schema drifted from backend." >&2
  echo "Run: cd frontend && npm run gen:api-types" >&2
  exit 1
fi
(cd "$ROOT_DIR/frontend" && npm run build)

echo "[4/4] Running project redline regex scan..."
forbid_match() {
  local label="$1"
  local pattern="$2"
  shift 2
  local matches
  matches="$(grep -rnE "$pattern" "$@" || true)"
  if [[ -n "$matches" ]]; then
    echo "ERROR: Redline violation ($label):" >&2
    echo "$matches" >&2
    exit 1
  fi
}

forbid_match "broken Tailwind CSS-var opacity" "var\(--md-sys-color[^)]*\)\]/" "$ROOT_DIR/frontend/src"
forbid_match "native window.confirm" "window\.confirm\(" "$ROOT_DIR/frontend/src"
forbid_match "retired wording" "致命死线|刚性程序|常规节点|内部待办" "$ROOT_DIR/frontend/src" "$ROOT_DIR/backend/app"
forbid_match "raw native input outside components/ui" "type=[\"'](checkbox|radio|color|file)[\"']" --exclude-dir=ui "$ROOT_DIR/frontend/src"

echo "ALL CHECKS PASSED."
