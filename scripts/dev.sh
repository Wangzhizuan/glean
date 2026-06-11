#!/bin/sh
# 同时启动前端 + 后端（演示模式）
# 用法: npm run dev:all
#       npm run dev:real   （真实处理模式，需要 yt-dlp/mlx-whisper/ollama）
set -eu

# 优先使用 Homebrew Python 3.13，回退到系统 python3
if [ -x /opt/homebrew/bin/python3.13 ]; then
  PYTHON=/opt/homebrew/bin/python3.13
  export PATH="/opt/homebrew/opt/python@3.13/libexec/bin:$PATH"
else
  PYTHON=python3
  export PATH="$HOME/Library/Python/3.9/bin:$PATH"
fi

MODE="${SHIJU_PROCESSOR_MODE:-real}"

cleanup() {
  kill "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# 真实模式下自动启动 Ollama（如果尚未运行）
if [ "$MODE" = "real" ]; then
  if ! curl -sf http://127.0.0.1:11434/ >/dev/null 2>&1; then
    echo "🦙 启动 Ollama 服务..."
    open -a Ollama
    # 等待 Ollama 就绪（最多 15 秒）
    i=0
    while [ $i -lt 15 ]; do
      if curl -sf http://127.0.0.1:11434/ >/dev/null 2>&1; then
        echo "✅ Ollama 已就绪"
        break
      fi
      sleep 1
      i=$((i + 1))
    done
    if [ $i -ge 15 ]; then
      echo "⚠️  Ollama 未能在 15 秒内启动，摘要功能可能不可用"
    fi
  else
    echo "✅ Ollama 已在运行"
  fi
  # 文章提取依赖软提示（不强制安装）
  $PYTHON -c "import trafilatura" >/dev/null 2>&1 || \
    echo "ℹ️  未安装 trafilatura，文章提取将不可用：pip3 install --break-system-packages trafilatura"
  $PYTHON -c "import playwright" >/dev/null 2>&1 || \
    echo "ℹ️  未安装 playwright（飞书/动态网页兜底需要）：pip3 install --break-system-packages playwright && python3 -m playwright install chromium"
  command -v lark-cli >/dev/null 2>&1 || \
    echo "ℹ️  未检测到 lark-cli（飞书文档优先走该 CLI，更稳定）：npm i -g @larksuite/cli && lark-cli auth login"
fi

echo "🚀 启动拾句后端 (模式: $MODE, 端口: 8787, Python: $PYTHON)"
SHIJU_PROCESSOR_MODE="$MODE" $PYTHON -m uvicorn app.main:app \
  --host 127.0.0.1 --port 8787 --reload --app-dir backend &
BACKEND_PID=$!

echo "🚀 启动 Next.js 前端 (端口: 3000)"
npm run dev
