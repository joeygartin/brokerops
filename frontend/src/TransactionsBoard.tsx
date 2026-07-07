import { Link } from "@tanstack/react-router";
import { useState } from "react";
import TransactionDocuments from "./TransactionDocuments";
import { type MilestoneView, type TransactionDetail } from "./client";
import { useRunMilestoneCron, useSeedDemo, useTransactions } from "./hooks/transactions";
import { PERMALINK_STYLE } from "./routeElements";

const CLASS_STYLES: Record<string, { color: string; background: string; label: string }> = {
  overdue: { color: "#fff", background: "#cf222e", label: "OVERDUE" },
  due_soon: { color: "#fff", background: "#9a6700", label: "DUE SOON" },
  blocked_external: { color: "#fff", background: "#8250df", label: "BLOCKED" },
  on_track: { color: "#fff", background: "#1a7f37", label: "ON TRACK" },
  complete: { color: "#57606a", background: "#eaeef2", label: "COMPLETE" },
  waived: { color: "#57606a", background: "#eaeef2", label: "WAIVED" },
};

function MilestoneRow({ milestone }: { milestone: MilestoneView }) {
  const style = CLASS_STYLES[milestone.classification] ?? CLASS_STYLES.on_track;
  return (
    <li
      style={{
        display: "flex",
        gap: "0.75rem",
        alignItems: "baseline",
        padding: "0.45rem 0",
        borderBottom: "1px solid #eaeef2",
      }}
    >
      <span
        style={{
          ...style,
          borderRadius: 999,
          padding: "0.1rem 0.55rem",
          fontSize: "0.7rem",
          whiteSpace: "nowrap",
        }}
      >
        {style.label}
      </span>
      <span style={{ flex: 1 }}>
        {milestone.title}
        {milestone.blocked_reason && (
          <em style={{ color: "#8250df", fontSize: "0.8rem" }}> — {milestone.blocked_reason}</em>
        )}
        {(milestone.escalation_level ?? 0) > 0 && (
          <strong style={{ color: "#cf222e", fontSize: "0.8rem" }}>
            {" "}
            (escalation L{milestone.escalation_level})
          </strong>
        )}
        {milestone.expected_document && (
          <span
            style={{
              marginLeft: "0.5rem",
              fontSize: "0.75rem",
              color: milestone.document_satisfied ? "#1a7f37" : "#9a6700",
            }}
          >
            {milestone.document_satisfied
              ? "📄 doc attached"
              : `📄 needs ${milestone.expected_document.replace(/_/g, " ")}`}
          </span>
        )}
      </span>
      <span style={{ color: "#57606a", fontSize: "0.8rem", whiteSpace: "nowrap" }}>
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
      <div style={{ display: "flex", gap: "0.6rem", justifyContent: "center", marginBottom: "1rem" }}>
        <button
          onClick={runCron}
          disabled={running}
          style={{
            padding: "0.4rem 1.1rem",
            borderRadius: 6,
            border: "1px solid #0969da",
            background: "#0969da",
            color: "#fff",
            cursor: running ? "wait" : "pointer",
          }}
        >
          {running ? "Checking…" : "Run milestone check (cron)"}
        </button>
        {details.length === 0 && (
          <button
            onClick={seed}
            style={{
              padding: "0.4rem 1.1rem",
              borderRadius: 6,
              border: "1px solid #d0d7de",
              background: "#fff",
              cursor: "pointer",
            }}
          >
            Seed demo transactions
          </button>
        )}
      </div>
      {error && <p style={{ textAlign: "center", color: "#cf222e" }}>{String(error)}</p>}
      {notice && (
        <p
          style={{
            textAlign: "center",
            color: "#0969da",
            background: "#ddf4ff",
            borderRadius: 6,
            padding: "0.5rem",
            maxWidth: 700,
            margin: "0 auto 1rem",
          }}
        >
          {notice}
        </p>
      )}
      {isPending && (
        <p style={{ textAlign: "center", color: "#57606a" }}>Loading transactions…</p>
      )}
      {!isPending && !error && details.length === 0 && (
        <p style={{ textAlign: "center", color: "#57606a" }}>
          No transactions yet. Seed the demo data to get started.
        </p>
      )}
      {details.map((detail) => (
        <div key={detail.transaction.id} style={{ maxWidth: 760, margin: "0 auto" }}>
          <div style={{ textAlign: "right", marginBottom: "0.25rem" }}>
            <Link
              to="/transactions/$id"
              params={{ id: detail.transaction.id }}
              style={PERMALINK_STYLE}
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

  const viewTabStyle = (active: boolean) => ({
    padding: "0.15rem 0.7rem",
    borderRadius: 6,
    border: "1px solid #d0d7de",
    background: active ? "#24292f" : "#fff",
    color: active ? "#fff" : "#24292f",
    fontSize: "0.75rem",
    cursor: "pointer",
  });

  return (
    <article
      style={{
        border: "1px solid #d0d7de",
        borderRadius: 8,
        padding: "1rem",
        textAlign: "left",
        background: "#fff",
        maxWidth: 760,
        margin: "0 auto 1rem",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <strong>
          {transaction.id} — {transaction.listing_key}
        </strong>
        <span
          style={{
            background: "#eaeef2",
            borderRadius: 999,
            padding: "0.1rem 0.6rem",
            fontSize: "0.75rem",
            textTransform: "uppercase",
          }}
        >
          {transaction.stage.replace(/_/g, " ")}
        </span>
      </div>
      <div style={{ color: "#57606a", fontSize: "0.85rem", margin: "0.3rem 0 0.6rem" }}>
        {(transaction.parties ?? [])
          .map((p) => `${p.name} (${p.role.replace(/_/g, " ")})`)
          .join(" · ")}
        {transaction.close_date ? ` — closes ${transaction.close_date}` : ""}
      </div>
      <div style={{ display: "flex", gap: "0.4rem", marginBottom: "0.6rem" }}>
        <button style={viewTabStyle(view === "timeline")} onClick={() => setView("timeline")}>
          Timeline
        </button>
        <button style={viewTabStyle(view === "documents")} onClick={() => setView("documents")}>
          Documents ({documents.length})
        </button>
      </div>
      {view === "timeline" ? (
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
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
    </article>
  );
}
