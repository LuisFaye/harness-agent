import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

export function Sidebar({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "flex h-full min-h-0 flex-col overflow-hidden bg-sidebar text-sidebar-foreground",
        className,
      )}
      data-slot="sidebar"
      {...props}
    />
  );
}

export function SidebarHeader({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn("flex shrink-0 flex-col gap-3 border-b border-sidebar-border p-3", className)}
      data-slot="sidebar-header"
      {...props}
    />
  );
}

export function SidebarContent({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn("min-h-0 flex-1 overflow-y-auto p-2", className)}
      data-slot="sidebar-content"
      {...props}
    />
  );
}

export function SidebarMenu({ className, ...props }: ComponentProps<"ul">) {
  return (
    <ul
      className={cn("flex min-w-0 flex-col gap-1", className)}
      data-slot="sidebar-menu"
      {...props}
    />
  );
}

export function SidebarMenuItem({ className, ...props }: ComponentProps<"li">) {
  return (
    <li
      className={cn("group/menu-item relative", className)}
      data-slot="sidebar-menu-item"
      {...props}
    />
  );
}

interface SidebarMenuButtonProps extends ComponentProps<"button"> {
  isActive?: boolean;
}

export function SidebarMenuButton({
  className,
  isActive = false,
  ...props
}: SidebarMenuButtonProps) {
  return (
    <button
      aria-current={isActive ? "page" : undefined}
      className={cn(
        "flex min-h-11 w-full min-w-0 flex-col justify-center rounded-lg px-3 py-2 text-left",
        "transition-colors duration-150 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring",
        isActive && "bg-sidebar-accent text-sidebar-accent-foreground",
        className,
      )}
      data-active={isActive || undefined}
      data-slot="sidebar-menu-button"
      type="button"
      {...props}
    />
  );
}
