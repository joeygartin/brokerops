import { useState } from "react";
import { useAuth } from "./authContext";
import { type ApprovalRequest } from "./client";
import { useApprovals, useDecideApproval } from "./hooks/approvals";

// The backend deliberately types ApprovalRequest.payload as an open dict — the
// HITL spine carries every approval kind through one model, discriminated by
// `kind` at runtime — so the generated type is `{ [key: string]: unknown }` and
// this typed view of the kinds the inbox renders stays frontend-owned.
type MarketingDraft = {
  listing_key: string;
  headline: string;
  body: string;
  channels: string[];
};

type EscalationMilestone = {
  id: string;
  title: string;
  due_date: string;
  days_overdue: number;
  escalation_level: number;
  note: string;
};

type ApprovalPayload = {
  kind: string;
  listing_key?: string;
  draft?: MarketingDraft;
  transaction_id?: string;
  milestones?: EscalationMilestone[];
  contact_id?: string;
  call_id?: string;
  reason?: string;
  summary?: string;
  // approve_outbound_message (BOP-019): a drafted email awaiting the human
  // decision — the body is editable in the card and the edited text is what sends.
  message_id?: string;
  channel?: string;
  recipient?: string;
  subject?: string;
  body?: string;
  template_ref?: string;
};

function payloadOf(approval: ApprovalRequest): ApprovalPayload {
  return approval.payload as ApprovalPayload;
}

