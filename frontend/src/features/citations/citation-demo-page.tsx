import { useState } from "react";
import {
  ArrowLeft,
  Check,
  CheckCircle2,
  ExternalLink,
  FileText,
  Info,
  Search,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { Link } from "react-router-dom";

import { ThemeToggle } from "@/components/layout/theme-toggle";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

type CitationMode = "paragraph" | "sentence";
type EvidenceLevel = "primary" | "passage" | "snippet";

interface DemoSource {
  id: number;
  title: string;
  domain: string;
  url: string;
  publisher: string;
  retrievedAt: string;
  quote: string;
  level: EvidenceLevel;
  protocol: string;
  takeaway: string;
}

const SOURCES = [
  {
    id: 1,
    title: "Responses API：URL citation annotations",
    domain: "developers.openai.com",
    url: "https://developers.openai.com/api/reference/typescript/resources/beta/subresources/responses/methods/create",
    publisher: "OpenAI",
    retrievedAt: "刚刚",
    quote: "输出文本可以携带 URL citation 注解，并记录引用在正文中的起止位置、标题和 URL。",
    level: "primary",
    protocol: "文本位置注解",
    takeaway: "正文与引用分开返回，通过 start/end index 精确挂载。",
  },
  {
    id: 2,
    title: "Citations：把回答与源文档关联",
    domain: "docs.anthropic.com",
    url: "https://platform.claude.com/docs/en/build-with-claude/citations",
    publisher: "Anthropic",
    retrievedAt: "刚刚",
    quote: "引用信息与生成文本分开返回，应用可以用结构化位置数据渲染自己的引用交互。",
    level: "primary",
    protocol: "文本块 + 源定位",
    takeaway: "保留 cited_text，并按页码、字符或内容块定位原文。",
  },
  {
    id: 3,
    title: "Grounding with Google Search",
    domain: "cloud.google.com",
    url: "https://cloud.google.com/vertex-ai/generative-ai/docs/grounding/grounding-with-google-search",
    publisher: "Google Cloud",
    retrievedAt: "刚刚",
    quote: "Grounding metadata 将来源块与回答片段的支持关系分开表达，并可携带支持分数。",
    level: "primary",
    protocol: "Chunks + Supports",
    takeaway: "把证据块和回答片段的支持关系建模为两个独立对象。",
  },
  {
    id: 4,
    title: "Streaming Citation Parsing",
    domain: "docs.perplexity.ai",
    url: "https://docs.perplexity.ai/docs/cookbook/articles/streaming-citations/README",
    publisher: "Perplexity",
    retrievedAt: "刚刚",
    quote: "搜索结果先于正文到达，正文中的编号引用通过结果 ID 映射到对应标题、URL 和摘要。",
    level: "primary",
    protocol: "结果 ID + 行内编号",
    takeaway: "先建立来源表，再流式渲染正文中的编号引用。",
  },
  {
    id: 5,
    title: "搜索结果摘要（演示弱证据）",
    domain: "search.example",
    url: "https://example.com/search-result",
    publisher: "示例搜索服务",
    retrievedAt: "刚刚",
    quote: "这是搜索结果中的摘要，尚未读取对应网页正文，因此只能作为较弱的发现线索。",
    level: "snippet",
    protocol: "只有搜索摘要",
    takeaway: "可以展示来源，但不能伪装成已读取并核验网页正文。",
  },
] as const satisfies readonly DemoSource[];

const LEVEL_META: Record<EvidenceLevel, { label: string; description: string; className: string }> = {
  primary: {
    label: "一手来源",
    description: "已读取官方或原始正文",
    className: "border-success/25 bg-success/10 text-success",
  },
  passage: {
    label: "正文片段",
    description: "已读取并定位到支持片段",
    className: "border-primary/20 bg-primary-soft text-primary",
  },
  snippet: {
    label: "搜索摘要",
    description: "尚未读取网页正文，证据较弱",
    className: "border-warning/25 bg-warning/10 text-warning",
  },
};

export function CitationDemoPage() {
  const [mode, setMode] = useState<CitationMode>("paragraph");
  const [activeSourceId, setActiveSourceId] = useState(1);

  return (
    <div className="min-h-svh bg-background text-foreground">
      <a className="skip-link" href="#citation-demo-content">跳到演示正文</a>
      <header className="sticky top-0 z-30 border-b bg-background/92 backdrop-blur-md">
        <div className="mx-auto flex h-16 w-full max-w-6xl items-center justify-between gap-3 px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <Link
              aria-label="返回 SB Agent"
              className="grid size-11 shrink-0 place-items-center rounded-xl text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/35"
              to="/agent"
            >
              <ArrowLeft aria-hidden="true" className="size-5" />
            </Link>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h1 className="truncate text-base font-semibold sm:text-lg">回答溯源 Demo</h1>
                <span className="hidden rounded-full border bg-card px-2 py-0.5 text-[11px] font-medium text-muted-foreground sm:inline-flex">
                  仅前端演示
                </span>
              </div>
              <p className="truncate text-xs text-muted-foreground">点击段尾角标查看证据与来源</p>
            </div>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6 sm:py-10" id="citation-demo-content">
        <section aria-labelledby="display-mode-title" className="mb-6 flex flex-col gap-4 rounded-2xl border bg-card p-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-sm font-semibold" id="display-mode-title">引用显示粒度</h2>
            <p className="mt-1 text-xs text-muted-foreground">切换后可以直观看到逐段与逐句的阅读差异。</p>
          </div>
          <div aria-label="选择引用显示粒度" className="grid grid-cols-2 rounded-xl bg-muted p-1" role="group">
            <ModeButton active={mode === "paragraph"} onClick={() => setMode("paragraph")}>
              段落级（推荐）
            </ModeButton>
            <ModeButton active={mode === "sentence"} onClick={() => setMode("sentence")}>
              逐句引用
            </ModeButton>
          </div>
        </section>

        <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
          <section aria-labelledby="answer-preview-title" className="overflow-hidden rounded-2xl border bg-card">
            <div className="flex items-center justify-between gap-3 border-b bg-muted/35 px-5 py-4 sm:px-7">
              <div className="flex items-center gap-3">
                <span className="grid size-9 place-items-center rounded-xl bg-primary-soft text-primary">
                  <Sparkles aria-hidden="true" className="size-4" />
                </span>
                <div>
                  <h2 className="text-sm font-semibold" id="answer-preview-title">SB Agent</h2>
                  <p className="text-xs text-muted-foreground">已关联 4 个官方来源</p>
                </div>
              </div>
              <span className="inline-flex items-center gap-1.5 rounded-full border border-success/25 bg-success/10 px-2.5 py-1 text-xs font-medium text-success">
                <CheckCircle2 aria-hidden="true" className="size-3.5" />
                引用结构完整
              </span>
            </div>

            <div className="px-5 py-6 sm:px-8 sm:py-8">
              <div className="mb-7 flex justify-end">
                <p className="max-w-[82%] rounded-2xl rounded-br-md border border-primary/15 bg-primary-soft px-4 py-2.5 text-sm">
                  主流 AI 产品的回答引用，前端可以怎么展示？
                </p>
              </div>

              <article className="space-y-5 text-[15px] leading-7 sm:text-base">
                {mode === "paragraph" ? (
                  <ParagraphAnswer onSourceActive={setActiveSourceId} />
                ) : (
                  <SentenceAnswer onSourceActive={setActiveSourceId} />
                )}
              </article>

              <div className="mt-7 flex items-start gap-2 rounded-xl border border-primary/15 bg-primary-soft/65 p-3 text-xs text-muted-foreground">
                <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-primary" />
                <p>引用角标只表达“这段内容由哪些证据支持”。点击后仍需看到原文片段、证据等级和真实来源，才能形成完整溯源。</p>
              </div>
            </div>
          </section>

          <aside aria-labelledby="source-panel-title" className="rounded-2xl border bg-card lg:sticky lg:top-22">
            <div className="border-b px-5 py-4">
              <div className="flex items-center gap-2">
                <Search aria-hidden="true" className="size-4 text-primary" />
                <h2 className="font-semibold" id="source-panel-title">本次回答的来源</h2>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">选择角标后，对应来源会在这里高亮。</p>
            </div>
            <ol className="space-y-2 p-3">
              {SOURCES.slice(0, 4).map((source) => (
                <SourceListItem
                  active={activeSourceId === source.id}
                  key={source.id}
                  onClick={() => setActiveSourceId(source.id)}
                  source={source}
                />
              ))}
            </ol>
          </aside>
        </div>

        <section aria-labelledby="weak-evidence-title" className="mt-6 rounded-2xl border border-warning/20 bg-card p-5 sm:p-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-warning/10 text-warning">
                <FileText aria-hidden="true" className="size-5" />
              </span>
              <div>
                <h2 className="font-semibold" id="weak-evidence-title">弱证据也要明确告诉用户</h2>
                <p className="mt-1 text-sm text-muted-foreground">只有搜索摘要、没有读取正文时，角标仍可展示，但必须降低证据等级。</p>
              </div>
            </div>
            <CitationBadge onActive={setActiveSourceId} source={SOURCES[4]} />
          </div>
        </section>

        <VendorPatternSection />
      </main>
    </div>
  );
}

function ParagraphAnswer({ onSourceActive }: { onSourceActive: (id: number) => void }) {
  return (
    <>
      <p>
        OpenAI 使用正文位置注解，Anthropic 使用带来源定位的文本块；两者都没有把“让模型自己写 URL”当成正式引用协议。
        <CitationGroup onActive={onSourceActive} sourceIds={[1, 2]} />
      </p>
      <p>
        Google 将证据块与回答片段的支持关系分开；Perplexity 则先返回搜索结果，再让正文编号映射结果 ID。这两种方式都保证来源表由服务端提供。
        <CitationGroup onActive={onSourceActive} sourceIds={[3, 4]} />
      </p>
      <p>
        SB Agent 可以采用混合方案：底层按文本片段精确绑定，普通聊天默认在段尾合并展示；研究和高风险场景再切换为逐句引用。
        <CitationGroup onActive={onSourceActive} sourceIds={[1, 2, 3, 4]} />
      </p>
    </>
  );
}

function SentenceAnswer({ onSourceActive }: { onSourceActive: (id: number) => void }) {
  return (
    <>
      <p>
        OpenAI 使用正文位置注解。<CitationGroup onActive={onSourceActive} sourceIds={[1]} />
        Anthropic 在文本块上返回引用和源文档定位。<CitationGroup onActive={onSourceActive} sourceIds={[2]} />
      </p>
      <p>
        Google 把来源块与回答支持关系分开。<CitationGroup onActive={onSourceActive} sourceIds={[3]} />
        Perplexity 用正文编号映射预先返回的搜索结果。<CitationGroup onActive={onSourceActive} sourceIds={[4]} />
      </p>
      <p>
        因此 SB Agent 的内部协议应保留精确文本片段，前端再按场景选择逐段或逐句展示。<CitationGroup onActive={onSourceActive} sourceIds={[1, 2, 3]} />
        流式过程中应先建立来源映射，不能让模型自行生成 URL。<CitationGroup onActive={onSourceActive} sourceIds={[4]} />
      </p>
    </>
  );
}

function CitationGroup({ sourceIds, onActive }: { sourceIds: number[]; onActive: (id: number) => void }) {
  return (
    <span className="ml-1 inline-flex translate-y-0.5 gap-0.5 whitespace-nowrap" role="group" aria-label="本段引用来源">
      {sourceIds.map((id) => {
        const source = SOURCES.find((item) => item.id === id);
        return source ? <CitationBadge key={source.id} onActive={onActive} source={source} /> : null;
      })}
    </span>
  );
}

function CitationBadge({ source, onActive }: { source: DemoSource; onActive: (id: number) => void }) {
  const level = LEVEL_META[source.level];
  return (
    <Popover onOpenChange={(open) => open && onActive(source.id)}>
      <PopoverTrigger
        aria-label={`查看来源 ${source.id}：${source.title}`}
        className={cn(
          "inline-grid size-6 place-items-center rounded-md border text-[11px] font-semibold leading-none outline-none transition-colors",
          "focus-visible:ring-3 focus-visible:ring-ring/35",
          level.className,
        )}
      >
        {source.id}
      </PopoverTrigger>
      <PopoverContent align="center" className="w-[min(360px,calc(100vw-2rem))] p-0" side="top">
        <div className="border-b p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs font-medium text-muted-foreground">来源 {source.id} · {source.publisher}</p>
              <h3 className="mt-1 text-sm font-semibold leading-5">{source.title}</h3>
            </div>
            <span className={cn("shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium", level.className)}>{level.label}</span>
          </div>
        </div>
        <div className="space-y-3 p-4">
          <div className="rounded-lg bg-muted/65 p-3">
            <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">官方协议要点</p>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">{source.quote}</p>
          </div>
          <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
            <span className="truncate">{source.domain}</span>
            <span className="shrink-0">读取于{source.retrievedAt}</span>
          </div>
          <a className="inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-lg border bg-background px-3 text-sm font-medium transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/35" href={source.url} rel="noreferrer noopener" target="_blank">
            打开原始来源
            <ExternalLink aria-hidden="true" className="size-4" />
          </a>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function SourceListItem({ source, active, onClick }: { source: DemoSource; active: boolean; onClick: () => void }) {
  const level = LEVEL_META[source.level];
  return (
    <li>
      <button
        aria-pressed={active}
        className={cn(
          "w-full rounded-xl border p-3 text-left outline-none transition-colors focus-visible:ring-3 focus-visible:ring-ring/35",
          active ? "border-primary/30 bg-primary-soft" : "border-transparent hover:border-border hover:bg-muted/55",
        )}
        id={`citation-source-${source.id}`}
        onClick={onClick}
        type="button"
      >
        <div className="flex items-start gap-3">
          <span className={cn("grid size-7 shrink-0 place-items-center rounded-lg border text-xs font-semibold", level.className)}>{source.id}</span>
          <span className="min-w-0 flex-1">
            <span className="line-clamp-2 block text-sm font-medium leading-5">{source.title}</span>
            <span className="mt-1 block text-[11px] font-medium text-primary">{source.protocol}</span>
            <span className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
              {source.level === "primary" ? <ShieldCheck aria-hidden="true" className="size-3.5" /> : <FileText aria-hidden="true" className="size-3.5" />}
              {source.domain}
            </span>
          </span>
          {active ? <Check aria-hidden="true" className="mt-1 size-4 shrink-0 text-primary" /> : null}
        </div>
      </button>
    </li>
  );
}

function VendorPatternSection() {
  return (
    <section aria-labelledby="vendor-pattern-title" className="mt-6 rounded-2xl border bg-card p-5 sm:p-6">
      <div className="max-w-2xl">
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-primary">官方协议对标</p>
        <h2 className="mt-1 text-lg font-semibold" id="vendor-pattern-title">Demo 采用了哪些主流做法</h2>
        <p className="mt-1 text-sm text-muted-foreground">这些是接口建模的共同点，不代表四家厂商的产品界面完全相同。</p>
      </div>
      <div className="mt-5 grid gap-3 sm:grid-cols-2">
        {SOURCES.slice(0, 4).map((source) => (
          <a
            className="group rounded-xl border p-4 transition-colors hover:border-primary/30 hover:bg-primary-soft/45 focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/35"
            href={source.url}
            key={source.id}
            rel="noreferrer noopener"
            target="_blank"
          >
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm font-semibold">{source.publisher}</span>
              <ExternalLink aria-hidden="true" className="size-4 text-muted-foreground transition-colors group-hover:text-primary" />
            </div>
            <p className="mt-2 text-xs font-medium text-primary">{source.protocol}</p>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">{source.takeaway}</p>
          </a>
        ))}
      </div>
    </section>
  );
}

function ModeButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: string }) {
  return (
    <Button
      aria-pressed={active}
      className={cn("h-9 px-3 text-xs sm:text-sm", active && "bg-card shadow-sm hover:bg-card")}
      onClick={onClick}
      size="sm"
      type="button"
      variant={active ? "secondary" : "ghost"}
    >
      {children}
    </Button>
  );
}
