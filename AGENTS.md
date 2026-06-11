<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes - APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

# 拾句前端开发说明

## 项目介绍

拾句是一个视频文案提取与学习工具的前端原型。项目使用 Next.js App Router、React、TypeScript 和原生 CSS，实现视频链接提交、任务进度、历史记录、文案详情及设计系统预览。

当前页面基于 `/Users/bytedance/Downloads/Web-Prototype` 中的原生 HTML/CSS/JavaScript 迁移。迁移目标是保持原型的视觉、布局、间距、颜色、字体层级和交互状态，同时建立可维护的组件体系。

## 前端目录结构

```text
src/
  app/
    page.tsx                 # 产品首页
    submit/page.tsx          # 新建提取任务
    progress/page.tsx        # 任务进度
    history/page.tsx         # 历史记录
    detail/page.tsx          # 文案详情
    design-system/           # 仅开发环境可访问的设计系统预览
    globals.css              # 全局样式入口
  components/
    ui/                      # Button、Card、Input、Badge、Progress 等基础组件
    layout/                  # Brand、AppShell、PageHero 等布局组件
    feedback/                # Toast 等反馈组件
    features/                # 后续业务级复合组件
  lib/                       # 无 UI 的通用函数
  styles/
    design-system.css        # 颜色、字体、间距、圆角、阴影、动效令牌
    components.css           # 通用组件样式
    pages.css                # 页面布局与业务模式样式
backend/
  app/main.py                # FastAPI、SQLite、Worker、SSE 与导出
  requirements.txt           # Python 依赖
scripts/
  dev.sh                     # 同时启动前后端
```

## 设计系统架构

- 所有设计变量统一定义在 `src/styles/design-system.css`，页面和组件不得重复声明同类颜色、间距、圆角或阴影常量。
- `src/styles/components.css` 只维护跨页面复用的组件和布局原语。
- `src/styles/pages.css` 维护共享页面模式及明确的业务样式，避免在 JSX 中堆叠大量样式。
- JSX 中仅允许为动态数值使用 `style`，例如进度条百分比和设计系统色板预览。
- 设计系统预览地址为 `/design-system`，仅在 `development` 环境开放。

## 组件复用规范

1. 开发页面时，必须优先复用 `src/components` 中的已有组件。
2. 如果已有组件可以通过 `props`、`variant`、`className`、组合或 children 扩展，应优先扩展已有组件，不得重新创建外观或行为相近的组件。
3. 只有现有组件无法表达新的语义、状态或交互时，才新增组件。
4. 新增基础组件放入 `components/ui`；跨页面布局放入 `components/layout`；业务复合组件放入 `components/features`。
5. Button 的视觉状态通过 `variant` 管理；Card 的内边距通过 `panel` 管理；状态文字优先使用 Badge。
6. 页面组件负责数据与状态编排，不应复制 Button、Card、Input、Navigation 等基础实现。

## 开发注意事项

- 保持 TypeScript 严格模式，不使用 `any` 规避类型问题。
- 客户端交互组件必须显式声明 `"use client"`；无交互页面默认使用 Server Component。
- 路由跳转使用 Next.js `Link` 或 `useRouter`，不要使用原生 `location.href`。
- 保持键盘焦点、ARIA 标签、表格语义和移动端导航可用。
- 响应式断点以原型为准：主要使用 `900px`、`820px`、`560px` 和 `430px`。
- 不引入在线字体或构建期网络资源。展示字体使用本地系统衬线字体回退栈，以保证离线构建。
- 修改视觉规范时，先更新设计令牌或共享组件，再检查所有页面和 `/design-system`。
- 提交前至少运行 `npm run lint` 和 `npm run build`。
- 本地完整开发使用 `npm run dev:all`，不要只启动 Next.js 后误判 API 不可用。
- 前端统一通过 `src/lib/api.ts` 调用 `127.0.0.1:8787/api`，可用 `NEXT_PUBLIC_SHIJU_API_URL` 覆盖；`/local-api` rewrite 保留给同源部署场景。
- 后端 schema、状态机和 API 字段需与 `docs/local-video-copy-backend-technical-design.md` 保持一致。
- 当前默认 `SHIJU_PROCESSOR_MODE=real`。真实媒体处理走 `backend/app/pipeline.py` 与 `backend/app/article.py`；当依赖缺失或无法识别时，必须以失败任务的形式如实暴露错误信息，不得伪造识别结果。`SHIJU_PROCESSOR_MODE=demo`（即 `npm run dev:demo`）仅用于无依赖环境下跑通 UI 流程，结果必须明确标记为占位、不得呈现为真实识别。
