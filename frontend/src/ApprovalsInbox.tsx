import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "./authContext";
import { type ApprovalRequest } from "./client";
import { useApprovals, useDecideApproval } from "./hooks/approvals";
import { PERMALINK_CLASS } from "./routeElements";

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
      <h3 className="mb-1 mt-2 font-semibold">{draft.headline}</h3>
      <p className="whitespace-pre-wrap text-sm text-foreground">{draft.body}</p>
      <div className="my-2 flex flex-wrap gap-1.5">
        {draft.channels.map((channel) => (
          <Badge key={channel} variant="info">
            {channel}
          </Badge>
        ))}
      </div>
    </>
  );
}

function EscalationPreview({ approval }: { approval: ApprovalRequest }) {
  const milestones = payloadOf(approval).milestones ?? [];
  return (
    <ul className="my-2 list-disc pl-5">
      {milestones.map((m) => (
        <li key={m.id} className="mb-1 text-sm">
          <strong className="text-destructive">
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
    <div className="my-2 text-left text-sm">
      <p className="mb-1 text-foreground">
        To <strong>{payload.recipient}</strong>
        <Badge variant="info" className="ml-2">
          {payload.channel}
        </Badge>
      </p>
      {payload.subject && <p className="mb-1 font-semibold">{payload.subject}</p>}
      <Textarea
        aria-label="Draft body"
        value={editedBody}
        onChange={(event) => onEditBody(event.target.value)}
        readOnly={!canEdit}
        rows={7}
      />
      {canEdit && editedBody.trim() === "" ? (
        <p className="mt-1 text-xs text-destructive">
          The draft body is empty — nothing would send. Restore some text to approve, or
          Reject to discard the draft.
        </p>
      ) : (
        <p className="mt-1 text-xs text-muted-foreground">
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
    <div className="my-2 text-sm">
      <p className="mb-1 font-semibold text-destructive">🔥 {payload.reason}</p>
      <p className="m-0 text-foreground">{payload.summary}</p>
      <p className="mt-1 text-xs text-muted-foreground">
        Contact {payload.contact_id} · call {payload.call_id}
      </p>
    </div>
  );
}

export function ApprovalCard({
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
    <Card as="article" className="mx-auto mb-4 max-w-2xl text-left">
      <CardHeader>
        <strong>{subject}</strong>
        <span className="text-xs text-muted-foreground">
          thread {approval.graph_thread_id.slice(0, 8)}
        </span>
      </CardHeader>
      <CardContent>
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
      </CardContent>
      {hasRole("admin") ? (
        <CardFooter>
          <Button
            variant="success"
            size="sm"
            onClick={() => decide("approved")}
            disabled={busy || blankDraftBody}
          >
            Approve
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={() => decide("rejected")}
            disabled={busy}
          >
            Reject
          </Button>
        </CardFooter>
      ) : (
        <CardFooter>
          <p className="text-xs text-muted-foreground">Awaiting an admin decision.</p>
        </CardFooter>
      )}
    </Card>
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
      {error && <p className="text-center text-destructive">{String(error)}</p>}
      {notice && (
        <p className="mx-auto mb-4 max-w-2xl rounded-md bg-success-soft p-2 text-center text-success-soft-foreground">
          {notice}
        </p>
      )}
      {isPending ? (
        <p className="text-center text-muted-foreground">Loading approvals…</p>
      ) : approvals.length === 0 ? (
        <p className="text-center text-muted-foreground">
          No pending approvals. Start a marketing workflow from the Listings tab.
        </p>
      ) : (
        approvals.map((approval) => (
          <div key={approval.id} className="mx-auto max-w-2xl">
            <div className="mb-1 text-right">
              <Link to="/approvals/$id" params={{ id: approval.id }} className={PERMALINK_CLASS}>
                Open ↗
              </Link>
            </div>
            <ApprovalCard approval={approval} onDecided={handleDecided} />
          </div>
        ))
      )}
    </>
  );
}
