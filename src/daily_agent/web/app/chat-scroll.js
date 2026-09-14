import { elements } from "./dom.js";
import { state } from "./state.js";

const FOLLOW_THRESHOLD = 80;

export function initializeChatScroll() {
  state.chatStickToBottom = true;
  elements.chatLog.addEventListener("scroll", () => {
    state.chatStickToBottom = distanceFromBottom() <= FOLLOW_THRESHOLD;
    syncJumpButton();
  }, { passive: true });
  elements.jumpToLatest.addEventListener("click", () => scrollChatToLatest({ force: true }));
}

export function followChatIfNeeded() {
  if (!state.chatStickToBottom) {
    syncJumpButton();
    return;
  }
  scrollChatToLatest({ force: true });
}

export function scrollChatToLatest({ force = false } = {}) {
  if (!force && !state.chatStickToBottom) {
    syncJumpButton();
    return;
  }
  elements.chatLog.scrollTop = elements.chatLog.scrollHeight;
  state.chatStickToBottom = true;
  syncJumpButton();
}

export function prepareForNewMessage() {
  if (distanceFromBottom() <= FOLLOW_THRESHOLD) {
    state.chatStickToBottom = true;
  }
}

function distanceFromBottom() {
  return Math.max(0, elements.chatLog.scrollHeight - elements.chatLog.scrollTop - elements.chatLog.clientHeight);
}

function syncJumpButton() {
  elements.jumpToLatest.hidden = state.chatStickToBottom || elements.chatLog.scrollHeight <= elements.chatLog.clientHeight;
}
