import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "./auth";
import { useAuth } from "./authContext";
import { API_BASE, ApprovalRequest } from "./types";

function MarketingPreview({ approval }: { approval: ApprovalRequest }) {
  const draft = approval.payload.draft;
  if (!draft) return null;
  return (
    <>
      <h3 style={{ margin: "0.6rem 0 0.3rem" }}>{draft.headline}</h3>
      <p style={{ whiteSpace: "pre-wrap", color: "#24292f", fontSize: "0.9rem" }}>{draft.body}</p>
      <div style={{ margin: "0.5rem 0" }}>
        {draft.channels.map((channel) => (
          <span
            key={channel}
            style={{
              display: "inline-block",
              background: "#ddf4ff",
              color: "#0969da",
              borderRadius: 999,
              padding: "0.1rem 0.6rem",
              fontSize: "0.75rem",
              marginRight: "0.4rem",
            }}
          >
            {channel}
          </span>
        ))}
      </div>
    </>
  );
}

function EscalationPreview({ approval }: { approval: ApprovalRequest }) {
  const milestones = approval.payload.milestones ?? [];
  return (
    <ul style={{ margin: "0.6rem 0", paddingLeft: "1.2rem" }}>
      {milestones.map((m) => (
        <li key={m.id} style={{ marginBottom: "0.35rem", fontSize: "0.9rem" }}>
          <strong style={{ color: "#cf222e" }}>
            {m.title} — {m.days_overdue} day(s) overdue
          </strong>{" "}
          (was due {m.due_date}, escalation level {m.escalation_level})
        </li>
      ))}
    </ul>
  );
}

function HotLeadPreview({ approval }: { approval: ApprovalRequest }) {
  return (
    <div style={{ margin: "0.6rem 0", fontSize: "0.9rem" }}>
      <p style={{ color: "#cf222e", fontWeight: 600, margin: "0 0 0.3rem" }}>
        🔥 {approval.payload.reason}
      </p>
      <p style={{ color: "#24292f", margin: 0 }}>{approval.payload.summary}</p>
      <p style={{ color: "#57606a", fontSize: "0.8rem", margin: "0.3rem 0 0" }}>
        Contact {approval.payload.contact_id} · call {approval.payload.call_id}
      </p>
    </div>
  );
}

function ApprovalCard({
  approval,
  onDecided,
}: {
  approval: ApprovalRequest;
  onDecided: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const { hasRole } = useAuth();
  const isEscalation = approval.kind === "approve_escalation";
  const isHotLead = approval.kind === "notify_agent";
  const subject = isEscalation
    ? `Escalate overdue milestones — ${approval.payload.transaction_id} (${approval.payload.listing_key})`
    : isHotLead
      ? `Hot lead — notify listing agent (${approval.payload.listing_key})`
      : `Approve marketing — ${approval.payload.listing_key}`;

  const decide = async (decision: "approved" | "rejected") => {
    setBusy(true);
    try {
      const response = await apiFetch(`${API_BASE}/approvals/${approval.id}/decide`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision }),
      });
      if (!response.ok) throw new Error(`api returned ${response.status}`);
      const outcome = (await response.json()) as {
        workflow: {
          status: string;
          output: { fub_task_ids?: string[]; escalated_task_ids?: string[] } | null;
        };
      };
      const output = outcome.workflow.output as {
        fub_task_ids?: string[];
        escalated_task_ids?: string[];
        hot_task_id?: string;
      } | null;
      const taskCount =
        output?.fub_task_ids?.length ??
        output?.escalated_task_ids?.length ??
        (output?.hot_task_id ? 1 : undefined);
      const target = approval.payload.transaction_id ?? approval.payload.listing_key;
      onDecided(
        `${target} ${decision} — workflow status: ${outcome.workflow.status}` +
          (taskCount ? `, ${taskCount} CRM task(s) created.` : "."),
      );
    } catch (cause) {
      onDecided(`Failed to decide ${approval.id}: ${String(cause)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <article
      style={{
        border: "1px solid #d0d7de",
        borderRadius: 8,
        padding: "1rem",
        textAlign: "left",
        background: "#fff",
        maxWidth: 700,
        margin: "0 auto 1rem",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <strong>{subject}</strong>
        <span style={{ color: "#57606a", fontSize: "0.75rem" }}>
          thread {approval.graph_thread_id.slice(0, 8)}
        </span>
      </div>
      {isEscalation ? (
        <EscalationPreview approval={approval} />
      ) : isHotLead ? (
        <HotLeadPreview approval={approval} />
      ) : (
        <MarketingPreview approval={approval} />
      )}
      {hasRole("admin") ? (
        <div style={{ display: "flex", gap: "0.6rem", marginTop: "0.6rem" }}>
          <button
            onClick={() => decide("approved")}
            disabled={busy}
            style={{
              padding: "0.4rem 1.1rem",
              borderRadius: 6,
              border: "1px solid #1a7f37",
              background: "#2da44e",
              color: "#fff",
              cursor: busy ? "wait" : "pointer",
            }}
          >
            Approve
          </button>
          <button
            onClick={() => decide("rejected")}
            disabled={busy}
            style={{
              padding: "0.4rem 1.1rem",
              borderRadius: 6,
              border: "1px solid #cf222e",
              background: "#fff",
              color: "#cf222e",
              cursor: busy ? "wait" : "pointer",
            }}
          >
            Reject
          </button>
        </div>
      ) : (
        <p style={{ color: "#57606a", fontSize: "0.8rem", marginTop: "0.6rem" }}>
          Awaiting an admin decision.
        </p>
      )}
    </article>
  );
}

export default function ApprovalsInbox() {
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(() => {
    apiFetch(`${API_BASE}/approvals`)
      .then((response) => {
        if (!response.ok) throw new Error(`api returned ${response.status}`);
        return response.json() as Promise<ApprovalRequest[]>;
      })
      .then(setApprovals)
      .catch((cause) => setError(String(cause)));
  }, []);

  useEffect(refresh, [refresh]);

  const handleDecided = (message: string) => {
    setNotice(message);
    refresh();
  };

  return (
    <>
      {error && <p style={{ textAlign: "center", color: "#cf222e" }}>{error}</p>}
      {notice && (
        <p
          style={{
            textAlign: "center",
            color: "#1a7f37",
            background: "#dafbe1",
            borderRadius: 6,
            padding: "0.5rem",
            maxWidth: 700,
            margin: "0 auto 1rem",
          }}
        >
          {notice}
        </p>
      )}
      {approvals.length === 0 ? (
        <p style={{ textAlign: "center", color: "#57606a" }}>
          No pending approvals. Start a marketing workflow from the Listings tab.
        </p>
      ) : (
        approvals.map((approval) => (
          <ApprovalCard key={approval.id} approval={approval} onDecided={handleDecided} />
        ))
      )}
    </>
  );
}
