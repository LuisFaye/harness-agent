import assert from "node:assert/strict";
import test from "node:test";

import {
  isImeComposing,
  shouldSubmitComposer,
} from "../../src/daily_agent/web/app/composer-keyboard.js";

test("中文输入法组合过程中按 Enter 不发送", () => {
  assert.equal(shouldSubmitComposer({ key: "Enter", shiftKey: false, isComposing: true }), false);
  assert.equal(shouldSubmitComposer({ key: "Enter", shiftKey: false, keyCode: 229 }), false);
  assert.equal(shouldSubmitComposer({ key: "Enter", shiftKey: false, which: 229 }), false);
});

test("组合结束后 Enter 发送，Shift + Enter 保持换行", () => {
  assert.equal(shouldSubmitComposer({ key: "Enter", shiftKey: false, isComposing: false }), true);
  assert.equal(shouldSubmitComposer({ key: "Enter", shiftKey: true, isComposing: false }), false);
  assert.equal(shouldSubmitComposer({ key: "a", shiftKey: false, isComposing: false }), false);
});

test("输入法组合状态识别兼容标准字段和 229 键码", () => {
  assert.equal(isImeComposing({ isComposing: true }), true);
  assert.equal(isImeComposing({ keyCode: 229 }), true);
  assert.equal(isImeComposing({ which: 229 }), true);
  assert.equal(isImeComposing({ isComposing: false, keyCode: 13 }), false);
});
