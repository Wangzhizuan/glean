import { Brand } from "@/components/layout/brand";
import { Button } from "@/components/ui/button";

const surfaces = [
  {
    href: "/submit",
    number: "01",
    title: "提交链接",
    description: "混合粘贴 1–10 条视频或文章链接，选择需要生成的内容。",
    action: "打开工作台 →",
  },
  {
    href: "/progress",
    number: "02",
    title: "任务进度",
    description: "逐条查看识别、总结与金句提炼状态，完成即查看。",
    action: "查看任务队列 →",
  },
  {
    href: "/detail",
    number: "03",
    title: "文案详情",
    description: "在总结、逐字稿与金句间切换，复制整篇或单独导出。",
    action: "查看示例文案 →",
  },
  {
    href: "/history",
    number: "04",
    title: "历史与下载",
    description: "筛选过往记录，勾选多条文案后批量下载保存。",
    action: "打开资料库 →",
  },
];

export default function HomePage() {
  return (
    <>
      <header className="container home-header">
        <Brand />
        <small>视频文案提取与学习工具</small>
      </header>
      <main className="home-main">
        <section className="container home-hero">
          <div>
            <span className="eyebrow">抖音 · Bilibili · YouTube</span>
            <h1>让视频里的好内容，真正留下来。</h1>
            <p className="lead">
              批量粘贴视频链接，自动生成逐字稿、结构化总结和精彩金句。无需登录，完成后可一键复制或导出
              TXT、Word。
            </p>
            <div className="hero-actions">
              <Button href="/submit">开始提取视频文案</Button>
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
            <h2>从提交到下载，一条清晰的路径。</h2>
            <p>
              四个独立界面覆盖批量处理的完整流程，移动端与桌面端都能顺畅操作。
            </p>
          </div>
          <div className="surface-grid">
            {surfaces.map((surface) => (
              <a
                className="surface-card"
                href={surface.href}
                key={surface.href}
              >
                <span className="surface-card__number">{surface.number}</span>
                <h3>{surface.title}</h3>
                <p>{surface.description}</p>
                <span>{surface.action}</span>
              </a>
            ))}
          </div>
        </section>
      </main>
    </>
  );
}
