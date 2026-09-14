import { parseStreamEvent, type StreamEvent } from "./schemas";

export class NdjsonLineDecoder {
  private tail = "";

  push(chunk: string): StreamEvent[] {
    this.tail += chunk;
    const lines = this.tail.split("\n");
    this.tail = lines.pop() ?? "";
    return this.parseLines(lines);
  }

  finish(): StreamEvent[] {
    const tail = this.tail.trim();
    this.tail = "";
    return tail ? this.parseLines([tail]) : [];
  }

  private parseLines(lines: string[]): StreamEvent[] {
    return lines
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => parseStreamEvent(JSON.parse(line) as unknown));
  }
}

export class NdjsonByteDecoder {
  private readonly textDecoder = new TextDecoder();
  private readonly lineDecoder = new NdjsonLineDecoder();

  push(chunk: Uint8Array): StreamEvent[] {
    return this.lineDecoder.push(this.textDecoder.decode(chunk, { stream: true }));
  }

  finish(): StreamEvent[] {
    const events = this.lineDecoder.push(this.textDecoder.decode());
    return [...events, ...this.lineDecoder.finish()];
  }
}

export function parseNdjsonDocument(content: string): StreamEvent[] {
  const decoder = new NdjsonLineDecoder();
  return [...decoder.push(content), ...decoder.finish()];
}
