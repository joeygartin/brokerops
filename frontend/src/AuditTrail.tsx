import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "./auth";
import { API_BASE, MutationRecord } from "./types";

const INTEGRATION_LABELS: Record<string, string> = {
  followupboss: "FollowUpBoss",
  vapi: "Vapi",
};

function OutcomeBadge({ outcome }: { outcome: MutationRecord["outcome"] }) {
  const ok = outcome === "success";
  return (
    <span
      style={{
        textTransform: "uppercase",
        fontSize: "0.7rem",
        fontWeight: 600,
        letterSpacing: "0.03em",
        padding: "0.1rem 0.5rem",
        borderRadius: 999,
        background: ok ? "#dafbe1" : "#ffebe9",
        color: ok ? "#1a7f37" : "#cf222e",
      }}
    >
      {outcome}
    </span>
  );
}

function MutationRow({ record }: { record: MutationRecord }) {
  return (
    <article
      style={{
        border: "1px solid #d0d7de",
        borderRadius: 8,
        padding: "0.8rem 1rem",
        textAlign: "left",
        background: "#fff",
        maxWidth: 760,
        margin: "0 auto 0.7rem",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <strong style={{ fontFamily: "ui-monospace, monospace" }}>
          {INTEGRATION_LABELS[record.integration] ?? record.integration} · {record.tool}
        </strong>
        <OutcomeBadge outcome={record.outcome} />
      </div>
      <p style={{ color: "#57606a", fontSize: "0.8rem", margin: "0.3rem 0" }}>
        {record.workflow} · run {record.workflow_run_id.slice(0, 8)}
        {record.approval_id ? ` · approval ${record.approval_id.slice(0, 8)}` : ""}
        {record.actor ? ` · by ${record.actor}` : ""}
      </p>
      <pre
        style={{
          background: "#f6f8fa",
          borderRadius: 6,
          padding: "0.5rem",
          fontSize: "0.78rem",
          overflowX: "auto",
          margin: "0.3rem 0",
        }}
      >
        {JSON.stringify(record.args, null, 2)}
      </pre>
      <p style={{ color: "#57606a", fontSize: "0.78rem", margin: 0 }}>
        {record.external_id ? `external id ${record.external_id} · ` : ""}
        {new Date(record.created_at).toLocaleString()}
      </p>
      {record.error && (
        <p style={{ color: "#cf222e", fontSize: "0.8rem", margin: "0.3rem 0 0" }}>{record.error}</p>
      )}
    </article>
  );
}

export default function AuditTrail() {
  const [records, setRecords] = useState<MutationRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [runFilter, setRunFilter] = useState("");

  const refresh = useCallback(() => {
    const query = runFilter.trim() ? `?workflow_run_id=${encodeURIComponent(runFilter.trim())}` : "";
    apiFetch(`${API_BASE}/audit${query}`)
      .then((response) => {
        if (!response.ok) throw new Error(`api returned ${response.status}`);
        return response.json() as Promise<MutationRecord[]>;
      })
      .then(setRecords)
      .catch((cause) => setError(String(cause)));
  }, [runFilter]);

  useEffect(refresh, [refresh]);

  return (
    <>
      <div
        style={{ display: "flex", gap: "0.6rem", justifyContent: "center", marginBottom: "1.2rem" }}
      >
        <input
          value={runFilter}
          onChange={(event) => setRunFilter(event.target.value)}
          placeholder="Filter by workflow run id"
          style={{
            padding: "0.4rem 0.7rem",
            borderRadius: 6,
            border: "1px solid #d0d7de",
            minWidth: 320,
          }}
        />
        <button
          onClick={refresh}
          style={{
            padding: "0.4rem 1.1rem",
            borderRadius: 6,
            border: "1px solid #d0d7de",
            background: "#fff",
            cursor: "pointer",
          }}
        >
          Refresh
        </button>
      </div>
      {error && <p style={{ textAlign: "center", color: "#cf222e" }}>{error}</p>}
      {records.length === 0 ? (
        <p style={{ textAlign: "center", color: "#57606a" }}>
          No recorded actions yet. Approve a workflow to see its CRM writes here.
        </p>
      ) : (
        records.map((record) => <MutationRow key={record.id} record={record} />)
      )}
    </>
  );
}
