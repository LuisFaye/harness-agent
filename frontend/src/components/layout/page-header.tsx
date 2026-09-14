import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function PageHeader({ title, description, icon: Icon, id, children }: {
  title: string;
  description: string;
  icon: LucideIcon;
  id?: string;
  children?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="page-header-heading">
        <span className="page-header-icon" aria-hidden="true"><Icon /></span>
        <div className="min-w-0">
          <h1 id={id}>{title}</h1>
          <p>{description}</p>
        </div>
      </div>
      {children ? <div className="page-header-actions">{children}</div> : null}
    </header>
  );
}
