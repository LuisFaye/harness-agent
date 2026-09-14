import { Collapsible } from "@base-ui/react/collapsible";
import { ChevronDown, ExternalLink, SearchCheck } from "lucide-react";
import {
  Children,
  createContext,
  isValidElement,
  useContext,
  useId,
  useMemo,
  useRef,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from "react";
import type { Components } from "streamdown";

import { MessageResponse } from "@/components/ai-elements/message-response";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type {
  CitationBundle,
  CitationChunk,
  CitationSource,
  CitationSupport,
} from "@/lib/stream/schemas";
import { cn } from "@/lib/utils";

interface CitationMessageResponseProps {
  children: string;
  citationBundle: CitationBundle | null;
  className?: string;
  settled?: boolean;
  streaming?: boolean;
}

interface CitationReference {
  number: number;
  source: CitationSource;
  chunks: CitationChunk[];
  support: CitationSupport;
}

const CitationReferencesContext = createContext<Map<string, CitationReference[]>>(new Map());

const CITATION_COMPONENTS: Components = {
  p: CitedParagraph,
  li: CitedListItem,
};

const LEVEL_META: Record<CitationSource["content_level"], { label: string; className: string }> = {
  metadata: {
    label: "仅来源信息",
    className: "border-muted-foreground/20 bg-muted text-muted-foreground",
  },
  snippet: {
    label: "搜索摘要",
    className: "border-warning/25 bg-warning/10 text-warning",
  },
  passage: {
    label: "已读取正文",
    className: "border-success/25 bg-success/10 text-success",
  },
  fulltext: {
    label: "完整内容",
    className: "border-success/25 bg-success/10 text-success",
  },
};

const ROLE_LABELS: Record<CitationSource["source_role"], string> = {
  primary: "一手来源",
  secondary: "二手来源",
  unknown: "来源属性未判定",
};

export function CitationMessageResponse({
  children,
  citationBundle,
  className,
  settled = false,
  streaming = false,
}: CitationMessageResponseProps) {
  const view = useMemo(() => buildCitationView(citationBundle), [citationBundle]);
  const sourceListLabelId = useId();

  return (
    <CitationReferencesContext.Provider value={view.referencesByQuote}>
      <>
        <MessageResponse
          components={CITATION_COMPONENTS}
          settled={settled}
          streaming={streaming}
          {...(className ? { className } : {})}
        >
          {children}
        </MessageResponse>
        {view.orderedSources.length ? (
          <CitationSourceList labelId={sourceListLabelId} sources={view.orderedSources} />
        ) : null}
      </>
    </CitationReferencesContext.Provider>
  );
}

function CitedParagraph({
  children,
  node: _node,
  ...props
}: ComponentPropsWithoutRef<"p"> & { node?: unknown }) {
  const referencesByQuote = useContext(CitationReferencesContext);
  return (
    <p {...props}>
      {children}
      <BlockCitations childrenNode={children} referencesByQuote={referencesByQuote} />
    </p>
  );
}

function CitedListItem({
  children,
  node,
  ...props
}: ComponentPropsWithoutRef<"li"> & { node?: unknown }) {
  const referencesByQuote = useContext(CitationReferencesContext);
  // 宽松列表的 li 在 AST 里会包含 p 子节点，但 React children 里拿不到稳定的 p 标记。
  // 直接读 markdown 节点，避免 li 与内部段落重复挂角标。
  const nestedBlock = listItemHasBlockChild(node);
  return (
    <li {...props}>
      {children}
      {nestedBlock ? null : (
        <BlockCitations childrenNode={children} referencesByQuote={referencesByQuote} />
      )}
    </li>
  );
}

function BlockCitations({
  childrenNode,
  referencesByQuote,
}: {
  childrenNode: ReactNode;
  referencesByQuote: Map<string, CitationReference[]>;
}) {
  const quote = normalizeText(nodeText(childrenNode));
  const references = referencesByQuote.get(quote) ?? [];
  return references.length ? <CitationBadges references={references} /> : null;
}

const LIST_ITEM_BLOCK_TAGS = new Set(["p", "blockquote", "pre", "div", "table", "ul", "ol"]);

function listItemHasBlockChild(node: unknown): boolean {
  if (!node || typeof node !== "object" || !("children" in node)) {
    return false;
  }
  const children = (node as { children?: unknown[] }).children ?? [];
  return children.some((child) => {
    if (!child || typeof child !== "object" || !("type" in child)) {
      return false;
    }
    if (child.type !== "element" || !("tagName" in child)) {
      return false;
    }
    const tagName = String((child as { tagName?: string }).tagName ?? "").toLowerCase();
    return LIST_ITEM_BLOCK_TAGS.has(tagName);
  });
}

function CitationBadges({ references }: { references: CitationReference[] }) {
  return (
    <span
      aria-label="本段引用来源"
      className="ml-1 inline-flex items-center align-middle gap-0.5 whitespace-nowrap"
      role="group"
    >
      {references.map((reference) => (
        <CitationBadge key={`${reference.support.support_id}:${reference.source.source_id}`} reference={reference} />
      ))}
    </span>
  );
}

function CitationBadge({ reference }: { reference: CitationReference }) {
  const { source, number, chunks } = reference;
  const level = LEVEL_META[source.content_level];
  const triggerRef = useRef<HTMLButtonElement>(null);
  return (
    <Popover
      onOpenChange={(open) => {
        if (!open) {
          window.requestAnimationFrame(() => triggerRef.current?.focus());
        }
      }}
    >
      <PopoverTrigger
        aria-label={`查看来源 ${number}：${source.title}`}
        className={cn(
          "relative inline-grid size-6 place-items-center rounded-md border text-[11px] font-semibold leading-none outline-none transition-colors",
          "after:absolute after:-inset-2 after:content-[''] hover:brightness-95 focus-visible:ring-3 focus-visible:ring-ring/35",
          level.className,
        )}
        ref={triggerRef}
      >
        {number}
      </PopoverTrigger>
      <PopoverContent align="center" className="w-[min(380px,calc(100vw-2rem))] p-0" side="top">
        <div className="border-b p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs font-medium text-muted-foreground">
                来源 {number} · {source.domain}
              </p>
              <p className="mt-1 text-sm font-semibold leading-5">{source.title}</p>
            </div>
            <span className={cn("shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium", level.className)}>
              {level.label}
            </span>
          </div>
        </div>
        <div className="space-y-3 p-4">
          {chunks.map((chunk) => (
            <blockquote className="max-h-40 overflow-y-auto rounded-lg bg-muted/65 p-3 text-sm leading-6 text-muted-foreground" key={chunk.chunk_id}>
              {chunk.text}
            </blockquote>
          ))}
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
            <span>{ROLE_LABELS[source.source_role]}</span>
            <time dateTime={source.retrieved_at}>{formatRetrievedAt(source.retrieved_at)}</time>
          </div>
          <a
            className="inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-lg border bg-background px-3 text-sm font-medium transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/35"
            href={source.url}
            rel="noreferrer noopener"
            target="_blank"
          >
            打开原始来源
            <ExternalLink aria-hidden="true" className="size-4" />
          </a>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function CitationSourceList({
  labelId,
  sources,
}: {
  labelId: string;
  sources: Array<{ number: number; source: CitationSource }>;
}) {
  return (
    <Collapsible.Root className="group/sources mt-5 border-t pt-2">
      <Collapsible.Trigger
        className={cn(
          "flex min-h-11 w-full items-center gap-2 rounded-lg px-2 text-left text-sm outline-none transition-colors",
          "hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/35",
        )}
      >
        <SearchCheck aria-hidden="true" className="size-4 shrink-0 text-primary" />
        <span className="min-w-0 flex-1 font-semibold" id={labelId}>本次回答的来源</span>
        <span className="text-xs font-normal text-muted-foreground">{sources.length} 个</span>
        <ChevronDown
          aria-hidden="true"
          className="size-4 shrink-0 text-muted-foreground transition-transform duration-200 group-data-[open]/sources:rotate-180 motion-reduce:transition-none"
        />
      </Collapsible.Trigger>
      <Collapsible.Panel>
        <ol aria-labelledby={labelId} className="grid gap-2 px-2 pb-2 pt-1 sm:grid-cols-2">
          {sources.map(({ number, source }) => {
            const level = LEVEL_META[source.content_level];
            return (
              <li className="min-w-0" key={source.source_id}>
                <a
                  className="flex min-h-11 items-start gap-2 rounded-xl border bg-card p-3 transition-colors hover:bg-muted/45 focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/35"
                  href={source.url}
                  rel="noreferrer noopener"
                  target="_blank"
                >
                  <span className={cn("grid size-6 shrink-0 place-items-center rounded-md border text-[11px] font-semibold", level.className)}>
                    {number}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="line-clamp-2 block text-sm font-medium leading-5">{source.title}</span>
                    <span className="mt-0.5 block truncate text-xs text-muted-foreground">{source.domain} · {level.label}</span>
                  </span>
                </a>
              </li>
            );
          })}
        </ol>
      </Collapsible.Panel>
    </Collapsible.Root>
  );
}

function buildCitationView(bundle: CitationBundle | null) {
  const sourceById = new Map((bundle?.sources ?? []).map((source) => [source.source_id, source]));
  const chunkById = new Map((bundle?.chunks ?? []).map((chunk) => [chunk.chunk_id, chunk]));
  const numberBySource = new Map<string, number>();
  const orderedSources: Array<{ number: number; source: CitationSource }> = [];
  const referencesByQuote = new Map<string, CitationReference[]>();
  const ambiguousQuotes = new Set<string>();
  if (!bundle || bundle.status === "unavailable") {
    return { orderedSources, referencesByQuote };
  }
  for (const support of bundle.supports) {
    if (support.support_status === "unsupported") {
      continue;
    }
    const references: CitationReference[] = [];
    const chunksBySource = new Map<string, CitationChunk[]>();
    for (const chunkId of support.chunk_ids) {
      const chunk = chunkById.get(chunkId);
      if (!chunk || !sourceById.has(chunk.source_id)) {
        continue;
      }
      chunksBySource.set(chunk.source_id, [...(chunksBySource.get(chunk.source_id) ?? []), chunk]);
    }
    for (const [sourceId, chunks] of chunksBySource) {
      const source = sourceById.get(sourceId);
      if (!source) {
        continue;
      }
      let number = numberBySource.get(sourceId);
      if (!number) {
        number = numberBySource.size + 1;
        numberBySource.set(sourceId, number);
        orderedSources.push({ number, source });
      }
      references.push({ number, source, chunks, support });
    }
    if (references.length) {
      const quote = normalizeText(support.answer_quote);
      if (referencesByQuote.has(quote)) {
        // 两个回答块文本完全相同时，仅靠渲染后的文本无法区分它们。此时保留底部
        // 来源汇总但移除行内角标，遵循“允许少引，不允许错引”的产品原则。
        referencesByQuote.delete(quote);
        ambiguousQuotes.add(quote);
      } else if (!ambiguousQuotes.has(quote)) {
        referencesByQuote.set(quote, references);
      }
    }
  }
  return { orderedSources, referencesByQuote };
}

function nodeText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }
  return Children.toArray(node).map((child) => {
    if (typeof child === "string" || typeof child === "number") {
      return String(child);
    }
    if (isValidElement<{ children?: ReactNode }>(child)) {
      return nodeText(child.props.children);
    }
    return "";
  }).join("");
}

function normalizeText(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function formatRetrievedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "检索时间未知";
  }
  return `检索于 ${new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date)}`;
}
