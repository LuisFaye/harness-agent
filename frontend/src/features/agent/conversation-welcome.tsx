import type { ReactNode } from "react";

const welcomeSuggestions = [
  {
    title: "研发与工作日报",
    description: "梳理关键进展与待办，生成结构化日报",
    prompt: "我想整理一份工作或研发日报，请先问我今天的关键进展和待办事项，再帮我生成结构化日报。",
  },
  {
    title: "数据查询与分析",
    description: "检查数据库连接与表结构，提炼数据趋势",
    prompt: "请帮我查询并分析数据库中的业务数据，先检查当前已配置的数据库连接与表结构。",
  },
  {
    title: "方案规划与协同",
    description: "结合可用工具与技能，拆解可行方案",
    prompt: "我有一个想法想和你讨论，请结合你当前的工具和技能，帮我规划可行的解决方案。",
  },
  {
    title: "定时与自动任务",
    description: "将目标拆解为步骤或配置周期自动执行",
    prompt: "帮我把一个目标拆成可执行的步骤，或者帮我规划一个定时自动执行的任务。",
  },
];

export function ConversationWelcome({
  children,
  onSelect,
}: {
  children?: ReactNode;
  onSelect: (prompt: string) => void;
}) {
  return (
    <div className="relative mx-auto flex min-h-full w-full max-w-[760px] flex-col items-center justify-center px-4 py-6 text-center sm:px-6 sm:py-10">
      <h2 className="text-xl font-semibold tracking-tight text-foreground sm:text-2xl">
        开始一个真实任务
      </h2>
      <p className="mt-1.5 text-xs text-muted-foreground sm:text-sm">
        直接描述你的目标，或选择下方常见场景快速开始
      </p>

      {children ? <div className="mt-5 w-full">{children}</div> : null}

      <div
        aria-label="对话灵感"
        className="mt-4 grid w-full max-w-[660px] grid-cols-1 gap-2.5 text-left sm:grid-cols-2"
      >
        {welcomeSuggestions.map((item) => (
          <button
            className="group flex flex-col justify-between rounded-xl border border-border/75 bg-card/75 px-3.5 py-3 text-left transition-all duration-150 hover:border-primary/35 hover:bg-card hover:shadow-xs"
            key={item.title}
            onClick={() => onSelect(item.prompt)}
            type="button"
          >
            <strong className="text-[13px] font-medium text-foreground group-hover:text-primary">
              {item.title}
            </strong>
            <span className="mt-1 text-xs leading-relaxed text-muted-foreground">
              {item.description}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
