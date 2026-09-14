import {
  CalendarClock,
  ChartNoAxesCombined,
  Grid2X2,
  MessageSquareText,
  Share2,
  Settings,
  UsersRound,
} from "lucide-react";
import { NavLink } from "react-router-dom";

import { cn } from "@/lib/utils";
import { useAuth } from "@/features/auth/auth-provider";

const memberNavigation = [
  { to: "/agent", label: "对话", icon: MessageSquareText },
  { to: "/runs", label: "运行监控", icon: ChartNoAxesCombined },
  { to: "/shared", label: "共享给我", icon: Share2 },
] as const;

const ownerNavigation = [
  ...memberNavigation,
  { to: "/schedules", label: "自动任务", icon: CalendarClock },
  { to: "/capabilities", label: "能力中心", icon: Grid2X2 },
  { to: "/settings", label: "设置", icon: Settings },
  { to: "/members", label: "成员管理", icon: UsersRound },
] as const;

interface AppNavigationProps {
  onNavigate?: () => void;
}

export function AppNavigation({ onNavigate }: AppNavigationProps) {
  const { user } = useAuth();
  const items = user?.is_owner ? ownerNavigation : memberNavigation;
  return (
    <nav aria-label="主要功能" className="shell-navigation flex shrink-0 flex-col">
      {[
        { label: "工作空间", entries: items.filter((item) => !["/settings", "/members"].includes(item.to)) },
        { label: "管理", entries: items.filter((item) => ["/settings", "/members"].includes(item.to)) },
      ].filter((group) => group.entries.length).map((group) => (
        <div className="shell-navigation-group" key={group.label}>
          <p className="shell-navigation-label">{group.label}</p>
          {group.entries.map(({ to, label, icon: Icon }) => (
            <NavLink
              className={({ isActive }) => cn(
                "shell-navigation-link flex min-h-11 shrink-0 items-center gap-3 rounded-lg px-3 text-sm text-sidebar-foreground transition-colors duration-200",
                "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring",
                isActive && "bg-sidebar-accent font-semibold text-sidebar-accent-foreground",
              )}
              aria-label={label}
              title={label}
              key={to}
              onClick={onNavigate}
              to={to}
            >
              <Icon aria-hidden="true" className="size-[18px]" strokeWidth={1.8} />
              <span>{label}</span>
            </NavLink>
          ))}
        </div>
      ))}
    </nav>
  );
}
