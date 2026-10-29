import { LogOut, Menu, PanelLeftClose, PanelLeftOpen, Plus, UserRound } from "lucide-react";
import { useEffect, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { AgentMark } from "@/components/layout/agent-mark";
import { AppNavigation } from "@/components/layout/app-navigation";
import { SidebarSessionSlotsProvider } from "@/components/layout/sidebar-session-slot";
import { ThemeToggle } from "@/components/layout/theme-toggle";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { createSessionId } from "@/features/agent/session-id";
import { useAuth } from "@/features/auth/auth-provider";
import { cn } from "@/lib/utils";

const SIDEBAR_WIDTH_CLASS = "w-[236px]";

export function AppShell() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try { return window.localStorage.getItem("sb-agent-sidebar-collapsed") === "true"; }
    catch { return false; }
  });
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);
  const [desktopSessionSlot, setDesktopSessionSlot] = useState<HTMLDivElement | null>(null);
  const [mobileSessionSlot, setMobileSessionSlot] = useState<HTMLDivElement | null>(null);
  const location = useLocation();
  const isAgentRoute = location.pathname === "/agent"
    || location.pathname.startsWith("/agent/");
  const isWorkspaceRoute = isAgentRoute
    || location.pathname === "/runs"
    || location.pathname.startsWith("/runs/");

  useEffect(() => {
    try { window.localStorage.setItem("sb-agent-sidebar-collapsed", String(sidebarCollapsed)); }
    catch { /* 偏好持久化不可用时保留本次页面状态。 */ }
  }, [sidebarCollapsed]);

  useEffect(() => {
    setMobileNavigationOpen(false);
  }, [location.pathname]);

  return (
    <SidebarSessionSlotsProvider
      value={{ desktop: desktopSessionSlot, mobile: mobileSessionSlot }}
    >
      <div className="app-frame flex h-dvh min-h-0 flex-col overflow-hidden bg-transparent lg:p-3">
        <a className="skip-link" href="#workspace-main">跳到主要内容</a>

        <header className={cn("z-30 flex shrink-0 items-center lg:hidden", isAgentRoute
          ? "absolute left-2 top-2"
          : "liquid-glass mx-2 mt-2 h-12 justify-between rounded-2xl px-3.5")}>
          <Dialog open={mobileNavigationOpen} onOpenChange={setMobileNavigationOpen}>
            <DialogTrigger
              render={
                <Button
                  aria-label="打开工作台导航"
                  size="icon-sm"
                  variant="ghost"
                />
              }
            >
              <Menu aria-hidden="true" />
            </DialogTrigger>
            <DialogContent
              className="liquid-glass top-2 left-2 flex h-[calc(100dvh-16px)] max-h-[calc(100dvh-16px)] max-w-[18.5rem] translate-x-0 translate-y-0 flex-col content-start rounded-[22px] p-0 sm:max-w-[18.5rem]"
            >
              <DialogHeader className="sr-only">
                <DialogTitle>工作台导航</DialogTitle>
                <DialogDescription>切换功能或打开历史会话</DialogDescription>
              </DialogHeader>
              <Brand />
              <NewConversationButton onNavigate={() => setMobileNavigationOpen(false)} />
              <AppNavigation onNavigate={() => setMobileNavigationOpen(false)} />
              <div className="min-h-0 flex-1 overflow-hidden" ref={setMobileSessionSlot} />
              <ShellStatus />
            </DialogContent>
          </Dialog>
          {!isAgentRoute ? <>
            <div className="flex items-center gap-2">
              <AgentMark className="size-7" />
              <span className="text-[13.5px] font-semibold tracking-tight">Harness <span className="font-normal text-muted-foreground">Agent</span></span>
            </div>
            <ThemeToggle />
          </> : null}
        </header>

        <div className="liquid-glass-window flex min-h-0 flex-1 overflow-hidden lg:rounded-[26px]">
          <aside className={cn(
            "hidden shrink-0 flex-col overflow-hidden border-r border-white/85 bg-sky-50/35 lg:flex dark:border-slate-800/80 dark:bg-slate-900/45",
            sidebarCollapsed ? "shell-sidebar-collapsed w-[68px]" : SIDEBAR_WIDTH_CLASS,
          )}
          >
            <div className="relative shrink-0">
              {!sidebarCollapsed ? <Brand /> : null}
              <Button
                aria-label={sidebarCollapsed ? "展开侧栏" : "收起侧栏"}
                aria-expanded={!sidebarCollapsed}
                className={sidebarCollapsed ? "mx-auto my-3.5 flex" : "absolute top-3.5 right-2 text-muted-foreground"}
                onClick={() => setSidebarCollapsed((value) => !value)}
                size="icon-sm"
                variant="ghost"
              >
                {sidebarCollapsed ? <PanelLeftOpen aria-hidden="true" /> : <PanelLeftClose aria-hidden="true" />}
              </Button>
            </div>
            <NewConversationButton onNavigate={() => setMobileNavigationOpen(false)} />
            <AppNavigation />
            <div className="min-h-0 flex-1 overflow-hidden" ref={setDesktopSessionSlot} />
            <ShellStatus />
          </aside>

          <main
            className={cn(
              "min-h-0 min-w-0 flex-1 bg-white/42 dark:bg-slate-950/35",
              isWorkspaceRoute
                ? "flex flex-col overflow-hidden"
                : "management-workspace overflow-y-auto overscroll-contain px-4 py-5 sm:px-6 lg:px-8 lg:py-7",
            )}
            id="workspace-main"
            tabIndex={-1}
          >
            <Outlet />
          </main>
        </div>
      </div>
    </SidebarSessionSlotsProvider>
  );
}

function Brand() {
  return (
    <div className="shell-brand flex shrink-0 items-center gap-2.5">
      <AgentMark />
      <div className="min-w-0">
        <strong className="block truncate text-[15px] font-semibold tracking-tight text-foreground">
          Harness <span className="font-normal text-foreground/80">Agent</span>
        </strong>
      </div>
    </div>
  );
}

function NewConversationButton({ onNavigate }: { onNavigate?: () => void }) {
  const navigate = useNavigate();
  return (
    <div className="shrink-0 px-3 pb-1.5">
      <Button
        aria-label="新建对话"
        title="新建对话"
        className="shell-new-conversation w-full gap-1.5 rounded-full px-3"
        onClick={() => {
          navigate(`/agent/${encodeURIComponent(createSessionId())}`);
          onNavigate?.();
        }}
      >
        <Plus aria-hidden="true" className="size-4 stroke-[2.2]" />
        <span>新建任务</span>
      </Button>
    </div>
  );
}

function ShellStatus() {
  const { logout, user } = useAuth();
  return (
    <div className="shell-account flex shrink-0 items-center justify-between gap-2 px-3 py-2">
      <span className="flex min-w-0 items-center gap-2">
        <span
          aria-hidden="true"
          className="liquid-glass-pill grid size-7.5 shrink-0 place-items-center rounded-full text-primary"
        >
          <UserRound className="size-3.5" strokeWidth={1.8} />
        </span>
        <span className="min-w-0 text-xs text-sidebar-foreground">
          <strong className="block truncate text-[12px] font-semibold leading-tight">{user?.display_name || "用户"}</strong>
          <span className="block truncate text-[10.5px] text-muted-foreground">{user?.email}</span>
        </span>
      </span>
      <span className="flex items-center gap-0.5">
        <ThemeToggle />
        <Button aria-label="退出登录" onClick={() => void logout()} size="icon-sm" variant="ghost">
          <LogOut aria-hidden="true" className="size-3.5" />
        </Button>
      </span>
    </div>
  );
}
