import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

interface ShimmerTextProps extends ComponentProps<"span"> {
  active?: boolean;
}

/** Cursor 风格的从左往右扫光文字，用于运行中的思考/工具步骤。 */
export function ShimmerText({
  active = true,
  className,
  children,
  ...props
}: ShimmerTextProps) {
  return (
    <span
      className={cn(
        active && "shimmer-text",
        !active && "text-muted-foreground",
        className,
      )}
      {...props}
    >
      {children}
    </span>
  );
}
