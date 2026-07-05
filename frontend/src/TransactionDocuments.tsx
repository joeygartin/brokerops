import { useCallback, useEffect, useState } from "react";
import { unwrap } from "./api";
import { useAuth } from "./authContext";
import {
  attachDocumentTransactionsTransactionIdDocumentsPost,
  DocumentKind,
  listFilesFilesGet,
  type Document,
  type FileRef,
  type MilestoneView,
  type Transaction,
} from "./client";

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
  onChanged,
}: {
  transaction: Transaction;
  documents: Document[];
  milestones: MilestoneView[];
  onChanged: () => void;
}) {
  const { hasRole } = useAuth();
  const [folderFiles, setFolderFiles] = useState<FileRef[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState("");
  const [kind, setKind] = useState<DocumentKind>("other");
  const [milestoneId, setMilestoneId] = useState("");
  const [attaching, setAttaching] = useState(false);

  const canAttach = hasRole("operator");

  const loadFolder = useCallback(() => {
    listFilesFilesGet({ query: { folder: transaction.listing_key } })
      .then((result) => setFolderFiles(unwrap(result)))
      .catch((cause) => setError(String(cause)));
  }, [transaction.listing_key]);

  useEffect(() => {
    if (canAttach) loadFolder();
  }, [canAttach, loadFolder]);

  const attach = async () => {
    if (!selectedFile) return;
    setAttaching(true);
    setError(null);
    try {
      unwrap(
        await attachDocumentTransactionsTransactionIdDocumentsPost({
          path: { transaction_id: transaction.id },
          body: {
            file_id: selectedFile,
            kind,
            milestone_id: milestoneId || null,
          },
        }),
      );
      setSelectedFile("");
      setMilestoneId("");
      onChanged();
    } catch (cause) {
      setError(String(cause));
    } finally {
      setAttaching(false);
    }
  };

  const attachedIds = new Set(documents.map((d) => d.file.file_id));
  const attachable = (folderFiles ?? []).filter((f) => !attachedIds.has(f.file_id));

  return (
    <div>
      {documents.length === 0 ? (
        <p style={{ color: "#57606a", fontSize: "0.85rem" }}>No documents attached yet.</p>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {documents.map((document) => (
            <li
              key={document.id}
              style={{
                display: "flex",
                gap: "0.75rem",
                alignItems: "baseline",
                padding: "0.45rem 0",
                borderBottom: "1px solid #eaeef2",
              }}
            >
              <span
                style={{
                  background: "#ddf4ff",
                  color: "#0969da",
                  borderRadius: 999,
                  padding: "0.1rem 0.55rem",
                  fontSize: "0.7rem",
                  whiteSpace: "nowrap",
                }}
              >
                {prettyKind(document.kind ?? "other").toUpperCase()}
              </span>
              <span style={{ flex: 1 }}>{document.title}</span>
              <span style={{ color: "#57606a", fontSize: "0.8rem", whiteSpace: "nowrap" }}>
                {document.uploaded_by || "unattributed"}
              </span>
              {document.file.web_url && (
                <a
                  href={document.file.web_url}
                  target="_blank"
                  rel="noreferrer"
                  style={{ fontSize: "0.85rem", color: "#0969da", whiteSpace: "nowrap" }}
                >
                  Open
                </a>
              )}
            </li>
          ))}
        </ul>
      )}

      {canAttach && (
        <div
          style={{
            display: "flex",
            gap: "0.5rem",
            alignItems: "center",
            flexWrap: "wrap",
            marginTop: "0.8rem",
          }}
        >
          <select
            aria-label="File to attach"
            value={selectedFile}
            onChange={(e) => setSelectedFile(e.target.value)}
            style={{ padding: "0.35rem", borderRadius: 6, border: "1px solid #d0d7de" }}
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
          </select>
          <select
            aria-label="Document kind"
            value={kind}
            onChange={(e) => setKind(e.target.value as DocumentKind)}
            style={{ padding: "0.35rem", borderRadius: 6, border: "1px solid #d0d7de" }}
          >
            {DOCUMENT_KINDS.map((k) => (
              <option key={k} value={k}>
                {prettyKind(k)}
              </option>
            ))}
          </select>
          <select
            aria-label="Attach to milestone"
            value={milestoneId}
            onChange={(e) => setMilestoneId(e.target.value)}
            style={{ padding: "0.35rem", borderRadius: 6, border: "1px solid #d0d7de" }}
          >
            <option value="">Whole transaction</option>
            {milestones.map((m) => (
              <option key={m.id} value={m.id}>
                {m.title}
              </option>
            ))}
          </select>
          <button
            onClick={attach}
            disabled={!selectedFile || attaching}
            style={{
              padding: "0.35rem 1rem",
              borderRadius: 6,
              border: "1px solid #1a7f37",
              background: "#2da44e",
              color: "#fff",
              cursor: !selectedFile || attaching ? "not-allowed" : "pointer",
            }}
          >
            {attaching ? "Attaching…" : "Attach"}
          </button>
        </div>
      )}
      {error && <p style={{ color: "#cf222e", fontSize: "0.85rem" }}>{error}</p>}
    </div>
  );
}
