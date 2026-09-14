// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CitationBundle } from "@/lib/stream/schemas";

import { CitationMessageResponse } from "./citation-message-response";

const bundle: CitationBundle = {
  version: "1.0",
  status: "complete",
  answer_hash: "x",
  sources: [{
    source_id: "src-1",
    source_type: "web",
    title: "Test",
    url: "https://example.com",
    domain: "example.com",
    provider: "test",
    tool_name: "search",
    retrieved_at: "2026-01-01T00:00:00+08:00",
    content_level: "snippet",
    source_role: "primary",
    content_hash: "h",
    evidence_id: null,
  }],
  chunks: [{
    chunk_id: "chk-1",
    source_id: "src-1",
    text: "quote text",
    locator: {},
    content_hash: "h2",
  }],
  supports: [{
    support_id: "sup-1",
    block_id: "li-01",
    answer_quote: "截图（Screenshot）：第一步",
    target_start: null,
    target_end: null,
    chunk_ids: ["chk-1"],
    support_status: "supported",
    origin: "agent_generated",
  }],
};

describe("CitationMessageResponse", () => {
  it("tight ordered list items render a single inline citation group", () => {
    const markdown = [
      "## 核心循环",
      "",
      "1. **截图（Screenshot）**：第一步",
      "2. **视觉理解**：第二步",
    ].join("\n");
    const { container } = render(
      <CitationMessageResponse citationBundle={bundle}>{markdown}</CitationMessageResponse>,
    );

    const groups = container.querySelectorAll('[aria-label="本段引用来源"]');
    const buttons = container.querySelectorAll('[aria-label^="查看来源"]');
    expect(groups.length).toBe(1);
    expect(buttons.length).toBe(1);

    const firstItem = container.querySelector("ol li");
    expect(firstItem?.querySelectorAll('[aria-label="本段引用来源"]').length).toBe(1);
  });

  it("loose ordered list items still render only one citation group", () => {
    const markdown = [
      "## 核心循环",
      "",
      "1. **截图（Screenshot）**：第一步",
      "",
      "2. **视觉理解**：第二步",
    ].join("\n");
    const { container } = render(
      <CitationMessageResponse citationBundle={bundle}>{markdown}</CitationMessageResponse>,
    );

    expect(container.querySelectorAll('[aria-label="本段引用来源"]').length).toBe(1);
    expect(container.querySelectorAll('[aria-label^="查看来源"]').length).toBe(1);
  });
});
