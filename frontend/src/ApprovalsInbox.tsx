import { useCallback, useEffect, useState } from "react";
import { API_BASE, ApprovalRequest } from "./types";

const DECIDED_BY = "demo-operator";

function ApprovalCard({
  approval,
  onDecided,
}: {
  approval: ApprovalRequest;
  onDecided: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const draft = approval.payload.draft;

  const decide = async (decision: "approved" | "rejected") => {
    setBusy(true);
    try {
      const response = await fetch(`${API_BASE}/approvals/${approval.id}/decide`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, decided_by: DECIDED_BY }),
      });
      if (!response.ok) throw new Error(`api returned ${response.status}`);
      const outcome = (await response.json()) as {
        workflow: { status: string; output: { fub_task_ids?: string[] } | null };
      };
      const taskCount = outcome.workflow.output?.fub_task_ids?.length;
      onDecided(
        `${approval.payload.listing_key} ${decision} — workflow status: ${outcome.workflow.status}` +
          (taskCount ? `, ${taskCount} CRM tasks created.` : "."),
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
        <strong>Approve marketing — {approval.payload.listing_key}</strong>
        <span style={{ color: "#57606a", fontSize: "0.75rem" }}>
          thread {approval.graph_thread_id.slice(0, 8)}
        </span>
      </div>
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
    </article>
  );
}

export default function ApprovalsInbox() {
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(() => {
    fetch(`${API_BASE}/approvals`)
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
