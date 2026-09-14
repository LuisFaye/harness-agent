class TestResizeObserver implements ResizeObserver {
  disconnect() {}

  observe() {}

  unobserve() {}
}

Object.defineProperty(globalThis, "ResizeObserver", {
  configurable: true,
  value: TestResizeObserver,
});

if (typeof HTMLElement !== "undefined" && !HTMLElement.prototype.scrollTo) {
  HTMLElement.prototype.scrollTo = () => undefined;
}
