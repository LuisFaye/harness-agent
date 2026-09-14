import { createHighlighterCore } from "@shikijs/core";
import { createJavaScriptRegexEngine } from "@shikijs/engine-javascript";
import css from "@shikijs/langs/css";
import html from "@shikijs/langs/html";
import java from "@shikijs/langs/java";
import javascript from "@shikijs/langs/javascript";
import json from "@shikijs/langs/json";
import markdown from "@shikijs/langs/markdown";
import python from "@shikijs/langs/python";
import shellscript from "@shikijs/langs/shellscript";
import sql from "@shikijs/langs/sql";
import typescript from "@shikijs/langs/typescript";
import yaml from "@shikijs/langs/yaml";
import githubDark from "@shikijs/themes/github-dark";
import githubLight from "@shikijs/themes/github-light";
import type { StreamdownProps } from "streamdown";

type CodePlugin = NonNullable<NonNullable<StreamdownProps["plugins"]>["code"]>;
type HighlightResult = NonNullable<ReturnType<CodePlugin["highlight"]>>;

const languageAliases: Record<string, string> = {
  bash: "shellscript",
  css: "css",
  html: "html",
  java: "java",
  javascript: "javascript",
  js: "javascript",
  json: "json",
  markdown: "markdown",
  md: "markdown",
  py: "python",
  python: "python",
  sh: "shellscript",
  shell: "shellscript",
  sql: "sql",
  ts: "typescript",
  tsx: "typescript",
  typescript: "typescript",
  yaml: "yaml",
  yml: "yaml",
};
const supportedLanguages = [...new Set(Object.values(languageAliases))];
const themes = ["github-light", "github-dark"] as const;
const resultCache = new Map<string, HighlightResult>();
const pendingCallbacks = new Map<string, Set<(result: HighlightResult) => void>>();

const highlighterPromise = createHighlighterCore({
  engine: createJavaScriptRegexEngine(),
  langs: [
    css,
    html,
    java,
    javascript,
    json,
    markdown,
    python,
    shellscript,
    sql,
    typescript,
    yaml,
  ],
  themes: [githubLight, githubDark],
});

export const limitedCodeHighlighter: CodePlugin = {
  name: "shiki",
  type: "code-highlighter",
  getSupportedLanguages: () => supportedLanguages,
  getThemes: () => [...themes],
  supportsLanguage: (language) => Boolean(languageAliases[normalizeLanguage(language)]),
  highlight: ({ code, language }, callback) => {
    const normalizedLanguage = languageAliases[normalizeLanguage(language)];
    if (!normalizedLanguage) {
      return null;
    }
    const cacheKey = `${normalizedLanguage}:${code}`;
    const cached = resultCache.get(cacheKey);
    if (cached) {
      return cached;
    }
    if (callback) {
      const callbacks = pendingCallbacks.get(cacheKey) ?? new Set();
      callbacks.add(callback);
      pendingCallbacks.set(cacheKey, callbacks);
    }
    if (pendingCallbacks.get(cacheKey)?.size === 1) {
      void highlighterPromise.then((highlighter) => {
        const result = highlighter.codeToTokens(code, {
          lang: normalizedLanguage,
          themes: {
            dark: themes[1],
            light: themes[0],
          },
        }) as HighlightResult;
        resultCache.set(cacheKey, result);
        pendingCallbacks.get(cacheKey)?.forEach((notify) => notify(result));
        pendingCallbacks.delete(cacheKey);
      });
    }
    return null;
  },
};

function normalizeLanguage(language: string): string {
  return language.trim().toLocaleLowerCase();
}
