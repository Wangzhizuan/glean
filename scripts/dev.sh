#!/bin/sh
# 同时启动前端 + 后端（演示模式）
# 用法: npm run dev:all
#       npm run dev:real   （真实处理模式，需要 yt-dlp/mlx-whisper/ollama）
set -eu

export PATH="$HOME/Library/Python/3.9/bin:$PATH"

MODE="${SHIJU_PROCESSOR_MODE:-demo}"

cleanup() {
  kill "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "🚀 启动拾句后端 (模式: $MODE, 端口: 8787)"
SHIJU_PROCESSOR_MODE="$MODE" python3 -m uvicorn app.main:app \
  --host 127.0.0.1 --port 8787 --reload --app-dir backend &
BACKEND_PID=$!

echo "🚀 启动 Next.js 前端 (端口: 3000)"
npm run dev
