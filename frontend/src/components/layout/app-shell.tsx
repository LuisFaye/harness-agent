import { LogOut, Menu, MessageSquarePlus, PanelLeftClose, PanelLeftOpen, UserRound } from "lucide-react";
import { useEffect, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { AgentMark } from "@/components/layout/agent-mark";
import { AppNavigation } from "@/components/layout/app-navigation";
import { SidebarResizableSections } from "@/components/layout/sidebar-resizable-sections";
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

const SIDEBAR_WIDTH_CLASS = "w-[280px]";
const SIDEBAR_OFFSET_CLASS = "lg:pl-[280px]";

export function AppShell() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
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
    setMobileNavigationOpen(false);
  }, [location.pathname]);

  return (
    <SidebarSessionSlotsProvider
      value={{ desktop: desktopSessionSlot, mobile: mobileSessionSlot }}
    >
      <div className="min-h-svh bg-background">
        <a className="skip-link" href="#workspace-main">跳到主要内容</a>
        <aside className={cn(
          "fixed inset-y-0 left-0 z-40 hidden flex-col border-r border-sidebar-border bg-sidebar lg:flex",
          sidebarCollapsed ? "shell-sidebar-collapsed w-[72px]" : SIDEBAR_WIDTH_CLASS,
        )}
        >
          <div className="relative shrink-0">
            {!sidebarCollapsed ? <Brand /> : null}
            <Button
              aria-label={sidebarCollapsed ? "展开侧栏" : "收起侧栏"}
              aria-expanded={!sidebarCollapsed}
              className={sidebarCollapsed ? "mx-auto my-[18px] flex" : "absolute top-[18px] right-2"}
              onClick={() => setSidebarCollapsed((value) => !value)}
              size="icon"
              variant="ghost"
            >
              {sidebarCollapsed ? <PanelLeftOpen aria-hidden="true" /> : <PanelLeftClose aria-hidden="true" />}
            </Button>
          </div>
          <NewConversationButton onNavigate={() => setMobileNavigationOpen(false)} />
          {isAgentRoute ? (
            <SidebarResizableSections
              className="border-t border-sidebar-border"
              navigation={<AppNavigation />}
              sessionSlotRef={setDesktopSessionSlot}
            />
          ) : (
            <>
              <AppNavigation />
              <div aria-hidden="true" className="flex-1" />
            </>
          )}
          <ShellStatus />
        </aside>

        <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b bg-background/95 px-4 backdrop-blur lg:hidden">
          <Dialog open={mobileNavigationOpen} onOpenChange={setMobileNavigationOpen}>
            <DialogTrigger
              render={
                <Button
                  aria-label="打开工作台导航"
                  size="icon"
                  variant="ghost"
                />
              }
            >
              <Menu aria-hidden="true" />
            </DialogTrigger>
            <DialogContent
              className="top-0 left-0 flex h-dvh max-h-dvh max-w-[20rem] translate-x-0 translate-y-0 flex-col content-start rounded-none border-y-0 border-l-0 bg-sidebar p-0 sm:max-w-[20rem]"
            >
              <DialogHeader className="sr-only">
                <DialogTitle>工作台导航</DialogTitle>
                <DialogDescription>切换功能或打开历史会话</DialogDescription>
              </DialogHeader>
              <Brand />
              <NewConversationButton onNavigate={() => setMobileNavigationOpen(false)} />
              {isAgentRoute ? (
                <SidebarResizableSections
                  className="border-t border-sidebar-border"
                  navigation={<AppNavigation onNavigate={() => setMobileNavigationOpen(false)} />}
                  sessionSlotRef={setMobileSessionSlot}
                />
              ) : (
                <>
                  <AppNavigation onNavigate={() => setMobileNavigationOpen(false)} />
                  <div aria-hidden="true" className="flex-1" />
                </>
              )}
              <ShellStatus />
            </DialogContent>
          </Dialog>
          <div className="flex items-center gap-2">
            <AgentMark className="size-8" />
            <span className="font-semibold">SB Agent</span>
          </div>
          <ThemeToggle />
        </header>

        <div className={cn("min-h-[calc(100dvh-4rem)] lg:min-h-svh", sidebarCollapsed ? "lg:pl-[72px]" : SIDEBAR_OFFSET_CLASS)}>
          <main
            className={cn(
              "w-full",
              isWorkspaceRoute
                ? "flex h-[calc(100dvh-4rem)] min-h-0 flex-col lg:h-dvh"
                : "management-workspace min-h-[calc(100dvh-4rem)] w-full max-w-[1440px] px-4 py-6 sm:px-6 lg:px-10 lg:py-10",
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
    <div className="shell-brand flex shrink-0 items-center gap-3">
      <AgentMark />
      <div className="min-w-0">
        <strong className="block truncate text-sm font-semibold text-sidebar-foreground">SB Agent</strong>
        <span className="block truncate text-xs text-muted-foreground">AI 工作台</span>
      </div>
    </div>
  );
}

function NewConversationButton({ onNavigate }: { onNavigate?: () => void }) {
  const navigate = useNavigate();
  return (
    <div className="shrink-0 px-3">
      <Button
        aria-label="新建对话"
        title="新建对话"
        className="shell-new-conversation w-full justify-start gap-3 rounded-xl"
        onClick={() => {
          navigate(`/agent/${encodeURIComponent(createSessionId())}`);
          onNavigate?.();
        }}
      >
        <MessageSquarePlus aria-hidden="true" />
        <span>新建对话</span>
      </Button>
    </div>
  );
}

function ShellStatus() {
  const { logout, user } = useAuth();
  return (
    <div className="shell-account flex shrink-0 items-center justify-between gap-2 border-t border-sidebar-border px-3 py-3">
      <span className="flex min-w-0 items-center gap-2.5">
        <span
          aria-hidden="true"
          className="grid size-8 shrink-0 place-items-center rounded-lg bg-sidebar-accent text-sidebar-accent-foreground"
        >
          <UserRound className="size-4" strokeWidth={1.8} />
        </span>
        <span className="min-w-0 text-xs text-sidebar-foreground">
          <strong className="block truncate font-medium">{user?.display_name || "用户"}</strong>
          <span className="block truncate text-sidebar-accent-foreground/70">{user?.email}</span>
        </span>
      </span>
      <span className="flex items-center">
        <ThemeToggle />
        <Button aria-label="退出登录" onClick={() => void logout()} size="icon" variant="ghost">
          <LogOut aria-hidden="true" className="size-4" />
        </Button>
      </span>
    </div>
  );
}
