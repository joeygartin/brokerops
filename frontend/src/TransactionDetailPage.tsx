import { Link, useParams } from "@tanstack/react-router";
import { type ReactNode } from "react";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { ApiError } from "./api";
import { TransactionCard } from "./TransactionsBoard";
import { type ApprovalRequest, type Message, type MutationRecord } from "./client";
import { useTransactionApprovals } from "./hooks/approvals";
import { useTransactionMutations } from "./hooks/audit";
import { useTransactionMessages } from "./hooks/messages";
import { useTransaction } from "./hooks/transactions";
import { BackLink, CenteredMessage, PERMALINK_CLASS } from "./routeElements";

// The transaction hub (BOP-027): one deal, one page. The existing card carries
// the milestones-timeline spine and the Documents tab; below it the hub composes
// the three per-deal slices — comms history, related approvals, and the audit
// trail scoped to this transaction's workflow runs. Every slice is a read (the
// API is the security boundary; viewers read everything), and each related
// approval links out to its /approvals/:id permalink to act on under the
// existing role gate.
export default function TransactionDetailPage() {
  const { id } = useParams({ from: "/transactions/$id" });
  const { data: detail, error, isPending } = useTransaction(id);
  const notFound = error instanceof ApiError && error.status === 404;

  return (
    <section>
      <div className="text-center">
        <BackLink to="/transactions" label="All transactions" />
      </div>
      {isPending && <CenteredMessage title="Loading transaction…" />}
      {notFound && (
        <CenteredMessage title={`No transaction found for ${id}.`}>
          <p className="m-0 text-sm">The link may be out of date.</p>
        </CenteredMessage>
      )}
      {error && !notFound && <p className="text-center text-destructive">{String(error)}</p>}
      {detail && (
        <>
          <TransactionCard detail={detail} />
          <CommsHistory transactionId={id} />
          <RelatedApprovals transactionId={id} />
          <TransactionAudit transactionId={id} />
        </>
      )}
    </section>
  );
}

// Shared frame for a hub slice: a titled card that renders a loading line, an
// error line, its empty message, or the rows — so the three sections read the
// same and each handles its own async state independently of the others.
function HubSection({
  title,
  isPending,
  error,
  isEmpty,
  emptyLabel,
  children,
}: {
  title: string;
  isPending: boolean;
  error: unknown;
  isEmpty: boolean;
  emptyLabel: string;
  children: ReactNode;
}) {
  return (
    <Card as="section" className="mx-auto mb-4 max-w-[760px] text-left">
      <CardHeader>
        <strong>{title}</strong>
      </CardHeader>
      <CardContent>
        {isPending ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : error ? (
          <p className="text-sm text-destructive">{String(error)}</p>
        ) : isEmpty ? (
          <p className="text-sm text-muted-foreground">{emptyLabel}</p>
        ) : (
          children
        )}
      </CardContent>
    </Card>
  );
}

const MESSAGE_STATUS_VARIANT: Record<string, BadgeProps["variant"]> = {
  sent: "success",
  delivered: "success",
  failed: "destructive",
  rejected: "destructive",
  pending_approval: "warning",
  drafted: "secondary",
};

function CommsHistory({ transactionId }: { transactionId: string }) {
  const { data: messages = [], error, isPending } = useTransactionMessages(transactionId);
  return (
    <HubSection
      title="Comms history"
      isPending={isPending}
      error={error}
      isEmpty={messages.length === 0}
      emptyLabel="No messages sent about this transaction yet."
    >
      <ul className="m-0 list-none p-0">
        {messages.map((message: Message) => (
          <li
            key={message.id}
            className="flex items-baseline gap-3 border-b border-muted py-1.5 text-sm"
          >
            <Badge variant="info" className="uppercase">
              {message.channel}
            </Badge>
            {/* The comms row is channel / status / recipient by contract (BOP-027);
                subject and body are role-restricted content, not shown here. */}
            <span className="flex-1">
              <strong>{message.recipient}</strong>
            </span>
            <Badge variant={MESSAGE_STATUS_VARIANT[message.status ?? ""] ?? "secondary"}>
              {(message.status ?? "drafted").replace(/_/g, " ")}
            </Badge>
          </li>
        ))}
      </ul>
    </HubSection>
  );
}

const APPROVAL_STATUS_VARIANT: Record<string, BadgeProps["variant"]> = {
  pending: "warning",
  approved: "success",
  rejected: "destructive",
};

// Strip the "approve_" verb and underscores so a gate kind reads as a label
// (approve_outbound_message → "outbound message"); unknown kinds still prettify.
function prettyKind(kind: string): string {
  return kind.replace(/^approve_/, "").replace(/_/g, " ");
}

function RelatedApprovals({ transactionId }: { transactionId: string }) {
  const { data: approvals = [], error, isPending } = useTransactionApprovals(transactionId);
  return (
    <HubSection
      title="Related approvals"
      isPending={isPending}
      error={error}
      isEmpty={approvals.length === 0}
      emptyLabel="No approvals raised for this transaction yet."
    >
      <ul className="m-0 list-none p-0">
        {approvals.map((approval: ApprovalRequest) => (
          <li
            key={approval.id}
            className="flex items-baseline gap-3 border-b border-muted py-1.5 text-sm"
          >
            <Badge variant={APPROVAL_STATUS_VARIANT[approval.status ?? ""] ?? "secondary"}>
              {approval.status ?? "pending"}
            </Badge>
            <span className="flex-1 capitalize">{prettyKind(approval.kind)}</span>
            <Link
              to="/approvals/$id"
              params={{ id: approval.id }}
              className={PERMALINK_CLASS}
            >
              Open ↗
            </Link>
          </li>
        ))}
      </ul>
    </HubSection>
  );
}

const INTEGRATION_LABELS: Record<string, string> = {
  followupboss: "FollowUpBoss",
  vapi: "Vapi",
};

// The hub renders a COMPACT audit row — what action ran, on which system, its
// outcome, by whom, when — deliberately NOT the raw `record.args` blob the
// dedicated Audit-trail tab shows (BOP-027 review r3). The per-deal slice is a
// viewer-open review surface, so it stays a metadata summary and never echoes a
// mutation's argument payload back into a per-transaction view.
function TransactionAuditRow({ record }: { record: MutationRecord }) {
  const created = record.created_at ? new Date(record.created_at).toLocaleString() : "";
  return (
    <li className="flex items-baseline gap-3 border-b border-muted py-1.5 text-sm">
      <Badge variant={record.outcome === "success" ? "successSoft" : "dangerSoft"}>
        {record.outcome}
      </Badge>
      <span className="flex-1 font-mono">
        {INTEGRATION_LABELS[record.integration] ?? record.integration} · {record.tool}
      </span>
      <span className="whitespace-nowrap text-xs text-muted-foreground">
        {record.actor ? `${record.actor} · ` : ""}
        {created}
      </span>
    </li>
  );
}

function TransactionAudit({ transactionId }: { transactionId: string }) {
  const { data: records = [], error, isPending } = useTransactionMutations(transactionId);
  return (
    <HubSection
      title="Audit trail"
      isPending={isPending}
      error={error}
      isEmpty={records.length === 0}
      emptyLabel="No recorded actions for this transaction yet."
    >
      <ul className="m-0 list-none p-0">
        {records.map((record) => (
          <TransactionAuditRow key={record.id} record={record} />
        ))}
      </ul>
    </HubSection>
  );
}
