import { CalendarClock, ChartNoAxesCombined, ChevronRight, Grid2X2, LayoutGrid, MoreHorizontal, Share2, Settings, UsersRound } from "lucide-react";
import { useState } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useAuth } from "@/features/auth/auth-provider";
import { cn } from "@/lib/utils";

const primaryNavigation = [
  { to: "/agent", label: "智能工作台", icon: LayoutGrid, owner: false },
  { to: "/capabilities", label: "能力中心", icon: Grid2X2, owner: true },
  { to: "/runs", label: "运行监控", icon: ChartNoAxesCombined, owner: false },
  { to: "/schedules", label: "自动任务", icon: CalendarClock, owner: true },
];

const moreNavigation = [
  { to: "/shared", label: "共享给我", description: "浏览伙伴分享的会话", icon: Share2, owner: false },
  { to: "/settings", label: "模型与设置", description: "模型连接与运行偏好", icon: Settings, owner: true },
  { to: "/members", label: "成员与邀请码", description: "管理工作空间成员", icon: UsersRound, owner: true },
];

export function AppNavigation({ onNavigate }: { onNavigate?: () => void }) {
  const { user } = useAuth();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const directItems = primaryNavigation.filter((item) => !item.owner || user?.is_owner);
  const items = moreNavigation.filter((item) => !item.owner || user?.is_owner);
  const activeMoreItem = items.find((item) => pathname === item.to || pathname.startsWith(`${item.to}/`));
  const navigate = () => { setOpen(false); onNavigate?.(); };
  return (
    <nav aria-label="主要功能" className="shell-navigation flex shrink-0 flex-col">
      {directItems.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          title={label}
          aria-label={label}
          onClick={navigate}
          className={({ isActive }) => cn("shell-navigation-link", isActive && "is-active")}
        >
          <Icon aria-hidden="true" />
          <span>{label}</span>
        </NavLink>
      ))}
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger render={<Button variant="ghost" className={cn("shell-navigation-link shell-more-trigger", activeMoreItem && "is-active")} aria-label="更多功能" title="更多功能" />}>
          <MoreHorizontal aria-hidden="true" />
          <span>更多与设置</span>
          <ChevronRight aria-hidden="true" className="shell-more-chevron ml-auto" />
        </PopoverTrigger>
        <PopoverContent side="bottom" align="start" className="shell-more-popover w-64 max-w-[calc(100vw-2rem)] p-1.5">
          <p className="px-2.5 pb-1 pt-1 text-[10.5px] font-semibold tracking-wider text-muted-foreground uppercase">空间与设置</p>
          <nav aria-label="更多功能" className="space-y-0.5">
            {items.map(({ to, label, description, icon: Icon }) => (
              <NavLink key={to} to={to} onClick={navigate} className={({ isActive }) => cn(
                "group/more flex items-center gap-2.5 rounded-xl px-2.5 py-1.5 transition-colors hover:bg-white/75 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                isActive && "bg-primary-soft text-primary",
              )}>
                <span className="liquid-glass-pill grid size-7.5 shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors group-hover/more:text-primary">
                  <Icon aria-hidden="true" className="size-3.5 shrink-0" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[12.5px] font-medium leading-snug">{label}</span>
                  <span className="block truncate text-[11px] text-muted-foreground">{description}</span>
                </span>
              </NavLink>
            ))}
          </nav>
        </PopoverContent>
      </Popover>
    </nav>
  );
}
