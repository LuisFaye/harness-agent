import { useQuery } from "@tanstack/react-query";
import {
  Blocks,
  Globe,
  Grid2X2,
  LoaderCircle,
  Search,
  SquareTerminal,
  Wrench,
} from "lucide-react";
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useState,
  type KeyboardEvent,
} from "react";

import { PromptInputButton } from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { listSkills, listTools } from "@/lib/api/management";
import type { SkillSummary, ToolDefinition } from "@/lib/api/management-schemas";
import { cn } from "@/lib/utils";

type CapabilityFilter = "all" | "web" | "code";

interface ComposerCapabilitiesProps {
  draft: string;
  disabled?: boolean;
  onDraftChange: (value: string) => void;
}

export interface ComposerCapabilitiesHandle {
  handleKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => boolean;
}

const SHORTCUTS: Array<{
  id: Exclude<CapabilityFilter, "all">;
  label: string;
  icon: typeof Globe;
}> = [
  { id: "web", label: "联网", icon: Globe },
  { id: "code", label: "运行代码", icon: SquareTerminal },
];

export const ComposerCapabilities = forwardRef<ComposerCapabilitiesHandle, ComposerCapabilitiesProps>(
  function ComposerCapabilities({
    draft,
    disabled = false,
    onDraftChange,
  }, ref) {
  const [open, setOpen] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [filter, setFilter] = useState<CapabilityFilter>("all");
  const [highlightedSlashIndex, setHighlightedSlashIndex] = useState(0);
  const skillsQuery = useQuery({ queryKey: ["skills"], queryFn: listSkills });
  const toolsQuery = useQuery({ queryKey: ["tools"], queryFn: listTools });
  const skills = skillsQuery.data?.items ?? [];
  const tools = toolsQuery.data?.items ?? [];
  const slashToken = leadingSlashToken(draft);
  const slashMatches = useMemo(
    () => (slashToken === null ? [] : matchSlashCommands(skills, slashToken)),
    [skills, slashToken],
  );
  const safeHighlight = slashMatches.length
    ? Math.min(highlightedSlashIndex, slashMatches.length - 1)
    : 0;

  function insertCommand(command: string) {
    onDraftChange(replaceLeadingSlash(draft, command));
    setOpen(false);
    setKeyword("");
    setFilter("all");
    setHighlightedSlashIndex(0);
  }

  useImperativeHandle(ref, () => ({
    handleKeyDown(event) {
      if (!slashMatches.length) {
        return false;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setHighlightedSlashIndex((current) => (current + 1) % slashMatches.length);
        return true;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setHighlightedSlashIndex((current) => (current - 1 + slashMatches.length) % slashMatches.length);
        return true;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        onDraftChange(draft.trim() === slashToken ? "" : draft);
        return true;
      }
      if ((event.key === "Enter" && !event.shiftKey) || event.key === "Tab") {
        const selected = slashMatches[safeHighlight];
        if (!selected) {
          return false;
        }
        event.preventDefault();
        insertCommand(selected.command);
        return true;
      }
      return false;
    },
  }), [draft, onDraftChange, safeHighlight, slashMatches, slashToken]);

  useEffect(() => {
    setHighlightedSlashIndex(0);
  }, [slashToken]);

  function openWithFilter(next: CapabilityFilter) {
    setFilter(next);
    setOpen(true);
  }

  return (
    <>
      {slashMatches.length ? (
        <SlashMenu
          highlightedIndex={safeHighlight}
          items={slashMatches}
          onHighlight={setHighlightedSlashIndex}
          onSelect={insertCommand}
        />
      ) : null}
      <Popover
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) {
            setKeyword("");
            setFilter("all");
          }
        }}
        open={open}
      >
        <PopoverTrigger
          disabled={disabled}
          onClick={() => setFilter("all")}
          render={
            <Button
              aria-expanded={open}
              aria-haspopup="dialog"
              aria-label="选择能力"
              className={chipClassName(open)}
              disabled={disabled}
              type="button"
              variant="ghost"
            />
          }
        >
          <Grid2X2 aria-hidden="true" />
          能力
        </PopoverTrigger>
        <PopoverContent
          align="start"
          aria-label="从能力中心选择可调用能力"
          className="flex w-[min(28rem,calc(100vw-2.5rem))] flex-col p-0"
          side="top"
        >
          <CapabilityPicker
            filter={filter}
            keyword={keyword}
            loading={skillsQuery.isLoading || toolsQuery.isLoading}
            onFilterChange={setFilter}
            onKeywordChange={setKeyword}
            onSelectSkill={insertCommand}
            skills={skills}
            tools={tools}
          />
        </PopoverContent>
      </Popover>
      <CapabilityShortcuts
        disabled={disabled}
        filter={filter}
        onInsertCommand={insertCommand}
        onOpenFilter={openWithFilter}
        open={open}
        skills={skills}
        tools={tools}
      />
    </>
  );
});

