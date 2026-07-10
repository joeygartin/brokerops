import { Link } from "@tanstack/react-router";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { type DeadlineRow } from "./client";
import { useDeadlineQueue } from "./hooks/transactions";
import { CenteredMessage, PERMALINK_CLASS } from "./routeElements";

// The coordinator's home (BOP-030): every active transaction's urgent milestones
// in one cross-transaction worklist, sorted most-urgent-first by the server. Each
// row links back to its transaction hub (BOP-027).

const CLASS_BADGE: Record<DeadlineRow["classification"], { variant: BadgeProps["variant"]; label: string }> = {
  overdue: { variant: "destructive", label: "OVERDUE" },
  due_soon: { variant: "warning", label: "DUE SOON" },
  blocked_external: { variant: "blocked", label: "BLOCKED" },
  // The queue only ever carries the three above; on_track is never returned but
  // the map is total so the type stays exhaustive.
  on_track: { variant: "success", label: "ON TRACK" },
};

// Human timing for a milestone, read off its signed days-until-due. Blocked
// milestones lead with the blocker instead — the date isn't the story.
function timing(row: DeadlineRow): string {
  if (row.classification === "blocked_external") {
    return row.blocked_reason ? `Blocked — ${row.blocked_reason}` : "Blocked externally";
  }
  const days = row.days_until_due;
  if (days < 0) return `${Math.abs(days)} day${Math.abs(days) === 1 ? "" : "s"} overdue`;
  if (days === 0) return "Due today";
  return `Due in ${days} day${days === 1 ? "" : "s"}`;
}

function DeadlineItem({ row }: { row: DeadlineRow }) {
  const badge = CLASS_BADGE[row.classification];
  return (
    <Card as="article" className="mx-auto mb-3 max-w-[760px] text-left">
      <CardContent className="flex items-baseline gap-3 p-4">
        <Badge variant={badge.variant}>{badge.label}</Badge>
        <div className="flex-1">
          <div className="font-medium">{row.title}</div>
          <div className="text-xs text-muted-foreground">
            {row.listing_key} · {timing(row)}
          </div>
        </div>
        <Link to="/transactions/$id" params={{ id: row.transaction_id }} className={PERMALINK_CLASS}>
          Open ↗
        </Link>
      </CardContent>
    </Card>
  );
}

export default function DeadlineQueue() {
  const { data: rows = [], error, isPending } = useDeadlineQueue();

  return (
    <>
      <h2 className="mb-4 text-center text-xl font-semibold">Deadline queue</h2>
      {error && <p className="text-center text-destructive">{String(error)}</p>}
      {isPending && <p className="text-center text-muted-foreground">Loading deadlines…</p>}
      {!isPending && !error && rows.length === 0 && (
        <CenteredMessage title="All caught up.">
          <p className="m-0 text-sm">No milestones need attention across your active transactions.</p>
        </CenteredMessage>
      )}
      {rows.map((row) => (
        <DeadlineItem key={row.milestone_id} row={row} />
      ))}
    </>
  );
}
