import { readFileSync } from "node:fs";
import { resolve } from "node:path";

export function readTextFixture(name: string): string {
  return readFileSync(resolve(process.cwd(), "src", "test", "fixtures", name), "utf8");
}

export function readJsonFixture(name: string): unknown {
  return JSON.parse(readTextFixture(name)) as unknown;
}
