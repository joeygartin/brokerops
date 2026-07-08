import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { useAuth } from "./authContext";
import {
  DocumentKind,
  type Document,
  type MilestoneView,
  type Transaction,
} from "./client";
import { useAttachDocument, useFolderFiles } from "./hooks/transactions";

// The Documents tab on a transaction card (BOP-021): list what's attached,
// open it in the file store's own viewer, and (operators and up) attach a file
// from the transaction's office folder. Pointers only — no bytes, no preview,
// no doc generation. The API is the security boundary; hiding the attach
// controls from viewers just mirrors the server's role gate.

// The kind picker enumerates the backend enum via its generated runtime object
// — a new DocumentKind lands here through `npm run generate`, never by hand.
const DOCUMENT_KINDS = Object.values(DocumentKind);

function prettyKind(kind: string): string {
  return kind.replace(/_/g, " ");
}

export default function TransactionDocuments({
  transaction,
  documents,
  milestones,
}: {
  transaction: Transaction;
  documents: Document[];
  milestones: MilestoneView[];
}) {
  const { hasRole } = useAuth();
  const [selectedFile, setSelectedFile] = useState("");
  const [kind, setKind] = useState<DocumentKind>("other");
  const [milestoneId, setMilestoneId] = useState("");

  const canAttach = hasRole("operator");
  const folderQuery = useFolderFiles(transaction.listing_key, { enabled: canAttach });
  const attachMutation = useAttachDocument();
  const attaching = attachMutation.isPending;
  // The folder load and the attach POST can each fail; surface whichever did.
  const error = folderQuery.error ?? attachMutation.error;

  const attach = async () => {
    if (!selectedFile) return;
    try {
      await attachMutation.mutateAsync({
        transactionId: transaction.id,
        body: { file_id: selectedFile, kind, milestone_id: milestoneId || null },
      });
      // The mutation invalidated the transactions query — the board (and this
      // card) re-render with the new document; just reset the form.
      setSelectedFile("");
      setMilestoneId("");
    } catch {
      // Rendered from attachMutation.error below.
    }
  };

  // `enabled` keeps the query idle for viewers, so `data` is undefined until the
  // operator's folder load resolves — mirror the old "Loading folder…" gate.
  const folderFiles = folderQuery.data ?? null;
  const attachedIds = new Set(documents.map((d) => d.file.file_id));
  const attachable = (folderFiles ?? []).filter((f) => !attachedIds.has(f.file_id));

  return (
    <div>
      {documents.length === 0 ? (
        <p className="text-sm text-muted-foreground">No documents attached yet.</p>
      ) : (
        <ul className="m-0 list-none p-0">
          {documents.map((document) => (
            <li
              key={document.id}
              className="flex items-baseline gap-3 border-b border-muted py-1.5"
            >
              <Badge variant="info">
                {prettyKind(document.kind ?? "other").toUpperCase()}
              </Badge>
              <span className="flex-1">{document.title}</span>
              <span className="whitespace-nowrap text-xs text-muted-foreground">
                {document.uploaded_by || "unattributed"}
              </span>
              {document.file.web_url && (
                <a
                  href={document.file.web_url}
                  target="_blank"
                  rel="noreferrer"
                  className="whitespace-nowrap text-sm text-primary no-underline hover:underline"
                >
                  Open
                </a>
              )}
            </li>
          ))}
        </ul>
      )}

      {canAttach && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Select
            aria-label="File to attach"
            value={selectedFile}
            onChange={(e) => setSelectedFile(e.target.value)}
          >
            <option value="">
              {folderFiles === null
                ? "Loading folder…"
                : attachable.length === 0
                  ? `No unattached files in ${transaction.listing_key}`
                  : "Choose a file…"}
            </option>
            {attachable.map((file) => (
              <option key={file.file_id} value={file.file_id}>
                {file.name}
              </option>
            ))}
          </Select>
          <Select
            aria-label="Document kind"
            value={kind}
            onChange={(e) => setKind(e.target.value as DocumentKind)}
          >
            {DOCUMENT_KINDS.map((k) => (
              <option key={k} value={k}>
                {prettyKind(k)}
              </option>
            ))}
          </Select>
          <Select
            aria-label="Attach to milestone"
            value={milestoneId}
            onChange={(e) => setMilestoneId(e.target.value)}
          >
            <option value="">Whole transaction</option>
            {milestones.map((m) => (
              <option key={m.id} value={m.id}>
                {m.title}
              </option>
            ))}
          </Select>
          <Button variant="success" size="sm" onClick={attach} disabled={!selectedFile || attaching}>
            {attaching ? "Attaching…" : "Attach"}
          </Button>
        </div>
      )}
      {error && <p className="text-sm text-destructive">{String(error)}</p>}
    </div>
  );
}
