import {
  MessageScroller as MessageScrollerPrimitive,
  useMessageScroller,
  useMessageScrollerScrollable,
  useMessageScrollerVisibility,
} from "@shadcn/react/message-scroller";
import { ArrowDown } from "lucide-react";
import type { ComponentProps } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function MessageScrollerProvider(
  props: ComponentProps<typeof MessageScrollerPrimitive.Provider>,
) {
  return <MessageScrollerPrimitive.Provider {...props} />;
}

export function MessageScroller({
  className,
  ...props
}: ComponentProps<typeof MessageScrollerPrimitive.Root>) {
  return (
    <MessageScrollerPrimitive.Root
      className={cn(
        "group/message-scroller relative flex size-full min-h-0 flex-col overflow-hidden",
        className,
      )}
      data-slot="message-scroller"
      {...props}
    />
  );
}

export function MessageScrollerViewport({
  className,
  ...props
}: ComponentProps<typeof MessageScrollerPrimitive.Viewport>) {
  return (
    <MessageScrollerPrimitive.Viewport
      className={cn(
        "size-full min-h-0 min-w-0 overflow-y-auto overscroll-contain",
        className,
      )}
      data-slot="message-scroller-viewport"
      {...props}
    />
  );
}

export function MessageScrollerContent({
  className,
  ...props
}: ComponentProps<typeof MessageScrollerPrimitive.Content>) {
  return (
    <MessageScrollerPrimitive.Content
      className={cn("flex min-h-full flex-col", className)}
      data-slot="message-scroller-content"
      {...props}
    />
  );
}

export function MessageScrollerItem({
  className,
  scrollAnchor = false,
  ...props
}: ComponentProps<typeof MessageScrollerPrimitive.Item>) {
  return (
    <MessageScrollerPrimitive.Item
      className={cn(
        "min-w-0 shrink-0 [contain-intrinsic-size:auto_10rem] [content-visibility:auto]",
        className,
      )}
      data-slot="message-scroller-item"
      scrollAnchor={scrollAnchor}
      {...props}
    />
  );
}

export function MessageScrollerButton({
  className,
  children,
  direction = "end",
  render,
  ...props
}: ComponentProps<typeof MessageScrollerPrimitive.Button>) {
  return (
    <MessageScrollerPrimitive.Button
      className={cn(
        "absolute bottom-4 left-1/2 z-10 -translate-x-1/2 transition-[transform,opacity] duration-200",
        "data-[active=false]:pointer-events-none data-[active=false]:translate-y-full data-[active=false]:scale-95 data-[active=false]:opacity-0",
        className,
      )}
      data-slot="message-scroller-button"
      direction={direction}
      render={render ?? <Button size="icon" variant="outline" />}
      {...props}
    >
      {children ?? (
        <>
          <ArrowDown aria-hidden="true" className="size-4" />
          <span className="sr-only">回到最新消息</span>
        </>
      )}
    </MessageScrollerPrimitive.Button>
  );
}

export {
  useMessageScroller,
  useMessageScrollerScrollable,
  useMessageScrollerVisibility,
};
