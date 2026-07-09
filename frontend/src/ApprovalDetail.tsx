import { useParams } from "@tanstack/react-router";
import { useState } from "react";
import { ApiError } from "./api";
import { ApprovalCard } from "./ApprovalsInbox";
import { useApproval } from "./hooks/approvals";
import { BackLink, CenteredMessage } from "./routeElements";

// The deep-link target for notification emails (BOP-025): a permalink to one
// approval card. Resolves through the keyed GET /approvals/{id}, which returns
// the row at ANY status, so this distinguishes a still-pending approval (render
// the card) from one already decided (a clean "no longer pending" state — the
// case an emailed link hits after someone else acted). An unknown id 404s into
// that same clean state; never a crash.
export default function ApprovalDetail() {
  const { id } = useParams({ from: "/approvals/$id" });
  const { data: approval, error, isPending } = useApproval(id);
  const [notice, setNotice] = useState<string | null>(null);
  const missing = error instanceof ApiError && error.status === 404;
  const decided = approval != null && approval.status !== "pending";
  const noLongerPending = missing || decided;

  return (
    <section className="mx-auto max-w-2xl">
      <div className="text-center">
        <BackLink to="/approvals" label="All approvals" />
      </div>
      {isPending && <CenteredMessage title="Loading approval…" />}
      {noLongerPending && (
        <CenteredMessage title="This approval is no longer pending.">
          <p className="m-0 text-sm">
            It may have already been decided, or the link is out of date.
          </p>
        </CenteredMessage>
      )}
      {error && !missing && <p className="text-center text-destructive">{String(error)}</p>}
      {notice && (
        <p className="mb-4 rounded-md bg-success-soft p-2 text-center text-success-soft-foreground">
          {notice}
        </p>
      )}
      {/* Key by id: ApprovalCard seeds its react-hook-form default only on mount,
          so moving between two cached permalinks must remount it — otherwise the
          previous approval's draft body would carry over and could send as this
          one's edited_payload (BOP-028 review-gate r6). */}
      {approval && !decided && (
        <ApprovalCard key={approval.id} approval={approval} onDecided={setNotice} />
      )}
    </section>
  );
}
