import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const testDirectory = dirname(fileURLToPath(import.meta.url));
const webDirectory = join(testDirectory, "../../src/daily_agent/web");

test("前端内部模块不使用版本查询参数，避免生成多份共享 state", () => {
  const moduleFiles = [
    join(webDirectory, "app.js"),
    ...readdirSync(join(webDirectory, "app"))
      .filter((name) => name.endsWith(".js"))
      .map((name) => join(webDirectory, "app", name)),
  ];

  const invalidImports = [];
  for (const filePath of moduleFiles) {
    const source = readFileSync(filePath, "utf8");
    if (/from\s+["'][^"']+\.js\?v=\d+["']/.test(source)) invalidImports.push(filePath);
  }

  assert.deepEqual(invalidImports, []);
});
