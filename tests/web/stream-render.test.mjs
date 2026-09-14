import assert from "node:assert/strict";
import test from "node:test";

globalThis.document = {
  getElementById: () => null,
  querySelectorAll: () => [],
};

const { updateStreamingResponseBody } = await import("../../src/daily_agent/web/app/stream-render.js");

function createBody() {
  return {
    classList: {
      hasContent: false,
      isStreaming: false,
      toggle(name, enabled) {
        if (name === "has-content") this.hasContent = enabled;
        if (name === "is-streaming") this.isStreaming = enabled;
      },
    },
    innerHTML: "",
    textContent: "",
  };
}

test("streaming response renders unfinished deltas as markdown", () => {
  const body = createBody();

  updateStreamingResponseBody(body, { streamedText: "## 第一段\n\n**重点**", responseCompleted: false });

  assert.equal(body.classList.hasContent, true);
  assert.equal(body.classList.isStreaming, true);
  assert.match(body.innerHTML, /<h3>第一段<\/h3>/);
  assert.match(body.innerHTML, /<strong>重点<\/strong>/);
});

test("streaming response turns a completed table prefix into a table", () => {
  const body = createBody();

  updateStreamingResponseBody(body, {
    streamedText: "| 时段 | 价格 |\n| --- | --- |\n| 上午 | 1000 |",
    responseCompleted: false,
  });

  assert.equal(body.classList.isStreaming, true);
  assert.match(body.innerHTML, /<table>/);
  assert.match(body.innerHTML, /<td style="text-align:left">上午<\/td>/);
});

test("completed response is rendered as markdown once", () => {
  const body = createBody();

  updateStreamingResponseBody(body, { streamedText: "**完成**", responseCompleted: true });

  assert.equal(body.classList.hasContent, true);
  assert.equal(body.classList.isStreaming, false);
  assert.match(body.innerHTML, /<strong>完成<\/strong>/);
});
