import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { type MutationRecord } from "./client";
import { useMutations } from "./hooks/audit";

const INTEGRATION_LABELS: Record<string, string> = {
  followupboss: "FollowUpBoss",
  vapi: "Vapi",
};

function OutcomeBadge({ outcome }: { outcome: MutationRecord["outcome"] }) {
  const ok = outcome === "success";
  return (
    <Badge variant={ok ? "successSoft" : "dangerSoft"} className="uppercase tracking-wide">
      {outcome}
    </Badge>
  );
}

function MutationRow({ record }: { record: MutationRecord }) {
  return (
    <Card as="article" className="mx-auto mb-3 max-w-[760px] text-left">
      <CardContent className="p-4">
        <div className="flex items-baseline justify-between gap-3">
          <strong className="font-mono">
            {INTEGRATION_LABELS[record.integration] ?? record.integration} · {record.tool}
          </strong>
          <OutcomeBadge outcome={record.outcome} />
        </div>
        <p className="my-1 text-xs text-muted-foreground">
          {record.workflow} · run {record.workflow_run_id.slice(0, 8)}
          {record.approval_id ? ` · approval ${record.approval_id.slice(0, 8)}` : ""}
          {record.actor ? ` · by ${record.actor}` : ""}
        </p>
        <pre className="my-1 overflow-x-auto rounded-md bg-muted p-2 text-xs">
          {JSON.stringify(record.args, null, 2)}
        </pre>
        <p className="m-0 text-xs text-muted-foreground">
          {record.external_id ? `external id ${record.external_id} · ` : ""}
          {new Date(record.created_at).toLocaleString()}
        </p>
        {record.error && <p className="mt-1 text-xs text-destructive">{record.error}</p>}
      </CardContent>
    </Card>
  );
}

export default function AuditTrail() {
  const [runFilter, setRunFilter] = useState("");
  const { data: records = [], error, isPending, refetch } = useMutations(runFilter);

  return (
    <>
      <div className="mb-5 flex justify-center gap-2">
        <Input
          value={runFilter}
          onChange={(event) => setRunFilter(event.target.value)}
          placeholder="Filter by workflow run id"
          className="min-w-[320px] max-w-md"
        />
        <Button variant="outline" onClick={() => refetch()}>
          Refresh
        </Button>
      </div>
      {error && <p className="text-center text-destructive">{String(error)}</p>}
      {isPending ? (
        <p className="text-center text-muted-foreground">Loading audit trail…</p>
      ) : records.length === 0 ? (
        <p className="text-center text-muted-foreground">
          No recorded actions yet. Approve a workflow to see its CRM writes here.
        </p>
      ) : (
        records.map((record) => <MutationRow key={record.id} record={record} />)
      )}
    </>
  );
}
