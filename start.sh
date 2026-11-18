#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
LOG_DIR="$ROOT_DIR/data/logs"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
OPEN_BROWSER=1

for arg in "$@"; do
  case "$arg" in
    --no-open)
      OPEN_BROWSER=0
      ;;
    --help|-h)
      cat <<'EOF'
Usage: ./start.sh [--no-open]

One-command local launcher for LawTeam-OS:
  1. Ensures .env exists (copies .env.example if missing)
  2. Verifies PostgreSQL on 127.0.0.1:5432 (starts Homebrew/Docker Postgres if needed)
  3. Syncs backend/frontend deps if missing and runs Alembic migrations
  4. Starts FastAPI (127.0.0.1:8000) + Vite (127.0.0.1:5173)
  5. Opens http://127.0.0.1:5173 in your browser (disable with --no-open)
EOF
      exit 0
      ;;
    *)
      echo "未知参数: $arg (使用 --help 查看说明)" >&2
      exit 1
      ;;
  esac
done

mkdir -p "$LOG_DIR" "$ROOT_DIR/data/cases"

BACKEND_PID=""
FRONTEND_PID=""
CLEANED_UP=0

cleanup() {
  local exit_code=$?
  if [[ "$CLEANED_UP" -eq 1 ]]; then
    return
  fi
  CLEANED_UP=1
  trap - EXIT INT TERM
  if [[ -n "$FRONTEND_PID" || -n "$BACKEND_PID" ]]; then
    echo ""
    echo "正在停止 LawTeam-OS 服务..."
  fi
  if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    kill "$FRONTEND_PID" 2>/dev/null || true
    wait "$FRONTEND_PID" 2>/dev/null || true
  fi
  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
  fi
  rm -f "$LOG_DIR/backend.pid" "$LOG_DIR/frontend.pid"
  exit "$exit_code"
}

on_signal() {
  exit 0
}

trap cleanup EXIT
trap on_signal INT TERM

port_in_use() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

check_pg_port() {
  local host="${1:-127.0.0.1}"
  local port="${2:-5432}"
  python3 -c '
import socket, sys
h, p = sys.argv[1], int(sys.argv[2])
try:
    with socket.create_connection((h, p), timeout=1.5):
        sys.exit(0)
except OSError:
    sys.exit(1)
' "$host" "$port" >/dev/null 2>&1
}

read_db_host_port() {
  python3 - "$ROOT_DIR/.env" <<'PY'
import os, pathlib, sys
from urllib.parse import urlparse
db_url = os.environ.get("DATABASE_URL", "")
env_path = pathlib.Path(sys.argv[1])
if not db_url and env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        if k.strip() == "DATABASE_URL":
            db_url = v.strip().strip("\"\x27")
            break
host, port = "127.0.0.1", "5432"
if db_url:
    p = urlparse(db_url)
    if p.hostname:
        host = p.hostname
    if p.port:
        port = str(p.port)
print(f"{host} {port}")
PY
}

# 1. Ensure .env
if [[ ! -f "$ROOT_DIR/.env" && -f "$ROOT_DIR/.env.example" ]]; then
  echo "[1/5] 未检测到 .env，正在从 .env.example 初始化..."
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
fi

# 2. Check / stop leftover LawTeam PIDs from previous crashed runs if needed
for pid_file in "$LOG_DIR/backend.pid" "$LOG_DIR/frontend.pid"; do
  if [[ -f "$pid_file" ]]; then
    old_pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
      kill "$old_pid" 2>/dev/null || true
      sleep 0.5
    fi
    rm -f "$pid_file"
  fi
done

if port_in_use "$BACKEND_PORT"; then
  echo "错误：端口 $BACKEND_PORT 已被其他进程占用：" >&2
  lsof -nP -iTCP:"$BACKEND_PORT" -sTCP:LISTEN >&2 || true
  exit 1
fi

if port_in_use "$FRONTEND_PORT"; then
  echo "错误：端口 $FRONTEND_PORT 已被其他进程占用：" >&2
  lsof -nP -iTCP:"$FRONTEND_PORT" -sTCP:LISTEN >&2 || true
  exit 1
fi

