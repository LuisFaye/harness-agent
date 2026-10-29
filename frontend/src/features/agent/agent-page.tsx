import {
  Download,
  FileText,
  PanelRightClose,
  PanelRightOpen,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { SidebarSessionPortals } from "@/components/layout/sidebar-session-slot";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/features/auth/auth-provider";
import { apiUrl } from "@/lib/api/client";
import type { Artifact } from "@/lib/api/schemas";
import { cn } from "@/lib/utils";

import { AgentComposer } from "./agent-composer";
import { AgentSessionActions } from "./agent-session-actions";
import { ConversationWelcome } from "./conversation-welcome";
import { MessageScroller } from "./message-scroller";
import { createSessionId } from "./session-id";
import { SessionPanel } from "./session-panel";
import {
  readFavoriteSessionIds,
  writeFavoriteSessionIds,
} from "./session-favorites";
import { useAgentWorkspace } from "./use-agent-workspace";

export function AgentPage() {
  const { user } = useAuth();
  const userId = user!.id;
  const { sessionId: routeSessionId } = useParams();
  const navigate = useNavigate();
  const generatedSessionId = useMemo(createSessionId, []);
  const sessionId = routeSessionId || generatedSessionId;
  const [suggestedPrompt, setSuggestedPrompt] = useState<{ text: string; revision: number } | null>(null);
  const [sessionKeyword, setSessionKeyword] = useState("");
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [favoriteSessionIds, setFavoriteSessionIds] = useState<string[]>(
    () => readFavoriteSessionIds(userId),
  );
  useEffect(() => setFavoriteSessionIds(readFavoriteSessionIds(userId)), [userId]);
  const workspace = useAgentWorkspace(sessionId, sessionKeyword);
  const activeSession = workspace.sessions.find((item) => item.session_id === sessionId);
  const isEmptyWelcome = !workspace.messagesLoading
    && !workspace.detailError
    && workspace.messages.length === 0
    && !workspace.projection;
  const sessionTitle = activeSession?.title || workspace.optimisticTitle || "新对话";
  const showInspector = inspectorOpen && !isEmptyWelcome;

  const handleCompletionView = () => {
    const targetSessionId = workspace.completionNotice?.sessionId;
    if (!targetSessionId) {
      return;
    }
    workspace.markSessionRead(targetSessionId);
    selectSession(targetSessionId);
  };

  function selectSession(nextSessionId: string) {
    navigate(`/agent/${encodeURIComponent(nextSessionId)}`);
  }

  async function sendQuery(query: string) {
    if (!routeSessionId) {
      navigate(`/agent/${encodeURIComponent(sessionId)}`, { replace: true });
    }
    await workspace.sendQuery(query);
  }

  function toggleFavoriteSession(targetSessionId: string): boolean {
    const next = favoriteSessionIds.includes(targetSessionId)
      ? favoriteSessionIds.filter((item) => item !== targetSessionId)
      : [...favoriteSessionIds, targetSessionId];
    setFavoriteSessionIds(next);
    return writeFavoriteSessionIds(userId, next);
  }

  const composerElement = (
    <AgentComposer
      variant={isEmptyWelcome ? "welcome" : "dock"}
      suggestedPrompt={suggestedPrompt}
      contextObservation={workspace.contextObservation}
      isRunning={workspace.isRunning}
      isUploading={workspace.isUploading}
      isStopping={workspace.isStopping}
      uploadNotice={workspace.uploadNotice}
      onRetryAttachment={workspace.retryAttachment}
      onCancelUpload={workspace.cancelAttachmentUpload}
      onQueuedEditingChange={workspace.setQueuedEditing}
      latestRunUsage={workspace.latestRunUsage}
      queuedMessage={workspace.queuedMessage}
      onCancelQueued={workspace.cancelQueuedMessage}
      onEditQueued={workspace.editQueuedMessage}
      onRemoveAttachment={workspace.removePendingAttachment}
      onSend={sendQuery}
      onStop={workspace.stopRun}
      onUpload={workspace.uploadFiles}
      pendingAttachments={workspace.pendingAttachments}
      sessionId={sessionId}
    />
  );

  return (
    <section
      aria-labelledby="agent-page-title"
      className="conversation-workspace flex h-full min-h-0 flex-col"
    >
      <SidebarSessionPortals
        render={() => (
          <SessionPanel
            activeSessionId={sessionId}
            className="h-full"
            completedSessionIds={workspace.completedSessionIds}
            error={workspace.sessionsError}
            favoriteSessionIds={favoriteSessionIds}
            hasMore={workspace.hasNextSessionsPage}
            keyword={sessionKeyword}
            loading={workspace.sessionsLoading}
            loadingMore={workspace.isFetchingNextSessionsPage}
            onKeywordChange={setSessionKeyword}
            onLoadMore={() => void workspace.loadNextSessionsPage()}
            onSelect={selectSession}
            runningSessionIds={workspace.activeSessionIds}
            sessions={workspace.sessions}
            timeFilter={workspace.timeFilter}
            totalSessions={workspace.totalSessions}
            onTimeFilterChange={workspace.setTimeFilter}
          />
        )}
      />
      {workspace.completionNotice ? (
        <CompletionToast
          notice={workspace.completionNotice}
          onClose={workspace.dismissCompletionNotice}
          onView={handleCompletionView}
        />
      ) : null}
      <div className="flex h-full min-h-0 min-w-0 flex-1 overflow-hidden bg-transparent">
        <div className="flex h-full min-h-0 min-w-0 flex-1 flex-col bg-transparent">
          <header className="conversation-header sticky top-0 z-20 flex min-h-13 shrink-0 items-center gap-2.5">
            <div className="flex min-w-0 flex-1 items-center gap-2.5">
              <h1 className="truncate text-sm font-semibold tracking-tight text-foreground" id="agent-page-title">
                {sessionTitle}
              </h1>
              {workspace.isRunning ? (
                <p
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-primary/20 bg-primary-soft px-2.5 py-0.5 text-[11px] font-medium text-primary"
                  role="status"
                >
                  <span aria-hidden="true" className="size-1.5 animate-pulse rounded-full bg-primary" />
                  {workspace.isStopping ? "正在停止任务…" : "正在处理"}
                </p>
              ) : null}
            </div>
            <div className="flex items-center gap-1">
              <AgentSessionActions
                hasMessages={workspace.messages.length > 0}
                isFavorite={favoriteSessionIds.includes(sessionId)}
                messages={workspace.messages}
                onToggleFavorite={() => toggleFavoriteSession(sessionId)}
                sessionId={sessionId}
                title={sessionTitle}
              />
              {!isEmptyWelcome && workspace.artifacts.length > 0 ? (
                <Button
                  aria-expanded={inspectorOpen}
                  aria-label={inspectorOpen ? "收起产物栏" : "展开产物栏"}
                  className="hidden lg:inline-flex"
                  onClick={() => setInspectorOpen((value) => !value)}
                  size="icon-sm"
                  title={inspectorOpen ? "收起产物栏" : "展开产物栏"}
                  variant="ghost"
                >
                  {inspectorOpen ? <PanelRightClose aria-hidden="true" /> : <PanelRightOpen aria-hidden="true" />}
                </Button>
              ) : null}
            </div>
          </header>

          {isEmptyWelcome ? (
            <div className="flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-contain">
              <ConversationWelcome
                onSelect={(text) => setSuggestedPrompt({ text, revision: Date.now() })}
              >
                {composerElement}
              </ConversationWelcome>
            </div>
          ) : (
            <>
              <MessageScroller
                key={sessionId}
                artifacts={workspace.artifacts}
                canRegenerate={workspace.canRegenerateLastAnswer}
                error={workspace.detailError}
                errorMessage={workspace.errorMessage}
                isRunning={workspace.isRunning}
                loading={workspace.messagesLoading}
                messages={workspace.messages}
                sessionAttachments={workspace.sessionAttachments}
                onCancelConfirmation={() => void workspace.cancelConfirmation()}
                onConfirm={() => void workspace.confirmRun()}
                onDismissError={workspace.clearError}
                onDatabaseConnect={workspace.connectDatabase}
                onRegenerate={workspace.regenerateLastAnswer}
                onSelectPrompt={(text) => setSuggestedPrompt({ text, revision: Date.now() })}
                onRetry={() => void workspace.retryLastQuery()}
                projection={workspace.projection}
              />
              {composerElement}
            </>
          )}
        </div>

        {showInspector ? (
          <LiveInspectorDrawer
            artifacts={workspace.artifacts}
            onClose={() => setInspectorOpen(false)}
          />
        ) : null}
      </div>
    </section>
  );
}

function LiveInspectorDrawer({
  artifacts,
  onClose,
}: {
  artifacts: Artifact[];
  onClose: () => void;
}) {
  return (
    <aside
      aria-label="会话产物"
      className="hidden h-full w-[240px] shrink-0 flex-col border-l border-border/60 bg-card/40 p-3.5 backdrop-blur-md lg:flex"
    >
      <div className="flex items-center justify-between gap-2">
        <strong className="text-xs font-semibold tracking-tight text-foreground">
          会话产物 ({artifacts.length})
        </strong>
        <Button aria-label="关闭产物面板" onClick={onClose} size="icon-xs" variant="ghost">
          <X aria-hidden="true" className="size-3.5" />
        </Button>
      </div>

      <div className="mt-2.5 min-h-0 flex-1 space-y-1.5 overflow-y-auto">
        {artifacts.map((artifact) => (
          <a
            className="flex items-center justify-between gap-2 rounded-lg border border-border/70 bg-background/70 p-2 text-xs transition-colors hover:border-primary/40 hover:text-primary"
            href={apiUrl(artifact.download_url)}
            key={artifact.artifact_id}
            rel="noreferrer"
            target="_blank"
          >
            <span className="flex min-w-0 items-center gap-2">
              <FileText aria-hidden="true" className="size-3.5 shrink-0 text-primary" />
              <span className="truncate font-medium">{artifact.filename}</span>
            </span>
            <Download aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
          </a>
        ))}
      </div>
    </aside>
  );
}

function CompletionToast({
  notice,
  onClose,
  onView,
}: {
  notice: { sessionId: string; title: string };
  onClose: () => void;
  onView: () => void;
}) {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => setVisible(true));
    const timer = window.setTimeout(onClose, 5000);
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(timer);
    };
  }, [notice.sessionId, onClose]);

  return (
    <div
      aria-live="polite"
      className={cn(
        "fixed right-4 bottom-4 z-50 max-w-[calc(100vw-2rem)] rounded-2xl border border-border bg-card/95 p-4 shadow-xl backdrop-blur-md transition-[opacity,transform] duration-200 motion-reduce:transition-none",
        visible ? "translate-y-0 opacity-100" : "translate-y-2 opacity-0",
      )}
      role="status"
    >
      <div className="flex items-start gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-foreground">任务已完成</p>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {notice.title || "新对话"}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button onClick={onView} size="sm">
            查看
          </Button>
          <Button
            aria-label="关闭通知"
            onClick={onClose}
            size="icon-sm"
            variant="ghost"
          >
            <X aria-hidden="true" className="size-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
