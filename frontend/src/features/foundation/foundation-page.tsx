import { Check, Construction } from "lucide-react";

interface FoundationPageProps {
  eyebrow: string;
  title: string;
  description: string;
  items: readonly string[];
}

export function FoundationPage({
  eyebrow,
  title,
  description,
  items,
}: FoundationPageProps) {
  return (
    <section aria-labelledby="foundation-title" className="mx-auto w-full max-w-5xl">
      <header className="max-w-3xl">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">{eyebrow}</p>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight" id="foundation-title">
          {title}
        </h1>
        <p className="mt-2 text-base text-muted-foreground">{description}</p>
      </header>

      <div className="mt-8 overflow-hidden rounded-2xl border bg-card text-card-foreground">
        <div className="flex items-start gap-3 border-b bg-muted/40 p-5">
          <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-accent text-accent-foreground">
            <Construction aria-hidden="true" className="size-5" />
          </span>
          <div>
            <h2 className="font-semibold">基础设施已开始迁移</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              当前页面不会替代旧版业务功能。每项能力会在接入真实 API 和运行事件并通过验收后开放。
            </p>
          </div>
        </div>
        <ul className="grid gap-px bg-border sm:grid-cols-2 lg:grid-cols-3">
          {items.map((item) => (
            <li className="flex min-h-16 items-center gap-3 bg-card px-5 py-4" key={item}>
              <Check aria-hidden="true" className="size-4 shrink-0 text-success" />
              <span className="text-sm font-medium">{item}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