# 3. Ensure PostgreSQL is running
read -r PG_HOST PG_PORT <<< "$(read_db_host_port)"
echo "[2/5] 检查 PostgreSQL ($PG_HOST:$PG_PORT)..."
if ! check_pg_port "$PG_HOST" "$PG_PORT"; then
  if [[ "$PG_HOST" == "127.0.0.1" || "$PG_HOST" == "localhost" ]]; then
    if command -v brew >/dev/null 2>&1 && brew services list 2>/dev/null | grep -q "postgresql"; then
      PG_SVC="$(brew services list | awk '/postgresql/{print $1; exit}')"
      echo "      正在通过 Homebrew 启动 $PG_SVC..."
      brew services start "$PG_SVC" >/dev/null || true
    elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
      echo "      正在通过 Docker Compose 启动 postgres 容器..."
      docker compose -f "$ROOT_DIR/docker-compose.yml" up -d postgres
    fi

    for _ in $(seq 1 30); do
      if check_pg_port "$PG_HOST" "$PG_PORT"; then
        break
      fi
      sleep 1
    done
  fi
fi

if ! check_pg_port "$PG_HOST" "$PG_PORT"; then
  echo "错误：PostgreSQL ($PG_HOST:$PG_PORT) 未就绪，请先启动数据库或检查 DATABASE_URL。" >&2
  exit 1
fi

# 4. Sync dependencies if needed & run Alembic migrations
echo "[3/5] 检查依赖并同步数据库迁移..."
if ! command -v uv >/dev/null 2>&1; then
  echo "错误：未找到 uv，请先安装 https://docs.astral.sh/uv/" >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "错误：未找到 npm / Node.js，请先安装 Node.js 20+" >&2
  exit 1
fi

(
  cd "$BACKEND_DIR"
  if [[ ! -d ".venv" ]]; then
    uv sync
  fi
  if ! uv run python -c "import pathlib, playwright.sync_api as pw; p=pw.sync_playwright().start(); exe=pathlib.Path(p.chromium.executable_path); p.stop(); assert exe.exists()" >/dev/null 2>&1; then
    echo "      正在安装 Playwright Chromium 浏览器运行时（用于司法送达抓件）..."
    uv run playwright install chromium
  fi
  uv run alembic upgrade head
)

(
  cd "$FRONTEND_DIR"
  if [[ ! -d "node_modules" ]]; then
    npm install
  fi
)

# 5. Start backend & frontend
echo "[4/5] 启动后端服务 (http://127.0.0.1:$BACKEND_PORT)..."
(
  cd "$BACKEND_DIR"
  exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT"
) >"$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" > "$LOG_DIR/backend.pid"

for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:$BACKEND_PORT/api/health" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "后端启动失败，最近日志：" >&2
    tail -n 30 "$LOG_DIR/backend.log" >&2 || true
    exit 1
  fi
  sleep 0.5
done

if ! curl -fsS "http://127.0.0.1:$BACKEND_PORT/api/health" >/dev/null 2>&1; then
  echo "后端健康检查超时，最近日志：" >&2
  tail -n 30 "$LOG_DIR/backend.log" >&2 || true
  exit 1
fi

echo "[5/5] 启动前端工作台 (http://127.0.0.1:$FRONTEND_PORT)..."
(
  cd "$FRONTEND_DIR"
  VITE_API_PROXY_TARGET="http://127.0.0.1:$BACKEND_PORT" exec ./node_modules/.bin/vite --host 127.0.0.1 --port "$FRONTEND_PORT"
) >"$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
echo "$FRONTEND_PID" > "$LOG_DIR/frontend.pid"

for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$FRONTEND_PORT" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
    echo "前端启动失败，最近日志：" >&2
    tail -n 30 "$LOG_DIR/frontend.log" >&2 || true
    exit 1
  fi
  sleep 0.5
done

echo ""
echo "============================================================"
echo "  LawTeam-OS 已启动就绪"
echo "  前端工作台: http://127.0.0.1:$FRONTEND_PORT"
echo "  后端 API:   http://127.0.0.1:$BACKEND_PORT/docs"
echo "  日志目录:   $LOG_DIR"
echo "  演示账号:   partner_admin / lead_lawyer / legal_assistant (密码 LawTeam@2026)"
echo "  按 Ctrl+C 可一键停止前后端服务"
echo "============================================================"
echo ""

if [[ "$OPEN_BROWSER" -eq 1 ]] && command -v open >/dev/null 2>&1; then
  open "http://127.0.0.1:$FRONTEND_PORT" >/dev/null 2>&1 || true
fi

# Monitor both child processes until either exits or Ctrl+C
while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 1
done

echo "检测到服务进程意外退出，请检查 $LOG_DIR 日志。" >&2
exit 1
