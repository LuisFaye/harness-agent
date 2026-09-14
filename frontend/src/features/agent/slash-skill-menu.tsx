import { useQuery } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type KeyboardEvent,
} from "react";

import { listSkills } from "@/lib/api/management";
import type { SkillSummary } from "@/lib/api/management-schemas";
import { cn } from "@/lib/utils";

export interface SkillMenuItem {
  description: string;
  insertText: string;
  label: string;
  name: string;
}

export interface SlashTokenMatch {
  end: number;
  start: number;
  token: string;
}

interface SlashSkillMenuProps {
  highlightedIndex: number;
  isLoading: boolean;
  items: SkillMenuItem[];
  onHighlight: (index: number) => void;
  onSelect: (insertText: string) => void;
}

interface UseSlashSkillMenuOptions {
  cursorPosition: number;
  draft: string;
  onCursorRestore?: (position: number) => void;
  onDraftChange: (value: string) => void;
}

/** 识别光标处正在输入的 / 命令片段（可在整段文本任意位置触发）。 */
export function activeSlashToken(draft: string, cursorPosition: number): SlashTokenMatch | null {
  const cursor = Math.max(0, Math.min(cursorPosition, draft.length));
  const beforeCursor = draft.slice(0, cursor);
  const match = beforeCursor.match(/(^|\s)(\/[^\s]*)$/);
  if (!match?.[2]) {
    return null;
  }
  const token = match[2];
  const start = beforeCursor.length - token.length;
  return {
    end: start + token.length,
    start,
    token,
  };
}

/** 根据当前 / 片段筛选可插入的已启用技能。 */
export function matchSkillMenuItems(skills: SkillSummary[], token: string): SkillMenuItem[] {
  const normalized = token.toLowerCase();
  const query = normalized.slice(1);

  return skills
    .filter((skill) => skill.enabled)
    .map((skill) => ({
      description: skill.description,
      insertText: insertTextOf(skill),
      label: displayLabelOf(skill),
      name: skill.name,
    }))
    .filter((item) => {
      if (!query) {
        return true;
      }
      const haystack = [
        item.name,
        item.description,
        item.insertText,
        item.label,
      ].join(" ").toLowerCase();
      return haystack.includes(query)
        || item.label.toLowerCase().startsWith(normalized)
        || item.insertText.toLowerCase().includes(query);
    })
    .slice(0, 8);
}

export function replaceSlashToken(
  draft: string,
  match: SlashTokenMatch,
  insertText: string,
): { nextDraft: string; nextCursor: number } {
  const nextDraft = `${draft.slice(0, match.start)}${insertText} ${draft.slice(match.end)}`;
  const nextCursor = match.start + insertText.length + 1;
  return { nextDraft, nextCursor };
}

export function useSlashSkillMenu({
  cursorPosition,
  draft,
  onCursorRestore,
  onDraftChange,
}: UseSlashSkillMenuOptions) {
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const skillsQuery = useQuery({ queryKey: ["skills"], queryFn: listSkills });
  const skills = skillsQuery.data?.items ?? [];
  const slashMatch = activeSlashToken(draft, cursorPosition);
  const slashToken = slashMatch?.token ?? null;
  const items = useMemo(
    () => (slashToken === null ? [] : matchSkillMenuItems(skills, slashToken)),
    [skills, slashToken],
  );
  const safeHighlight = items.length
    ? Math.min(highlightedIndex, items.length - 1)
    : 0;

  const insertSkill = useCallback((insertText: string) => {
    if (!slashMatch) {
      return;
    }
    const { nextDraft, nextCursor } = replaceSlashToken(draft, slashMatch, insertText);
    onDraftChange(nextDraft);
    onCursorRestore?.(nextCursor);
    setHighlightedIndex(0);
  }, [draft, onCursorRestore, onDraftChange, slashMatch]);

  const dismissSlashToken = useCallback(() => {
    if (!slashMatch) {
      return;
    }
    onDraftChange(`${draft.slice(0, slashMatch.start)}${draft.slice(slashMatch.end)}`);
    onCursorRestore?.(slashMatch.start);
    setHighlightedIndex(0);
  }, [draft, onCursorRestore, onDraftChange, slashMatch]);

  const handleKeyDown = useCallback((event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (!slashMatch) {
      return false;
    }
    if (items.length) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setHighlightedIndex((current) => (current + 1) % items.length);
        return true;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setHighlightedIndex((current) => (current - 1 + items.length) % items.length);
        return true;
      }
      if ((event.key === "Enter" && !event.shiftKey) || event.key === "Tab") {
        const selected = items[safeHighlight];
        if (!selected) {
          return false;
        }
        event.preventDefault();
        insertSkill(selected.insertText);
        return true;
      }
    }
    if (event.key === "Escape") {
      event.preventDefault();
      dismissSlashToken();
      return true;
    }
    return false;
  }, [dismissSlashToken, insertSkill, items, safeHighlight, slashMatch]);

  useEffect(() => {
    setHighlightedIndex(0);
  }, [slashToken]);

  return {
    handleKeyDown,
    highlightedIndex: safeHighlight,
    insertSkill,
    isLoading: skillsQuery.isLoading,
    isOpen: slashMatch !== null,
    items,
    setHighlightedIndex,
  };
}

export function SlashSkillMenu({
  highlightedIndex,
  isLoading,
  items,
  onHighlight,
  onSelect,
}: SlashSkillMenuProps) {
  return (
    <div
      aria-label="可选技能"
      className="absolute inset-x-0 bottom-full z-20 mb-1 max-h-40 overflow-y-auto rounded-lg border border-border bg-popover shadow-md"
      role="listbox"
    >
      {isLoading ? (
        <div className="flex items-center gap-1.5 px-2 py-1.5 text-xs text-muted-foreground" role="status">
          <LoaderCircle aria-hidden="true" className="size-3 animate-spin" />
          正在读取技能
        </div>
      ) : null}
      {!isLoading && !items.length ? (
        <p className="px-2 py-1.5 text-xs text-muted-foreground">没有匹配的技能</p>
      ) : null}
      {!isLoading ? items.map((item, index) => (
        <button
          aria-selected={index === highlightedIndex}
          className={cn(
            "flex w-full items-center gap-2 px-2 py-1 text-left",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            index === highlightedIndex ? "bg-primary-soft" : "hover:bg-muted",
          )}
          key={item.name}
          onClick={() => onSelect(item.insertText)}
          onMouseEnter={() => onHighlight(index)}
          role="option"
          title={item.description || undefined}
          type="button"
        >
          <span className="min-w-0 flex-1 truncate text-xs font-medium">{item.name}</span>
          <span className="shrink-0 truncate font-mono text-[11px] text-muted-foreground">{item.label}</span>
        </button>
      )) : null}
    </div>
  );
}

/** 统一成 /命令：优先用技能自带的斜杠触发词，否则用 /技能名。 */
function slashCommandOf(skill: SkillSummary): string {
  const declared = skill.triggers.find((item) => item.startsWith("/"));
  if (declared) {
    return declared;
  }
  return `/${skill.name}`;
}

function displayLabelOf(skill: SkillSummary): string {
  return slashCommandOf(skill);
}

function insertTextOf(skill: SkillSummary): string {
  return slashCommandOf(skill);
}
