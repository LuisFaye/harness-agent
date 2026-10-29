import { PageHeader } from "@/components/layout/page-header";
import { ArrowLeft, Copy, Download, LoaderCircle, MessagesSquare } from "lucide-react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { apiUrl } from "@/lib/api/client";

import { copyReceivedShare, getReceivedShare, listReceivedShares } from "./sharing-api";

export function SharedSessionsPage() {
  const { shareId = "" } = useParams();
  const navigate = useNavigate();
  const shares = useQuery({ queryKey: ["received-shares"], queryFn: listReceivedShares, enabled: !shareId });
  const detail = useQuery({ queryKey: ["received-share", shareId], queryFn: () => getReceivedShare(shareId), enabled: Boolean(shareId) });
  const copy = useMutation({
    mutationFn: () => copyReceivedShare(shareId),
    onSuccess: (response) => navigate(`/agent/${encodeURIComponent(response.session_id)}`),
  });

  if (shareId) {
    if (detail.isLoading) return <PageLoader />;
    if (!detail.data) return <p className="p-6 text-sm text-destructive">无法读取这个分享，它可能已被撤销。</p>;
    const { share, artifacts } = detail.data;
    return (
      <section className="mx-auto w-full max-w-4xl space-y-5 p-4 sm:p-6">
        <header className="flex flex-wrap items-center gap-3 rounded-2xl border border-border/85 bg-card p-4 shadow-2xs">
          <Button aria-label="返回共享会话" onClick={() => navigate("/shared")} size="icon-sm" variant="ghost"><ArrowLeft /></Button>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-lg font-semibold tracking-tight">{share.title}</h1>
            <p className="text-xs text-muted-foreground">{share.owner_display_name} 分享 · 只读</p>
          </div>
          <Button disabled={copy.isPending} onClick={() => copy.mutate()} size="sm">
            {copy.isPending ? <LoaderCircle className="animate-spin" /> : <Copy />}复制并继续
          </Button>
        </header>
        <div className="space-y-3">
          {share.messages.map((message) => (
            <article className={message.role === "user" ? "ml-auto max-w-[85%] rounded-2xl border border-primary/15 bg-primary-soft px-4 py-3 text-foreground shadow-2xs" : "max-w-[92%] rounded-2xl border border-border/85 bg-card px-4 py-3 shadow-2xs"} key={message.id}>
              <p className="mb-1 text-[11px] font-medium text-muted-foreground">{message.role === "user" ? "原用户" : message.role === "assistant" ? "Harness Agent" : "系统"}</p>
              <p className="whitespace-pre-wrap text-sm leading-6">{message.content}</p>
            </article>
          ))}
        </div>
        {artifacts.length ? (
          <div className="rounded-2xl border border-border/85 bg-card p-4.5 shadow-2xs">
            <h2 className="font-semibold tracking-tight">共享产物</h2>
            <div className="mt-3 space-y-2">{artifacts.map((artifact) => (
              <a className="flex items-center justify-between rounded-xl border border-border/75 bg-background/60 px-3.5 py-2.5 text-sm transition-colors hover:border-primary/30 hover:bg-muted/60" href={apiUrl(artifact.download_url)} key={artifact.artifact_id}>
                <span className="truncate font-medium">{artifact.filename}</span><Download className="size-4 text-muted-foreground" />
              </a>
            ))}</div>
          </div>
        ) : null}
      </section>
    );
  }

  return (
    <section className="mx-auto w-full max-w-5xl space-y-6">
      <PageHeader title="共享给我" description="查看伙伴分享的对话，复制到你的空间后继续探索。" icon={MessagesSquare} />
      {shares.isLoading ? <PageLoader /> : (
        <div className="grid gap-3.5 md:grid-cols-2">
          {(shares.data?.items || []).map((share) => (
            <button className="group rounded-2xl border border-border/85 bg-card p-4.5 text-left shadow-2xs transition-all duration-150 hover:-translate-y-0.5 hover:border-primary/35 hover:shadow-md" key={share.id} onClick={() => navigate(`/shared/${share.id}`)} type="button">
              <span className="mb-3 grid size-9 place-items-center rounded-xl border border-primary/15 bg-primary-soft text-primary">
                <MessagesSquare className="size-4.5" />
              </span>
              <h2 className="truncate font-semibold tracking-tight group-hover:text-primary">{share.title}</h2>
              <p className="mt-1 text-xs text-muted-foreground">{share.owner_display_name} · {share.message_count} 条消息</p>
            </button>
          ))}
          {!shares.data?.items.length ? (
            <div className="col-span-full flex min-h-64 flex-col items-center justify-center rounded-2xl border border-dashed border-border/80 bg-card/50 px-6 text-center">
              <span className="mb-3 grid size-12 place-items-center rounded-2xl bg-muted/60 text-muted-foreground">
                <MessagesSquare aria-hidden="true" className="size-6" />
              </span>
              <h2 className="text-base font-semibold tracking-tight">精彩的对话，值得分享</h2>
              <p className="mt-1.5 text-sm text-muted-foreground">伙伴分享给你的会话会出现在这里。</p>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

function PageLoader() {
  return <div className="grid min-h-48 place-items-center"><LoaderCircle aria-label="加载中" className="animate-spin text-muted-foreground" /></div>;
}
