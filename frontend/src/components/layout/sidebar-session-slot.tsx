import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";

interface SidebarSessionSlots {
  desktop: HTMLElement | null;
  mobile: HTMLElement | null;
}

const SidebarSessionSlotsContext = createContext<SidebarSessionSlots>({
  desktop: null,
  mobile: null,
});

export function SidebarSessionSlotsProvider({
  children,
  value,
}: {
  children: ReactNode;
  value: SidebarSessionSlots;
}) {
  return (
    <SidebarSessionSlotsContext.Provider value={value}>
      {children}
    </SidebarSessionSlotsContext.Provider>
  );
}

export function SidebarSessionPortals({ render }: { render: () => ReactNode }) {
  const slots = useContext(SidebarSessionSlotsContext);
  return (
    <>
      {slots.desktop ? createPortal(render(), slots.desktop) : null}
      {slots.mobile ? createPortal(render(), slots.mobile) : null}
    </>
  );
}
