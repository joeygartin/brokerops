import { Link, useNavigate } from "@tanstack/react-router";
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { useForm } from "react-hook-form";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { clearDraft, getDraft, setDraft } from "./approvalDrafts";
import {
  AGE_OPTIONS,
  countByKind,
  distinctKinds,
  distinctWorkflows,
  filterApprovals,
  kindLabel,
  NO_FILTERS,
  sortByDecidedAt,
  sortForTriage,
  type TriageFilters,
} from "./approvalTriage";
import { useAuth } from "./authContext";
import { type ApprovalRequest } from "./client";
import { useApprovals, useDecideApproval, useDecidedApprovals } from "./hooks/approvals";
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
  return (approval.payload ?? {}) as ApprovalPayload;
}

// The card heading for a gate — shared by the inbox card, the decided-history
// row, and (via ApprovalCard) the permalink view, so a kind reads the same
// everywhere.
export function approvalSubject(approval: ApprovalRequest): string {
  const payload = payloadOf(approval);
  switch (approval.kind) {
    case "approve_escalation":
      return `Escalate overdue milestones — ${payload.transaction_id} (${payload.listing_key})`;
    case "notify_agent":
      return `Hot lead — notify listing agent (${payload.listing_key})`;
    case "approve_outbound_message":
      return `Approve outbound ${payload.channel ?? "message"} — ${payload.recipient}`;
    default:
      return `Approve marketing — ${payload.listing_key}`;
  }
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

// The one editable payload (BOP-028): the outbound draft body, on react-hook-form.
// `register` binds the native textarea; a non-blank validation runs before submit
// and a dirty-state indicator warns that edits are unsaved until a decision.
function OutboundMessageForm({
  approval,
  form,
  canEdit,
}: {
  approval: ApprovalRequest;
  form: ReturnType<typeof useForm<{ body: string }>>;
  canEdit: boolean;
}) {
  const payload = payloadOf(approval);
  const originalBody = payload.body ?? "";
  const bodyValue = form.watch("body");
  const blank = bodyValue.trim() === "";
  // Dirty is measured against the ORIGINAL payload body, not the form's default —
  // the default is seeded from the persisted edit, so a remounted card would read
  // as "clean" even though its text still differs from what the server holds.
  const dirty = bodyValue !== originalBody;
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
        rows={7}
        readOnly={!canEdit}
        aria-invalid={canEdit && blank}
        {...form.register("body", {
          validate: (value) => value.trim() !== "" || "The draft body is empty.",
          // Persist every keystroke so the edit survives an unmount (see approvalDrafts).
          onChange: (event) => setDraft(approval.id, event.target.value, originalBody),
        })}
      />
      {canEdit && blank ? (
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
      {canEdit && dirty && !blank && (
        <p className="mt-1 text-xs text-warning">
          Unsaved edits — they apply only when you Approve.
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

// The imperative surface the inbox's keyboard flow drives: approve/reject the
// focused card (a/r) reads the card's own live form state, so a keyboard approve
// carries any draft edits exactly as the button would. approve and reject are
// gated separately — a blanked draft blocks approve but Reject stays available
// (discarding a draft is exactly what Reject is for).
export type ApprovalCardHandle = {
  decide: (decision: "approved" | "rejected") => void;
  canApprove: boolean;
  canReject: boolean;
};

export const ApprovalCard = forwardRef<
  ApprovalCardHandle,
  { approval: ApprovalRequest; onDecided: (message: string) => void }
>(function ApprovalCard({ approval, onDecided }, ref) {
  const { hasRole } = useAuth();
  const decideMutation = useDecideApproval();
  const busy = decideMutation.isPending;
  const payload = payloadOf(approval);
  const isEscalation = approval.kind === "approve_escalation";
  const isHotLead = approval.kind === "notify_agent";
  const isOutboundMessage = approval.kind === "approve_outbound_message";
  const isAdmin = hasRole("admin");
  const originalBody = payload.body ?? "";

  // react-hook-form owns the editable outbound body. Bound to a small typed shape
  // (the generated payload is an open dict); the default is seeded from the
  // persisted draft (draftStore) so an edit survives a card unmount. mode:onChange
  // keeps the blank guard and dirty indicator live as the operator types.
  const form = useForm<{ body: string }>({
    defaultValues: { body: getDraft(approval.id) ?? originalBody },
    mode: "onChange",
  });
  const bodyValue = form.watch("body");
  // An emptied draft can't be approved — the card promises the visible text is
  // exactly what sends, and blank means "nothing would send". Reject instead.
  const blankDraftBody = isOutboundMessage && bodyValue.trim() === "";
  // The hard-unload guard is NOT here — a card-scoped effect is torn down the
  // moment the card unmounts (filter/tab/optimistic removal), which would leave a
  // still-dirty draftStore entry unguarded. It lives at module scope instead
  // (registerDraftUnloadGuard), reading draftStore independent of any card.

  const decide = useCallback(
    async (decision: "approved" | "rejected") => {
      const edited = form.getValues("body");
      // A blank draft can never be approved — the guard the button enforces, made
      // explicit here so the keyboard path (a) honours it too.
      if (isOutboundMessage && decision === "approved" && edited.trim() === "") return;
      const editedPayload =
        isOutboundMessage && decision === "approved" && edited !== (payload.body ?? "")
          ? { body: edited }
          : undefined;
      try {
        const outcome = await decideMutation.mutateAsync({
          approvalId: approval.id,
          body: editedPayload ? { decision, edited_payload: editedPayload } : { decision },
        });
        // The workflow output is an open dict on the wire (engine-specific), so
        // the task-count view of it stays a local cast.
        const output = outcome.workflow.output as
          | { fub_task_ids?: string[]; escalated_task_ids?: string[]; hot_task_id?: string }
          | null
          | undefined;
        const taskCount =
          output?.fub_task_ids?.length ??
          output?.escalated_task_ids?.length ??
          (output?.hot_task_id ? 1 : undefined);
        const target = isOutboundMessage
          ? (payload.recipient ?? payload.listing_key)
          : (payload.transaction_id ?? payload.listing_key);
        // The decision landed — the draft is consumed, so drop its persisted edit.
        clearDraft(approval.id);
        onDecided(
          `${target} ${decision} — workflow status: ${outcome.workflow.status}` +
            (taskCount ? `, ${taskCount} CRM task(s) created.` : "."),
        );
      } catch (cause) {
        // Left in draftStore on failure, so the rolled-back card keeps the edit.
        onDecided(`Failed to decide ${approval.id}: ${String(cause)}`);
      }
    },
    [approval.id, decideMutation, form, isOutboundMessage, onDecided, payload],
  );

  useImperativeHandle(
    ref,
    () => ({
      decide,
      canApprove: isAdmin && !busy && !blankDraftBody,
      canReject: isAdmin && !busy,
    }),
    [decide, isAdmin, busy, blankDraftBody],
  );

  return (
    <Card as="article" className="text-left">
      <CardHeader>
        <strong>{approvalSubject(approval)}</strong>
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
          <OutboundMessageForm approval={approval} form={form} canEdit={isAdmin && !busy} />
        ) : (
          <MarketingPreview approval={approval} />
        )}
      </CardContent>
      {isAdmin ? (
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
});

// One labelled native <select> for a triage filter (kind / workflow / age).
function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
      {label}
      <Select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </Select>
    </label>
  );
}

// The pending triage surface: filter bar with per-kind count badges, oldest-first
// order (most-overdue escalations first), and a keyboard flow — j/k to move, enter
// to open, a/r to approve/reject the focused card with a confirm.
function PendingTriage({
  approvals,
  filters,
  onFilters,
  onDecided,
}: {
  approvals: ApprovalRequest[];
  filters: TriageFilters;
  onFilters: (filters: TriageFilters) => void;
  onDecided: (message: string) => void;
}) {
  const { hasRole } = useAuth();
  const navigate = useNavigate();
  const [focusedIndex, setFocusedIndex] = useState(0);

  const counts = useMemo(() => countByKind(approvals), [approvals]);
  const kinds = useMemo(() => distinctKinds(approvals), [approvals]);
  const workflows = useMemo(() => distinctWorkflows(approvals), [approvals]);
  const visible = useMemo(
    () => sortForTriage(filterApprovals(approvals, filters, new Date())),
    [approvals, filters],
  );

  // Card decide handles (keyboard a/r) and wrapper elements (focus target), keyed
  // by approval id so they survive the list reordering/shrinking as decisions land.
  const handleRefs = useRef(new Map<string, ApprovalCardHandle>());
  const wrapperRefs = useRef(new Map<string, HTMLDivElement>());
  // Set when a KEYBOARD decision optimistically removes the focused card, so the
  // list-shrink effect knows to carry DOM focus to the card that slid into its
  // place — otherwise focus falls off the removed element and j/k/a/r stop
  // reaching the list (a mouse decision leaves this false so we never steal focus).
  const refocusAfterRemoval = useRef(false);

  // Keep the focused index in range as the visible list shrinks (a decided card
  // leaves the list optimistically) or a filter narrows it, and — after a keyboard
  // decision — move focus to the card now at that index so triage keeps flowing.
  useEffect(() => {
    const clamped = Math.min(focusedIndex, Math.max(0, visible.length - 1));
    if (clamped !== focusedIndex) setFocusedIndex(clamped);
    if (refocusAfterRemoval.current) {
      refocusAfterRemoval.current = false;
      if (visible.length > 0) wrapperRefs.current.get(visible[clamped].id)?.focus();
    }
  }, [visible, focusedIndex]);

  const focusCard = useCallback((index: number, approval: ApprovalRequest | undefined) => {
    setFocusedIndex(index);
    if (approval) wrapperRefs.current.get(approval.id)?.focus();
  }, []);

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (visible.length === 0) return;
    // Only the card wrapper owns the triage shortcuts. When focus is on an
    // interactive descendant — the draft textarea/filters, or an Approve/Reject
    // button or Open link — the native control keeps every key (so Enter activates
    // a focused button, not the permalink), and typing in the draft is never
    // hijacked.
    const interactive = (event.target as HTMLElement).closest(
      "button, a, input, textarea, select",
    );
    if (interactive) return;
    const key = event.key.toLowerCase();

    if (key === "j" || key === "arrowdown") {
      event.preventDefault();
      focusCard(Math.min(focusedIndex + 1, visible.length - 1), visible[focusedIndex + 1]);
    } else if (key === "k" || key === "arrowup") {
      event.preventDefault();
      focusCard(Math.max(focusedIndex - 1, 0), visible[focusedIndex - 1]);
    } else if (key === "enter") {
      // Opens the permalink; a dirty draft survives via draftStore (the permalink
      // renders the same card and reads the same edit), so no discard prompt.
      const target = visible[focusedIndex];
      if (target) navigate({ to: "/approvals/$id", params: { id: target.id } });
    } else if (key === "a" || key === "r") {
      const target = visible[focusedIndex];
      const handle = target && handleRefs.current.get(target.id);
      if (!target || !handle || !hasRole("admin")) return;
      // Approve is blocked on a blanked draft; Reject is not — discarding a draft
      // is what Reject is for.
      const allowed = key === "a" ? handle.canApprove : handle.canReject;
      if (!allowed) return;
      event.preventDefault();
      const decision = key === "a" ? "approved" : "rejected";
      if (window.confirm(`${decision === "approved" ? "Approve" : "Reject"} “${approvalSubject(target)}”?`)) {
        // Carry focus to the next card once this one is optimistically removed.
        refocusAfterRemoval.current = true;
        handle.decide(decision);
      }
    }
  };

  return (
    <div>
      <div className="mx-auto mb-4 max-w-2xl">
        <div className="mb-2 flex flex-wrap items-end gap-3">
          <FilterSelect
            label="Kind"
            value={filters.kind}
            onChange={(kind) => onFilters({ ...filters, kind })}
            options={[
              { value: "all", label: `All kinds (${approvals.length})` },
              ...kinds.map((kind) => ({
                value: kind,
                label: `${kindLabel(kind)} (${counts[kind]})`,
              })),
            ]}
          />
          <FilterSelect
            label="Workflow"
            value={filters.workflow}
            onChange={(workflow) => onFilters({ ...filters, workflow })}
            options={[
              { value: "all", label: "All workflows" },
              ...workflows.map((workflow) => ({ value: workflow, label: workflow })),
            ]}
          />
          <FilterSelect
            label="Age"
            value={filters.age}
            onChange={(age) => onFilters({ ...filters, age: age as TriageFilters["age"] })}
            options={AGE_OPTIONS}
          />
        </div>
        <div className="flex flex-wrap gap-1.5" aria-label="Pending counts by kind">
          {kinds.map((kind) => (
            <Badge key={kind} variant="secondary">
              {kindLabel(kind)} {counts[kind]}
            </Badge>
          ))}
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          Keyboard: <kbd>j</kbd>/<kbd>k</kbd> move · <kbd>enter</kbd> open ·{" "}
          <kbd>a</kbd>/<kbd>r</kbd> approve/reject the focused card
        </p>
      </div>

      {visible.length === 0 ? (
        <p className="text-center text-muted-foreground">
          {approvals.length === 0
            ? "No pending approvals. Start a marketing workflow from the Listings tab."
            : "No approvals match the current filters."}
        </p>
      ) : (
        <div role="list" onKeyDown={onKeyDown}>
          {visible.map((approval, index) => (
            <div
              key={approval.id}
              role="listitem"
              tabIndex={0}
              ref={(node) => {
                if (node) wrapperRefs.current.set(approval.id, node);
                else wrapperRefs.current.delete(approval.id);
              }}
              onFocus={() => setFocusedIndex(index)}
              aria-current={index === focusedIndex}
              className={`mx-auto mb-4 max-w-2xl rounded-lg outline-none ring-offset-2 ring-offset-background focus-visible:ring-2 focus-visible:ring-ring ${
                index === focusedIndex ? "ring-2 ring-ring" : ""
              }`}
            >
              <div className="mb-1 text-right">
                <Link
                  to="/approvals/$id"
                  params={{ id: approval.id }}
                  className={PERMALINK_CLASS}
                >
                  Open ↗
                </Link>
              </div>
              <ApprovalCard
                approval={approval}
                onDecided={onDecided}
                ref={(handle) => {
                  if (handle) handleRefs.current.set(approval.id, handle);
                  else handleRefs.current.delete(approval.id);
                }}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// The decided-history log (BOP-028): approved/rejected gates, most-recent first,
// separate from the pending triage list. Read-only — each row links out to its
// permalink; the data (who decided, when) already rides on ApprovalRequest.
function DecidedHistory({
  approvals,
  isPending,
  error,
}: {
  approvals: ApprovalRequest[];
  isPending: boolean;
  error: unknown;
}) {
  const rows = useMemo(() => sortByDecidedAt(approvals), [approvals]);
  if (isPending) return <p className="text-center text-muted-foreground">Loading history…</p>;
  if (error) return <p className="text-center text-destructive">{String(error)}</p>;
  if (rows.length === 0) {
    return <p className="text-center text-muted-foreground">No decided approvals yet.</p>;
  }
  return (
    <div role="list">
      {rows.map((approval) => (
        <Card
          key={approval.id}
          as="article"
          role="listitem"
          className="mx-auto mb-3 flex max-w-2xl items-center justify-between gap-3 p-3 text-left"
        >
          <div className="min-w-0">
            <p className="truncate font-medium">{approvalSubject(approval)}</p>
            <p className="text-xs text-muted-foreground">
              {approval.decided_by ?? "unknown"}
              {approval.decided_at ? ` · ${new Date(approval.decided_at).toLocaleString()}` : ""}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Badge variant={approval.status === "approved" ? "success" : "destructive"}>
              {approval.status}
            </Badge>
            <Link to="/approvals/$id" params={{ id: approval.id }} className={PERMALINK_CLASS}>
              Open ↗
            </Link>
          </div>
        </Card>
      ))}
    </div>
  );
}

export default function ApprovalsInbox() {
  const { data: approvals = [], error, isPending } = useApprovals();
  const [view, setView] = useState<"pending" | "decided">("pending");
  const [filters, setFilters] = useState<TriageFilters>(NO_FILTERS);
  const [notice, setNotice] = useState<string | null>(null);
  const decided = useDecidedApprovals(view === "decided");

  // The decide mutation invalidates the approvals query, so a decision refreshes
  // the inbox without a manual refetch here — this just surfaces the outcome.
  const handleDecided = (message: string) => setNotice(message);

  const tabClass = (active: boolean) =>
    `rounded-md border px-3 py-1.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background ${
      active
        ? "border-strong bg-strong text-strong-foreground"
        : "border-border bg-card text-foreground hover:bg-muted"
    }`;

  return (
    <>
      <div className="mx-auto mb-4 flex max-w-2xl justify-center gap-2">
        <button
          type="button"
          className={tabClass(view === "pending")}
          aria-pressed={view === "pending"}
          onClick={() => setView("pending")}
        >
          Pending ({approvals.length})
        </button>
        <button
          type="button"
          className={tabClass(view === "decided")}
          aria-pressed={view === "decided"}
          onClick={() => setView("decided")}
        >
          Decided
        </button>
      </div>

      {error && <p className="text-center text-destructive">{String(error)}</p>}
      {notice && (
        <p className="mx-auto mb-4 max-w-2xl rounded-md bg-success-soft p-2 text-center text-success-soft-foreground">
          {notice}
        </p>
      )}

      {view === "decided" ? (
        <DecidedHistory approvals={decided.data} isPending={decided.isPending} error={decided.error} />
      ) : isPending ? (
        <p className="text-center text-muted-foreground">Loading approvals…</p>
      ) : (
        <PendingTriage
          approvals={approvals}
          filters={filters}
          onFilters={setFilters}
          onDecided={handleDecided}
        />
      )}
    </>
  );
}