function CapabilityShortcuts({
  disabled,
  filter,
  onInsertCommand,
  onOpenFilter,
  open,
  skills,
  tools,
}: {
  disabled: boolean;
  filter: CapabilityFilter;
  onInsertCommand: (command: string) => void;
  onOpenFilter: (filter: CapabilityFilter) => void;
  open: boolean;
  skills: SkillSummary[];
  tools: ToolDefinition[];
}) {
  return (
    <>
      {SHORTCUTS.map((shortcut) => {
        const visible = hasShortcutItems(shortcut.id, skills, tools);
        if (!visible) {
          return null;
        }
        const Icon = shortcut.icon;
        return (
          <PromptInputButton
            aria-label={`查看${shortcut.label}相关能力`}
            className={cn(chipClassName(open && filter === shortcut.id), "hidden sm:inline-flex")}
            disabled={disabled}
            key={shortcut.id}
            onClick={() => {
              const command = preferredSlashCommand(shortcut.id, skills);
              if (command && !open) {
                onInsertCommand(command);
                return;
              }
              onOpenFilter(shortcut.id);
            }}
          >
            <Icon aria-hidden="true" />
            {shortcut.label}
          </PromptInputButton>
        );
      })}
    </>
  );
}

export function leadingSlashToken(draft: string): string | null {
  const match = draft.match(/^(\/[^\s]*)$/);
  return match?.[1] ?? null;
}

export function matchSlashCommands(skills: SkillSummary[], token: string): SlashCommand[] {
  const normalized = token.toLowerCase();
  return skills
    .flatMap((skill) => {
      const command = slashCommandOf(skill);
      if (!command || !skill.enabled) {
        return [];
      }
      return [{ command, description: skill.description, name: skill.name }];
    })
    .filter((item) => item.command.toLowerCase().startsWith(normalized) || item.name.toLowerCase().includes(normalized.slice(1)))
    .slice(0, 8);
}

