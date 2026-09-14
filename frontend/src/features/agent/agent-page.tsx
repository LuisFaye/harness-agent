import {
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { AgentMark } from "@/components/layout/agent-mark";
import { SidebarSessionPortals } from "@/components/layout/sidebar-session-slot";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/features/auth/auth-provider";
import { cn } from "@/lib/utils";

import { AgentComposer } from "./agent-composer";
import { AgentSessionActions } from "./agent-session-actions";
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
  const [favoriteSessionIds, setFavoriteSessionIds] = useState<string[]>(
    () => readFavoriteSessionIds(userId),
  );
  useEffect(() => setFavoriteSessionIds(readFavoriteSessionIds(userId)), [userId]);
  const workspace = useAgentWorkspace(sessionId, sessionKeyword);
  const activeSession = workspace.sessions.find((item) => item.session_id === sessionId);
  const sessionTitle = activeSession?.title || workspace.optimisticTitle || "新对话";

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
      <div className="flex h-full min-h-0 min-w-0 flex-col bg-background">
        <header className="flex min-h-14 shrink-0 items-center gap-3 border-b border-border bg-background px-3 sm:px-4">
          <AgentMark />
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-[15px] font-semibold" id="agent-page-title">
              {sessionTitle}
            </h1>
            <p className="truncate text-xs text-muted-foreground">
              {workspace.isRunning ? "任务进行中 · 回答将实时呈现" : "你的 AI 工作伙伴"}
            </p>
          </div>
          <AgentSessionActions
            hasMessages={workspace.messages.length > 0}
            isFavorite={favoriteSessionIds.includes(sessionId)}
            messages={workspace.messages}
            onToggleFavorite={() => toggleFavoriteSession(sessionId)}
            sessionId={sessionId}
            title={sessionTitle}
          />
        </header>

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
        <AgentComposer
          suggestedPrompt={suggestedPrompt}
          contextObservation={workspace.contextObservation}
          isRunning={workspace.isRunning}
          isUploading={workspace.isUploading}
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
      </div>
    </section>
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
        "fixed right-4 bottom-4 z-50 max-w-[calc(100vw-2rem)] rounded-xl border border-border bg-card p-4 shadow-lg transition-[opacity,transform] duration-200 motion-reduce:transition-none",
        visible ? "translate-y-0 opacity-100" : "translate-y-2 opacity-0",
      )}
      role="status"
    >
      <div className="flex items-start gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-foreground">任务已完成</p>
          <p className="mt-0.5 truncate text-sm text-muted-foreground">
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
            size="icon"
            variant="ghost"
          >
            <X aria-hidden="true" className="size-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
