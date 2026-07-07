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
    <section style={{ maxWidth: 700, margin: "0 auto" }}>
      <div style={{ textAlign: "center" }}>
        <BackLink to="/approvals" label="All approvals" />
      </div>
      {isPending && <CenteredMessage title="Loading approval…" />}
      {noLongerPending && (
        <CenteredMessage title="This approval is no longer pending.">
          <p style={{ fontSize: "0.85rem", margin: 0 }}>
            It may have already been decided, or the link is out of date.
          </p>
        </CenteredMessage>
      )}
      {error && !missing && (
        <p style={{ textAlign: "center", color: "#cf222e" }}>{String(error)}</p>
      )}
      {notice && (
        <p
          style={{
            textAlign: "center",
            color: "#1a7f37",
            background: "#dafbe1",
            borderRadius: 6,
            padding: "0.5rem",
            margin: "0 0 1rem",
          }}
        >
          {notice}
        </p>
      )}
      {approval && !decided && <ApprovalCard approval={approval} onDecided={setNotice} />}
    </section>
  );
}