function MarketingPreview({ approval }: { approval: ApprovalRequest }) {
  const draft = payloadOf(approval).draft;
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
  const milestones = payloadOf(approval).milestones ?? [];
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

function OutboundMessagePreview({
  approval,
  editedBody,
  onEditBody,
  canEdit,
}: {
  approval: ApprovalRequest;
  editedBody: string;
  onEditBody: (body: string) => void;
  canEdit: boolean;
}) {
  const payload = payloadOf(approval);
  return (
    <div style={{ margin: "0.6rem 0", fontSize: "0.9rem", textAlign: "left" }}>
      <p style={{ margin: "0 0 0.3rem", color: "#24292f" }}>
        To <strong>{payload.recipient}</strong>
        <span
          style={{
            display: "inline-block",
            background: "#ddf4ff",
            color: "#0969da",
            borderRadius: 999,
            padding: "0.1rem 0.6rem",
            fontSize: "0.75rem",
            marginLeft: "0.5rem",
          }}
        >
          {payload.channel}
        </span>
      </p>
      {payload.subject && (
        <p style={{ margin: "0 0 0.3rem", fontWeight: 600 }}>{payload.subject}</p>
      )}
      <textarea
        aria-label="Draft body"
        value={editedBody}
        onChange={(event) => onEditBody(event.target.value)}
        readOnly={!canEdit}
        rows={7}
        style={{
          width: "100%",
          boxSizing: "border-box",
          fontFamily: "inherit",
          fontSize: "0.9rem",
          color: "#24292f",
          border: "1px solid #d0d7de",
          borderRadius: 6,
          padding: "0.5rem",
        }}
      />
      {canEdit && editedBody.trim() === "" ? (
        <p style={{ color: "#cf222e", fontSize: "0.8rem", margin: "0.3rem 0 0" }}>
          The draft body is empty — nothing would send. Restore some text to approve, or
          Reject to discard the draft.
        </p>
      ) : (
        <p style={{ color: "#57606a", fontSize: "0.8rem", margin: "0.3rem 0 0" }}>
          {canEdit
            ? "Edit the draft before approving — the text above is exactly what sends."
            : "The draft text above is what an admin can approve and send."}
        </p>
      )}
    </div>
  );
}

function HotLeadPreview({ approval }: { approval: ApprovalRequest }) {
  const payload = payloadOf(approval);
  return (
    <div style={{ margin: "0.6rem 0", fontSize: "0.9rem" }}>
      <p style={{ color: "#cf222e", fontWeight: 600, margin: "0 0 0.3rem" }}>
        🔥 {payload.reason}
      </p>
      <p style={{ color: "#24292f", margin: 0 }}>{payload.summary}</p>
      <p style={{ color: "#57606a", fontSize: "0.8rem", margin: "0.3rem 0 0" }}>
        Contact {payload.contact_id} · call {payload.call_id}
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
  const { hasRole } = useAuth();
  const decideMutation = useDecideApproval();
  const busy = decideMutation.isPending;
  const payload = payloadOf(approval);
  const isEscalation = approval.kind === "approve_escalation";
  const isHotLead = approval.kind === "notify_agent";
  const isOutboundMessage = approval.kind === "approve_outbound_message";
  // The editable draft body (approve_outbound_message): approving carries any
  // edits through as edited_payload, so the human decision includes the final text.
  const [editedBody, setEditedBody] = useState(payload.body ?? "");
  // An emptied draft can't be approved — the card promises the visible text is
  // exactly what sends, and blank means "nothing would send". Reject instead.
  const blankDraftBody = isOutboundMessage && editedBody.trim() === "";
  const subject = isEscalation
    ? `Escalate overdue milestones — ${payload.transaction_id} (${payload.listing_key})`
    : isHotLead
      ? `Hot lead — notify listing agent (${payload.listing_key})`
      : isOutboundMessage
        ? `Approve outbound ${payload.channel ?? "message"} — ${payload.recipient}`
        : `Approve marketing — ${payload.listing_key}`;

  const decide = async (decision: "approved" | "rejected") => {
    const editedPayload =
      isOutboundMessage && decision === "approved" && editedBody !== (payload.body ?? "")
        ? { body: editedBody }
        : undefined;
    try {
      const outcome = await decideMutation.mutateAsync({
        approvalId: approval.id,
        body: editedPayload ? { decision, edited_payload: editedPayload } : { decision },
      });
      // The workflow output is an open dict on the wire (engine-specific), so
      // the task-count view of it stays a local cast.
      const output = outcome.workflow.output as
        | {
            fub_task_ids?: string[];
            escalated_task_ids?: string[];
            hot_task_id?: string;
          }
        | null
        | undefined;
      const taskCount =
        output?.fub_task_ids?.length ??
        output?.escalated_task_ids?.length ??
        (output?.hot_task_id ? 1 : undefined);
      const target = isOutboundMessage
        ? (payload.recipient ?? payload.listing_key)
        : (payload.transaction_id ?? payload.listing_key);
      onDecided(
        `${target} ${decision} — workflow status: ${outcome.workflow.status}` +
          (taskCount ? `, ${taskCount} CRM task(s) created.` : "."),
      );
    } catch (cause) {
      onDecided(`Failed to decide ${approval.id}: ${String(cause)}`);
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
      ) : isOutboundMessage ? (
        <OutboundMessagePreview
          approval={approval}
          editedBody={editedBody}
          onEditBody={setEditedBody}
          canEdit={hasRole("admin") && !busy}
        />
      ) : (
        <MarketingPreview approval={approval} />
      )}
      {hasRole("admin") ? (
        <div style={{ display: "flex", gap: "0.6rem", marginTop: "0.6rem" }}>
          <button
            onClick={() => decide("approved")}
            disabled={busy || blankDraftBody}
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
  const { data: approvals = [], error, isPending } = useApprovals();
  const [notice, setNotice] = useState<string | null>(null);

  // The decide mutation invalidates the approvals query, so a decision refreshes
  // the inbox without a manual refetch here — this just surfaces the outcome.
  const handleDecided = (message: string) => setNotice(message);

  return (
    <>
      {error && <p style={{ textAlign: "center", color: "#cf222e" }}>{String(error)}</p>}
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
      {isPending ? (
        <p style={{ textAlign: "center", color: "#57606a" }}>Loading approvals…</p>
      ) : approvals.length === 0 ? (
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
