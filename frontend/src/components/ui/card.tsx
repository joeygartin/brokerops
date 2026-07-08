import * as React from "react";
import { cn } from "@/lib/utils";

// shadcn/ui Card family — a bordered surface on the `card` token, plus the
// header/title/content/footer slots the views compose (BOP-026). `as` lets a
// call site keep its original semantic element (the entity cards were
// `<article>` before the restyle) so the restyle stays DOM-semantics-neutral.
type CardProps = React.HTMLAttributes<HTMLElement> & { as?: React.ElementType };
export const Card = React.forwardRef<HTMLElement, CardProps>(
  ({ className, as: Comp = "div", ...props }, ref) => (
    <Comp
      ref={ref}
      className={cn(
        "rounded-lg border border-border bg-card text-card-foreground shadow-sm",
        className,
      )}
      {...props}
    />
  ),
);
Card.displayName = "Card";

export const CardHeader = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("flex items-baseline justify-between gap-3 p-4 pb-2", className)}
    {...props}
  />
));
CardHeader.displayName = "CardHeader";

export const CardTitle = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("font-semibold leading-tight", className)} {...props} />
));
CardTitle.displayName = "CardTitle";

export const CardContent = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("p-4 pt-2", className)} {...props} />
));
CardContent.displayName = "CardContent";

export const CardFooter = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("flex items-center gap-2 p-4 pt-2", className)} {...props} />
));
CardFooter.displayName = "CardFooter";
