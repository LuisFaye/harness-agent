import type { ComponentProps, ComponentPropsWithoutRef } from "react";
import { defaultRemarkPlugins, Streamdown, type Components } from "streamdown";

import { cn } from "@/lib/utils";

import { limitedCodeHighlighter } from "./limited-code-highlighter";

const streamdownComponents = {
  a: SafeLink,
} as NonNullable<ComponentProps<typeof Streamdown>["components"]>;

type RemarkPluginList = NonNullable<ComponentProps<typeof Streamdown>["remarkPlugins"]>;
type RemarkPluginItem = RemarkPluginList[number];

const gfmPlugin = Array.isArray(defaultRemarkPlugins.gfm)
  ? defaultRemarkPlugins.gfm[0]
  : defaultRemarkPlugins.gfm;

const streamdownRemarkPlugins: RemarkPluginList = [
  ...(gfmPlugin ? ([[gfmPlugin, { singleTilde: false }]] as unknown as RemarkPluginItem[]) : []),
  ...Object.entries(defaultRemarkPlugins)
    .filter(([key]) => key !== "gfm")
    .map(([, plugin]) => plugin),
];

interface MessageResponseProps {
  children: string;
  className?: string;
  components?: Components;
  settled?: boolean;
  streaming?: boolean;
}

export function MessageResponse({
  children,
  className,
  components,
  settled = false,
  streaming = false,
}: MessageResponseProps) {
  const mergedComponents = {
    ...streamdownComponents,
    ...components,
  } as Components;
  const streamLifecycle = streaming && !settled;
  return (
    <Streamdown
      className={cn("markdown-content", streamLifecycle && "is-streaming", className)}
      components={mergedComponents}
      dir="auto"
      // 始终保留分块渲染树，流式结果交接为历史消息时不切换整篇 Markdown 的 DOM。
      mode="streaming"
      parseIncompleteMarkdown={streamLifecycle}
      plugins={{ code: limitedCodeHighlighter }}
      remarkPlugins={streamdownRemarkPlugins}
      skipHtml
    >
      {children}
    </Streamdown>
  );
}

function SafeLink({
  href,
  children,
  ...props
}: ComponentPropsWithoutRef<"a">) {
  const external = Boolean(href?.startsWith("http://") || href?.startsWith("https://"));
  return (
    <a
      {...props}
      href={href}
      rel={external ? "noreferrer noopener" : undefined}
      target={external ? "_blank" : undefined}
    >
      {children}
    </a>
  );
}
