import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type RefCallback,
} from "react";

import { cn } from "@/lib/utils";

const STORAGE_KEY = "sb-agent:sidebar-session-ratio";
const DEFAULT_RATIO = 0.58;
const MIN_RATIO = 0.28;
const MAX_RATIO = 0.82;
const MIN_SESSION_PX = 168;
const MIN_NAV_PX = 112;
const KEYBOARD_STEP = 0.04;

function readStoredRatio(): number {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return DEFAULT_RATIO;
    }
    const value = Number.parseFloat(raw);
    if (Number.isFinite(value) && value >= MIN_RATIO && value <= MAX_RATIO) {
      return value;
    }
  } catch {
    // localStorage 不可用时回退默认值。
  }
  return DEFAULT_RATIO;
}

function clampRatio(next: number, containerHeight: number): number {
  const minByPixels = Math.max(MIN_RATIO, MIN_SESSION_PX / containerHeight);
  const maxByPixels = Math.min(MAX_RATIO, 1 - MIN_NAV_PX / containerHeight);
  return Math.min(maxByPixels, Math.max(minByPixels, next));
}

interface SidebarResizableSectionsProps {
  navigation: ReactNode;
  sessionSlotRef: RefCallback<HTMLDivElement>;
  className?: string;
}

export function SidebarResizableSections({
  navigation,
  sessionSlotRef,
  className,
}: SidebarResizableSectionsProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const ratioRef = useRef(readStoredRatio());
  const [ratio, setRatio] = useState(ratioRef.current);
  const draggingRef = useRef(false);

  const persistRatio = useCallback((next: number) => {
    ratioRef.current = next;
    setRatio(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, String(next));
    } catch {
      // 隐私模式等场景下静默降级。
    }
  }, []);

  const updateRatioFromPointer = useCallback((clientY: number) => {
    const container = containerRef.current;
    if (!container) {
      return;
    }
    const rect = container.getBoundingClientRect();
    const sessionHeight = rect.bottom - clientY;
    persistRatio(clampRatio(sessionHeight / rect.height, rect.height));
  }, [persistRatio]);

  useEffect(() => {
    const handlePointerMove = (event: PointerEvent) => {
      if (!draggingRef.current) {
        return;
      }
      updateRatioFromPointer(event.clientY);
    };
    const handlePointerUp = () => {
      if (!draggingRef.current) {
        return;
      }
      draggingRef.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, [updateRatioFromPointer]);

  const startDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    draggingRef.current = true;
    event.currentTarget.setPointerCapture(event.pointerId);
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
    updateRatioFromPointer(event.clientY);
  };

  const nudgeRatio = (delta: number) => {
    const container = containerRef.current;
    if (!container) {
      return;
    }
    persistRatio(clampRatio(ratioRef.current + delta, container.clientHeight));
  };

  return (
    <div
      className={cn("flex min-h-0 flex-1 flex-col overflow-hidden", className)}
      ref={containerRef}
    >
      <div
        className="min-h-0 overflow-y-auto"
        style={{ flex: `${1 - ratio} 1 0` }}
      >
        {navigation}
      </div>
      <div
        aria-label="调整最近对话区域高度"
        aria-orientation="horizontal"
        aria-valuemax={MAX_RATIO}
        aria-valuemin={MIN_RATIO}
        aria-valuenow={Number(ratio.toFixed(2))}
        className="sidebar-resize-handle group relative flex h-3 shrink-0 cursor-row-resize touch-none items-center border-t border-sidebar-border bg-sidebar focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
        onDoubleClick={() => persistRatio(DEFAULT_RATIO)}
        onKeyDown={(event) => {
          if (event.key === "ArrowUp") {
            event.preventDefault();
            nudgeRatio(KEYBOARD_STEP);
          }
          if (event.key === "ArrowDown") {
            event.preventDefault();
            nudgeRatio(-KEYBOARD_STEP);
          }
          if (event.key === "Home") {
            event.preventDefault();
            persistRatio(MAX_RATIO);
          }
          if (event.key === "End") {
            event.preventDefault();
            persistRatio(MIN_RATIO);
          }
        }}
        onPointerDown={startDrag}
        role="separator"
        tabIndex={0}
        title="拖动调整最近对话高度，双击恢复默认"
      >
        <span
          aria-hidden="true"
          className="mx-auto h-1 w-12 rounded-full bg-sidebar-border transition-colors group-hover:bg-primary/45 group-active:bg-primary/65"
        />
      </div>
      <div
        className="flex min-h-0 flex-col overflow-hidden"
        ref={sessionSlotRef}
        style={{ flex: `${ratio} 0 0`, minHeight: MIN_SESSION_PX }}
      />
    </div>
  );
}
