import { elements } from "./dom.js";

let workspaceNavigationTrigger = null;
let sessionNavigationTrigger = null;

export function openWorkspaceNavigation() {
  workspaceNavigationTrigger = document.activeElement;
  document.body.classList.add("workspace-nav-open");
  elements.workspaceMenuToggle.setAttribute("aria-expanded", "true");
  syncScrim();
}

export function closeWorkspaceNavigation({ restoreFocus = false } = {}) {
  document.body.classList.remove("workspace-nav-open");
  elements.workspaceMenuToggle.setAttribute("aria-expanded", "false");
  elements.mobileSidebarToggle.setAttribute("aria-expanded", "false");
  syncScrim();
  if (restoreFocus && workspaceNavigationTrigger?.isConnected) workspaceNavigationTrigger.focus();
}

export function toggleWorkspaceNavigation() {
  if (document.body.classList.contains("workspace-nav-open")) closeWorkspaceNavigation();
  else openWorkspaceNavigation();
}

export function openSessionNavigation() {
  sessionNavigationTrigger = document.activeElement;
  document.body.classList.add("session-drawer-open");
  elements.mobileSessionToggle.setAttribute("aria-expanded", "true");
  syncScrim();
}

export function closeSessionNavigation({ restoreFocus = false } = {}) {
  document.body.classList.remove("session-drawer-open");
  elements.mobileSessionToggle.setAttribute("aria-expanded", "false");
  syncScrim();
  if (restoreFocus && sessionNavigationTrigger?.isConnected) sessionNavigationTrigger.focus();
}

export function toggleSessionNavigation() {
  if (document.body.classList.contains("session-drawer-open")) closeSessionNavigation();
  else openSessionNavigation();
}

export function closeWorkspaceDrawers({ restoreFocus = false } = {}) {
  closeWorkspaceNavigation({ restoreFocus });
  closeSessionNavigation({ restoreFocus });
}

export function autoResizeComposer() {
  const input = elements.chatInput;
  input.style.height = "auto";
  const nextHeight = Math.min(168, Math.max(44, input.scrollHeight));
  input.style.height = `${nextHeight}px`;
  input.style.overflowY = input.scrollHeight > 168 ? "auto" : "hidden";
}

function syncScrim() {
  elements.workspaceScrim.hidden = !(
    document.body.classList.contains("workspace-nav-open")
    || document.body.classList.contains("session-drawer-open")
  );
}
