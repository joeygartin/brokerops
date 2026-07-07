import { Link } from "@tanstack/react-router";
import { type ReactNode } from "react";

// Small shared pieces for the routed views (BOP-025): a back link and a centered
// message block used by detail pages, the not-found route, and role denials.

// Shared styling for the "Open ↗" permalinks the boards render into each entity's
// detail route, making every row addressable (BOP-025).
export const PERMALINK_STYLE = {
  color: "#0969da",
  fontSize: "0.8rem",
  textDecoration: "none",
} as const;

export function BackLink({ to, label }: { to: string; label: string }) {
  return (
    <Link
      to={to}
      style={{
        display: "inline-block",
        margin: "0 auto 1rem",
        color: "#0969da",
        fontSize: "0.85rem",
        textDecoration: "none",
      }}
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
    <div style={{ textAlign: "center", color: "#57606a", padding: "2rem 0" }}>
      <p style={{ fontSize: "1rem", color: "#24292f", margin: "0 0 0.5rem" }}>{title}</p>
      {children}
    </div>
  );
}
