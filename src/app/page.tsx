import { Brand } from "@/components/layout/brand";
import { Button } from "@/components/ui/button";
import { SurfaceGrid } from "@/components/features/surface-grid";

export default function HomePage() {
  return (
    <>
      <header className="container home-header">
        <Brand />
        <small>视频与文章文案提取工具</small>
      </header>
      <main className="home-main">
        <section className="container home-hero">
          <div>
            <span className="eyebrow">
              抖音 · Bilibili · YouTube · 小宇宙 · 微信公众号 · 小红书 · 飞书 · 网页
            </span>
            <h1>把值得反复看的内容，变成随时可用的文字。</h1>
            <p className="lead">
              批量粘贴视频或文章链接，本机自动完成下载、识别、整理与提炼，
              生成结构化总结、逐字稿/正文与精彩金句。无需登录，结果保存在当前
              Mac，可一键复制或导出 TXT、Markdown、JSON。
            </p>
            <div className="hero-actions">
              <Button href="/submit">开始提取文案</Button>
              <Button href="/history" variant="secondary">
                查看历史记录
              </Button>
            </div>
          </div>
          <div aria-label="文案提取结果预览" className="home-demo">
            <div className="home-demo__head">
              <b>正在提炼内容</b>
              <span aria-label="提取流程状态" className="status-dots">
                <i
                  aria-label="链接已识别"
                  data-tip="链接已识别"
                  tabIndex={0}
                />
                <i
                  aria-label="正在进行语音转写"
                  data-tip="正在进行语音转写"
                  tabIndex={0}
                />
                <i
                  aria-label="等待提炼总结与金句"
                  data-tip="等待提炼总结与金句"
                  tabIndex={0}
                />
              </span>
            </div>
            <div className="home-demo__input">
              youtube.com/watch?v=quiet-ideas
            </div>
            <div className="home-demo__result">
              <small>精彩金句 · 03</small>
              <p>“收藏只是把信息留下，转述才是把理解留下。”</p>
            </div>
          </div>
        </section>
        <section className="container surface-section">
          <div className="surface-section__head">
            <h2>从提交到归档，一条清晰的路径。</h2>
            <p>
              四个独立界面覆盖批量处理与回看的完整流程，桌面端与移动端都能顺畅操作。
            </p>
          </div>
          <SurfaceGrid />
        </section>
        <section className="container creator-highlight">
          <div className="creator-highlight__body">
            <span className="eyebrow">博主批量提取 · 新</span>
            <h2>给一个抖音博主主页，收获一整张文案表。</h2>
            <p>
              粘贴博主主页链接（或输入名字），本机自动抓取其视频列表与点赞、评论、收藏、转发等指标，逐条识别文案，再一键汇总写入飞书多维表格，连视频封面链接也一并带上。
            </p>
            <div className="hero-actions">
              <Button href="/creator">试试博主批量提取</Button>
            </div>
          </div>
          <ul className="creator-highlight__points">
            <li>
              <b>抓列表 + 指标</b>
              <span>Playwright + 本机登录态，点赞收藏一网打尽</span>
            </li>
            <li>
              <b>逐条转文案</b>
              <span>复用本机 yt-dlp + mlx-whisper + Ollama 全流程</span>
            </li>
            <li>
              <b>一键写飞书</b>
              <span>自动新建多维表格，含标题、指标、封面与文案</span>
            </li>
          </ul>
        </section>
      </main>
    </>
  );
}
