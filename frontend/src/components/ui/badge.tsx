import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

// shadcn/ui Badge, widened to the domain's status vocabulary (BOP-026). Solid
// intents for entity status (a listing's `active`, a milestone's `overdue`);
// soft intents for annotations (a channel chip, an audit outcome). Every pair
// clears WCAG AA (tokens.contrast.test.ts).
const badgeVariants = cva(
  "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground",
        secondary: "bg-muted text-muted-foreground",
        strong: "bg-strong text-strong-foreground",
        success: "bg-success text-success-foreground",
        warning: "bg-warning text-warning-foreground",
        destructive: "bg-destructive text-destructive-foreground",
        blocked: "bg-blocked text-blocked-foreground",
        info: "bg-info-soft text-info-soft-foreground",
        successSoft: "bg-success-soft text-success-soft-foreground",
        dangerSoft: "bg-danger-soft text-danger-soft-foreground",
        outline: "border border-border text-foreground",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { badgeVariants };
