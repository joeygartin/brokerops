import * as React from "react";
import { cn } from "@/lib/utils";

// A token-styled NATIVE <select> (BOP-026). Deliberately not Radix Select: the
// native control keeps the `combobox` role and full keyboard/screen-reader
// behavior the document-attach form and its tests depend on, and needs no
// portal. Styling only — semantics unchanged.
export const Select = React.forwardRef<HTMLSelectElement, React.ComponentProps<"select">>(
  ({ className, ...props }, ref) => (
    <select
      ref={ref}
      className={cn(
        "h-9 rounded-md border border-input bg-card px-3 text-sm text-foreground shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  ),
);
Select.displayName = "Select";
