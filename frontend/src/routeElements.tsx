import { Link } from "@tanstack/react-router";
import { type ReactNode } from "react";
import { cn } from "@/lib/utils";

// Small shared pieces for the routed views (BOP-025). Restyled onto design
// tokens in BOP-026 — the "Open ↗" permalinks and back links are token-blue,
// underline-on-hover; the centered message block reads as muted body text.

// Shared class for the "Open ↗" permalinks the boards render into each entity's
// detail route, making every row addressable (BOP-025).
export const PERMALINK_CLASS = "text-xs text-primary no-underline hover:underline";

export function BackLink({ to, label }: { to: string; label: string }) {
  return (
    <Link
      to={to}
      className="mb-4 inline-block text-sm text-primary no-underline hover:underline"
    >
      ← {label}
    </Link>
  );
}

export function CenteredMessage({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className={cn("py-8 text-center text-muted-foreground")}>
      <p className="mb-2 text-base text-foreground">{title}</p>
      {children}
    </div>
  );
}
