import { Moon, Sun } from "lucide-react";

import { useTheme } from "@/app/theme-provider";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const nextTheme = resolvedTheme === "dark" ? "light" : "dark";
  const label = nextTheme === "dark" ? "切换到深色主题" : "切换到浅色主题";

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            aria-label={label}
            onClick={() => setTheme(nextTheme)}
            size="icon-sm"
            variant="ghost"
          />
        }
      >
        {resolvedTheme === "dark" ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}
