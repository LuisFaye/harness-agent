import { ArrowUpRight, FileText, Lightbulb, ListChecks } from "lucide-react";

import { AgentMark } from "@/components/layout/agent-mark";

const suggestions = [
  { icon: FileText, title: "整理与表达", description: "让零散内容清晰起来", prompt: "我想整理一份材料，请先问我材料的内容和用途，再帮我提炼重点。" },
  { icon: Lightbulb, title: "探索一个想法", description: "从问题走向新的思路", prompt: "我有一个想法想和你讨论，请先问我想解决什么问题，再一起探索可行的方向。" },
  { icon: ListChecks, title: "拆解一个任务", description: "把目标变成可执行的步骤", prompt: "帮我把一个目标拆成可执行的步骤，请先问我目标、截止时间和已有条件。" },
];

export function ConversationWelcome({ onSelect }: { onSelect: (prompt: string) => void }) {
  return (
    <div className="conversation-welcome">
      <AgentMark className="welcome-mark" />
      <p className="welcome-eyebrow">你的 AI 工作伙伴</p>
      <h2>今天，一起完成什么？</h2>
      <p className="welcome-description">从一个问题、一份文档或一个想法开始。</p>
      <div className="welcome-suggestions" aria-label="对话灵感">
        {suggestions.map(({ icon: Icon, title, description, prompt }) => (
          <button key={title} type="button" onClick={() => onSelect(prompt)}>
            <span className="welcome-suggestion-top"><Icon aria-hidden="true" /><ArrowUpRight aria-hidden="true" /></span>
            <strong>{title}</strong>
            <span>{description}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
