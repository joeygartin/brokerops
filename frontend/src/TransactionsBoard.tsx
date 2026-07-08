import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import TransactionDocuments from "./TransactionDocuments";
import { type MilestoneView, type TransactionDetail } from "./client";
import { useRunMilestoneCron, useSeedDemo, useTransactions } from "./hooks/transactions";
import { PERMALINK_CLASS } from "./routeElements";

const CLASS_BADGE: Record<string, { variant: BadgeProps["variant"]; label: string }> = {
  overdue: { variant: "destructive", label: "OVERDUE" },
  due_soon: { variant: "warning", label: "DUE SOON" },
  blocked_external: { variant: "blocked", label: "BLOCKED" },
  on_track: { variant: "success", label: "ON TRACK" },
  complete: { variant: "secondary", label: "COMPLETE" },
  waived: { variant: "secondary", label: "WAIVED" },
};

function MilestoneRow({ milestone }: { milestone: MilestoneView }) {
  const badge = CLASS_BADGE[milestone.classification] ?? CLASS_BADGE.on_track;
  return (
    <li className="flex items-baseline gap-3 border-b border-muted py-1.5">
      <Badge variant={badge.variant}>{badge.label}</Badge>
      <span className="flex-1">
        {milestone.title}
        {milestone.blocked_reason && (
          <em className="text-xs text-blocked"> — {milestone.blocked_reason}</em>
        )}
        {(milestone.escalation_level ?? 0) > 0 && (
          <strong className="text-xs text-destructive">
            {" "}
            (escalation L{milestone.escalation_level})
          </strong>
        )}
        {milestone.expected_document && (
          <span
            className={cn(
              "ml-2 text-xs",
              milestone.document_satisfied ? "text-success" : "text-warning",
            )}
          >
            {milestone.document_satisfied
              ? "📄 doc attached"
              : `📄 needs ${milestone.expected_document.replace(/_/g, " ")}`}
          </span>
        )}
      </span>
      <span className="whitespace-nowrap text-xs text-muted-foreground">
        {milestone.due_date} · {milestone.owner || "unassigned"}
      </span>
    </li>
  );
}

export default function TransactionsBoard() {
  const { data: details = [], error, isPending } = useTransactions();
  const [notice, setNotice] = useState<string | null>(null);
  const seedMutation = useSeedDemo();
  const cronMutation = useRunMilestoneCron();
  const running = cronMutation.isPending;

  const seed = async () => {
    await seedMutation.mutateAsync();
  };

  const runCron = async () => {
    try {
      const summary = await cronMutation.mutateAsync();
      const escalations = summary.results.filter((r) => r.status === "awaiting_approval").length;
      setNotice(
        `Milestone check: ${summary.checked} transaction(s) checked, ` +
          `${escalations} escalation(s) waiting in Approvals, ` +
          `${summary.skipped_pending_escalation} skipped (already pending).`,
      );
    } catch (cause) {
      setNotice(`Cron run failed: ${String(cause)}`);
    }
  };

  return (
    <>
      <div className="mb-4 flex justify-center gap-2">
        <Button onClick={runCron} disabled={running}>
          {running ? "Checking…" : "Run milestone check (cron)"}
        </Button>
        {details.length === 0 && (
          <Button variant="outline" onClick={seed}>
            Seed demo transactions
          </Button>
        )}
      </div>
      {error && <p className="text-center text-destructive">{String(error)}</p>}
      {notice && (
        <p className="mx-auto mb-4 max-w-2xl rounded-md bg-info-soft p-2 text-center text-info-soft-foreground">
          {notice}
        </p>
      )}
      {isPending && <p className="text-center text-muted-foreground">Loading transactions…</p>}
      {!isPending && !error && details.length === 0 && (
        <p className="text-center text-muted-foreground">
          No transactions yet. Seed the demo data to get started.
        </p>
      )}
      {details.map((detail) => (
        <div key={detail.transaction.id} className="mx-auto max-w-[760px]">
          <div className="mb-1 text-right">
            <Link
              to="/transactions/$id"
              params={{ id: detail.transaction.id }}
              className={PERMALINK_CLASS}
            >
              Open ↗
            </Link>
          </div>
          <TransactionCard detail={detail} />
        </div>
      ))}
    </>
  );
}

export function TransactionCard({ detail }: { detail: TransactionDetail }) {
  const { transaction, milestones, documents } = detail;
  const [view, setView] = useState<"timeline" | "documents">("timeline");

  return (
    <Card as="article" className="mx-auto mb-4 max-w-[760px] text-left">
      <CardHeader>
        <strong>
          {transaction.id} — {transaction.listing_key}
        </strong>
        <Badge variant="secondary" className="uppercase">
          {transaction.stage.replace(/_/g, " ")}
        </Badge>
      </CardHeader>
      <CardContent>
        <div className="mb-2 text-sm text-muted-foreground">
          {(transaction.parties ?? [])
            .map((p) => `${p.name} (${p.role.replace(/_/g, " ")})`)
            .join(" · ")}
          {transaction.close_date ? ` — closes ${transaction.close_date}` : ""}
        </div>
        <div className="mb-2 flex gap-1.5">
          <Button
            size="sm"
            variant={view === "timeline" ? "default" : "outline"}
            className={view === "timeline" ? "bg-strong text-strong-foreground hover:bg-strong" : ""}
            onClick={() => setView("timeline")}
          >
            Timeline
          </Button>
          <Button
            size="sm"
            variant={view === "documents" ? "default" : "outline"}
            className={
              view === "documents" ? "bg-strong text-strong-foreground hover:bg-strong" : ""
            }
            onClick={() => setView("documents")}
          >
            Documents ({documents.length})
          </Button>
        </div>
        {view === "timeline" ? (
          <ul className="m-0 list-none p-0">
            {milestones.map((m) => (
              <MilestoneRow key={m.id} milestone={m} />
            ))}
          </ul>
        ) : (
          <TransactionDocuments
            transaction={transaction}
            documents={documents}
            milestones={milestones}
          />
        )}
      </CardContent>
    </Card>
  );
}
