# 拾句 (glean)

本地视频文案提取工具。前端使用 Next.js，后端使用 FastAPI + SQLite。

## 启动

首次运行先安装后端依赖：

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
```

同时启动 Next.js 和 FastAPI：

```bash
npm run dev:all
```

打开 [http://localhost:3000](http://localhost:3000)。

## 当前处理模式

默认使用 `demo` 处理器，可以完整跑通：

- 批量创建任务
- SQLite 持久化
- SSE 实时进度
- 暂停、继续、取消
- 历史记录和详情
- TXT、SRT、Markdown、JSON 导出

演示处理器不会下载或识别真实视频。真实处理还需要安装并接入：

- FFmpeg
- yt-dlp
- mlx-whisper
- Ollama

代码入口和 TODO 位于 `backend/app/main.py` 的 `Worker.process`。

## 单独启动

```bash
npm run backend
npm run dev
```

后端仅监听 `127.0.0.1:8787`，运行数据默认保存在 `.data/glean.db`。

前端默认连接 `http://127.0.0.1:8787/api`。可通过
`NEXT_PUBLIC_GLEAN_API_URL` 覆盖后端地址。