function CapabilityPicker({
  filter,
  keyword,
  loading,
  onFilterChange,
  onKeywordChange,
  onSelectSkill,
  skills,
  tools,
}: {
  filter: CapabilityFilter;
  keyword: string;
  loading: boolean;
  onFilterChange: (value: CapabilityFilter) => void;
  onKeywordChange: (value: string) => void;
  onSelectSkill: (command: string) => void;
  skills: SkillSummary[];
  tools: ToolDefinition[];
}) {
  const filteredSkills = skills.filter((skill) => matchesFilter(filter, skill, undefined) && matchesKeyword(keyword, skill.name, skill.description));
  const filteredTools = tools.filter((tool) => matchesFilter(filter, undefined, tool) && matchesKeyword(keyword, tool.name, tool.description));

  return (
    <div className="flex max-h-[min(24rem,70svh)] flex-col">
      <div className="border-b border-border px-3 py-2.5">
        <p className="text-sm font-medium">能力中心</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          选择带 / 命令的技能写入对话框；其余能力仍由 Agent 在本轮自动调用。
        </p>
        <label className="relative mt-2 block">
          <span className="sr-only">搜索能力</span>
          <Search aria-hidden="true" className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            className="h-9 w-full rounded-lg border border-border bg-background pr-3 pl-8 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onChange={(event) => onKeywordChange(event.target.value)}
            placeholder="搜索技能、工具或 / 命令"
            value={keyword}
          />
        </label>
        <div className="mt-2 flex gap-1">
          {([
            ["all", "全部"],
            ["web", "联网"],
            ["code", "运行代码"],
          ] as const).map(([value, label]) => (
            <button
              className={cn(
                "h-8 rounded-full px-2.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                filter === value
                  ? "bg-primary-soft text-accent-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
              key={value}
              onClick={() => onFilterChange(value)}
              type="button"
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {loading ? (
          <div className="grid min-h-32 place-items-center text-sm text-muted-foreground" role="status">
            <span className="flex items-center gap-2">
              <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
              正在读取能力
            </span>
          </div>
        ) : null}
        {!loading && !filteredSkills.length && !filteredTools.length ? (
          <p className="grid min-h-32 place-items-center px-3 text-center text-sm text-muted-foreground">
            没有匹配的可调用能力。
          </p>
        ) : null}
        {filteredSkills.length ? (
          <section aria-label="技能">
            <h3 className="flex items-center gap-1.5 px-2 py-1.5 text-xs font-semibold text-muted-foreground">
              <Blocks aria-hidden="true" className="size-3.5" />
              技能
            </h3>
            <ul className="flex flex-col gap-0.5">
              {filteredSkills.map((skill) => {
                const command = slashCommandOf(skill);
                const canInsert = Boolean(command && skill.enabled);
                const meta = command || (skill.callable_by_agent ? "自动调用" : "不可用");
                const body = (
                  <>
                    <span className="min-w-0 flex-1">
                      <strong className="block truncate text-sm font-medium">{skill.name}</strong>
                      <span className="mt-0.5 block line-clamp-2 text-xs text-muted-foreground">
                        {skill.description || "暂无说明"}
                      </span>
                    </span>
                    <span className="mt-0.5 shrink-0 rounded-md bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
                      {meta}
                    </span>
                  </>
                );
                return (
                  <li key={skill.name}>
                    {canInsert ? (
                      <button
                        className="flex min-h-11 w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        onClick={() => onSelectSkill(command!)}
                        type="button"
                      >
                        {body}
                      </button>
                    ) : (
                      <div className="flex min-h-11 w-full items-start gap-2 rounded-lg px-2 py-1.5">
                        {body}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        ) : null}
        {filteredTools.length ? (
          <section aria-label="工具" className="mt-1">
            <h3 className="flex items-center gap-1.5 px-2 py-1.5 text-xs font-semibold text-muted-foreground">
              <Wrench aria-hidden="true" className="size-3.5" />
              Agent 可调用工具
            </h3>
            <ul className="flex flex-col gap-0.5">
              {filteredTools.slice(0, 12).map((tool) => (
                <li
                  className="flex min-h-11 items-start gap-2 rounded-lg px-2 py-1.5"
                  key={tool.tool_id}
                >
                  <span className="min-w-0 flex-1">
                    <strong className="block truncate text-sm font-medium">{tool.name}</strong>
                    <span className="mt-0.5 block line-clamp-2 text-xs text-muted-foreground">
                      {tool.description || "暂无说明"}
                    </span>
                  </span>
                  <span className="mt-0.5 shrink-0 text-[11px] text-muted-foreground">
                    {tool.available ? "可调用" : tool.enabled ? "不可用" : "已停用"}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </div>
      <div className="border-t border-border px-3 py-2">
        <a
          className="inline-flex min-h-9 items-center text-xs font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          href="#/capabilities"
        >
          打开完整能力中心
        </a>
      </div>
    </div>
  );
}

function SlashMenu({
  highlightedIndex,
  items,
  onHighlight,
  onSelect,
}: {
  highlightedIndex: number;
  items: SlashCommand[];
  onHighlight: (index: number) => void;
  onSelect: (command: string) => void;
}) {
  return (
    <div
      aria-label="可选能力命令"
      className="absolute inset-x-0 bottom-full z-20 mb-2 overflow-hidden rounded-xl border border-border bg-popover shadow-lg"
      role="listbox"
    >
      {items.map((item, index) => (
        <button
          aria-selected={index === highlightedIndex}
          className={cn(
            "flex min-h-11 w-full items-start gap-2 px-3 py-2 text-left text-sm",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            index === highlightedIndex ? "bg-primary-soft" : "hover:bg-muted",
          )}
          key={item.command}
          onClick={() => onSelect(item.command)}
          onMouseEnter={() => onHighlight(index)}
          role="option"
          type="button"
        >
          <span className="min-w-0 flex-1">
            <strong className="block truncate font-medium">{item.name}</strong>
            <span className="mt-0.5 block truncate text-xs text-muted-foreground">{item.description}</span>
          </span>
          <span className="shrink-0 font-mono text-xs text-muted-foreground">{item.command}</span>
        </button>
      ))}
    </div>
  );
}

interface SlashCommand {
  command: string;
  description: string;
  name: string;
}

function slashCommandOf(skill: SkillSummary): string | null {
  return skill.triggers.find((item) => item.startsWith("/")) ?? null;
}

function replaceLeadingSlash(draft: string, command: string): string {
  if (/^\/[^\s]*$/.test(draft.trim())) {
    return `${command} `;
  }
  if (!draft.trim()) {
    return `${command} `;
  }
  return `${command} ${draft.trim()} `;
}

function matchesKeyword(keyword: string, name: string, description: string): boolean {
  const normalized = keyword.trim().toLowerCase();
  if (!normalized) {
    return true;
  }
  return `${name} ${description}`.toLowerCase().includes(normalized);
}

function matchesFilter(
  filter: CapabilityFilter,
  skill: SkillSummary | undefined,
  tool: ToolDefinition | undefined,
): boolean {
  if (filter === "all") {
    return true;
  }
  if (skill) {
    return skillMatchesShortcut(filter, skill);
  }
  if (tool) {
    return toolMatchesShortcut(filter, tool);
  }
  return false;
}

function hasShortcutItems(
  id: Exclude<CapabilityFilter, "all">,
  skills: SkillSummary[],
  tools: ToolDefinition[],
): boolean {
  return skills.some((skill) => skillMatchesShortcut(id, skill) && skill.enabled)
    || tools.some((tool) => toolMatchesShortcut(id, tool) && tool.available);
}

function preferredSlashCommand(
  id: Exclude<CapabilityFilter, "all">,
  skills: SkillSummary[],
): string | null {
  const commands = skills
    .filter((skill) => skill.enabled && skillMatchesShortcut(id, skill))
    .map(slashCommandOf)
    .filter((item): item is string => Boolean(item));
  return commands.length === 1 ? commands[0] ?? null : null;
}

function skillMatchesShortcut(id: Exclude<CapabilityFilter, "all">, skill: SkillSummary): boolean {
  const haystack = `${skill.name} ${skill.description} ${(skill.capabilities ?? []).join(" ")} ${skill.triggers.join(" ")}`.toLowerCase();
  if (id === "web") {
    return /search|web|fetch|联网|网页/.test(haystack);
  }
  return /code|shell|python|终端|运行代码/.test(haystack);
}

function toolMatchesShortcut(id: Exclude<CapabilityFilter, "all">, tool: ToolDefinition): boolean {
  const haystack = `${tool.name} ${tool.tool_id} ${tool.description}`.toLowerCase();
  if (id === "web") {
    return /search|web|fetch|联网/.test(haystack);
  }
  return tool.source_type === "shell" || /code|shell|python|终端/.test(haystack);
}

function chipClassName(active: boolean): string {
  return cn(
    "min-h-11 gap-1.5 rounded-full border border-border/80 bg-muted/50 px-2.5 text-xs font-medium text-foreground",
    "hover:bg-muted hover:text-foreground",
    active && "border-primary/40 bg-primary-soft text-accent-foreground",
  );
}
