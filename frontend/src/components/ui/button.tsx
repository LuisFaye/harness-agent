import { Button as ButtonPrimitive } from "@base-ui/react/button"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "group/button inline-flex shrink-0 items-center justify-center rounded-xl border border-transparent bg-clip-padding text-[13px] font-medium whitespace-nowrap transition-all duration-150 outline-none select-none active:scale-[0.98] focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/25 disabled:pointer-events-none disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-3.5",
  {
    variants: {
      variant: {
        default:
          "liquid-glass-cta text-white font-semibold",
        outline:
          "liquid-glass-pill text-foreground hover:border-primary/35 hover:bg-white/95 hover:text-primary aria-expanded:bg-white/95 dark:bg-card/80 dark:hover:bg-muted",
        secondary:
          "border-white/75 bg-secondary text-secondary-foreground shadow-2xs hover:bg-white/85 aria-expanded:bg-secondary",
        ghost:
          "hover:bg-white/65 hover:text-foreground aria-expanded:bg-white/70 dark:hover:bg-muted/50",
        destructive:
          "border-destructive/25 bg-destructive/10 text-destructive hover:bg-destructive/18 focus-visible:border-destructive/40 focus-visible:ring-destructive/20",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default:
          "h-8.5 gap-1.5 px-3 has-data-[icon=inline-end]:pr-2.5 has-data-[icon=inline-start]:pl-2.5",
        xs: "h-6 gap-1 rounded-lg px-2 text-[11.5px] in-data-[slot=button-group]:rounded-lg [&_svg:not([class*='size-'])]:size-3",
        sm: "h-7.5 gap-1.5 rounded-lg px-2.5 text-xs in-data-[slot=button-group]:rounded-lg [&_svg:not([class*='size-'])]:size-3.5",
        lg: "h-9.5 gap-2 px-3.5 text-[13.5px]",
        icon: "size-8.5 rounded-xl",
        "icon-xs":
          "size-6 rounded-lg in-data-[slot=button-group]:rounded-lg [&_svg:not([class*='size-'])]:size-3",
        "icon-sm":
          "size-7.5 rounded-lg in-data-[slot=button-group]:rounded-lg",
        "icon-lg": "size-9.5 rounded-xl",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: ButtonPrimitive.Props & VariantProps<typeof buttonVariants>) {
  return (
    <ButtonPrimitive
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button, buttonVariants }
