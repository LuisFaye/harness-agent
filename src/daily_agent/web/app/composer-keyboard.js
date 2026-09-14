export function isImeComposing(event) {
  return Boolean(event?.isComposing || event?.keyCode === 229 || event?.which === 229);
}

export function shouldSubmitComposer(event) {
  return event?.key === "Enter" && !event.shiftKey && !isImeComposing(event);
}
