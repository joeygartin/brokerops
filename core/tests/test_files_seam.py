"""File writes flow through the same two seams as every external write (BOP-021):

RecordingFiles lands `put` in the action ledger (success and failure, args carry
the content digest — never the bytes); IdempotentFiles dedupes a replayed `put`
within a run so the store is written at most once and no second mutation record
appears. Reads pass through both untouched.
"""

from brokerops_core.models.files import FileRef
from brokerops_core.models.idempotency import ClaimStatus, IdempotencyClaim
from brokerops_core.models.mutation import MutationOutcome, MutationRecord
from brokerops_core.services.audit import AuditContext, RecordingFiles, audit_scope
from brokerops_core.services.idempotency import IdempotentFiles


class CollectingAuditLog:
    def __init__(self) -> None:
        self.records: list[MutationRecord] = []

    async def record(self, record: MutationRecord) -> None:
        self.records.append(record)

    async def list(
        self, workflow_run_id: str | None = None, limit: int = 200
    ) -> list[MutationRecord]:
        return list(self.records)


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self._rows: dict[str, tuple[ClaimStatus, str | None]] = {}

    async def begin(self, key: str, *, workflow_run_id: str, tool: str) -> IdempotencyClaim:
        existing = self._rows.get(key)
        if existing is None:
            self._rows[key] = (ClaimStatus.PENDING, None)
            return IdempotencyClaim(status=ClaimStatus.NEW)
        status, result = existing
        return IdempotencyClaim(status=status, result=result)

    async def complete(self, key: str, result: str) -> None:
        self._rows[key] = (ClaimStatus.COMPLETED, result)


class FakeFiles:
    def __init__(self, fail: bool = False) -> None:
        self.puts: list[tuple[str, bytes, str]] = []
        self.fail = fail

    async def list(self, folder: str) -> list[FileRef]:
        return [FileRef(file_id="f1", name="a.pdf")]

    async def get(self, file_id: str) -> FileRef | None:
        return FileRef(file_id=file_id, name="a.pdf")

    async def put(self, name: str, content: bytes, folder: str) -> FileRef:
        if self.fail:
            raise RuntimeError("drive down")
        self.puts.append((name, content, folder))
        return FileRef(file_id=f"f-{len(self.puts)}", name=name, size_bytes=len(content))


def _ctx() -> AuditContext:
    return AuditContext(workflow_run_id="run-1", workflow="documents", actor="op@x.com")


async def test_put_is_recorded_with_digest_not_bytes() -> None:
    audit = CollectingAuditLog()
    inner = FakeFiles()
    files = RecordingFiles(inner, audit, integration="google_drive")
    with audit_scope(_ctx()):
        ref = await files.put("a.pdf", b"synthetic body", "RM1")
    assert ref.file_id == "f-1"
    (record,) = audit.records
    assert record.integration == "google_drive"
    assert record.tool == "put_file"
    assert record.outcome is MutationOutcome.SUCCESS
    assert record.external_id == "f-1"
    assert record.args["name"] == "a.pdf" and record.args["folder"] == "RM1"
    assert record.args["size_bytes"] == len(b"synthetic body")
    # the ledger carries a digest, never the content itself
    assert "content" not in record.args and len(str(record.args["content_sha256"])) == 64


async def test_failed_put_is_recorded_and_reraised() -> None:
    audit = CollectingAuditLog()
    files = RecordingFiles(FakeFiles(fail=True), audit, integration="google_drive")
    with audit_scope(_ctx()):
        try:
            await files.put("a.pdf", b"x", "RM1")
        except RuntimeError:
            pass
    (record,) = audit.records
    assert record.outcome is MutationOutcome.FAILURE
    assert record.error == "drive down"


async def test_replayed_put_dedupes_and_writes_no_second_record() -> None:
    # Idempotency wraps recording (the main.py layering): a replay short-circuits
    # before the recording wrapper, so one side effect and one ledger row.
    audit = CollectingAuditLog()
    inner = FakeFiles()
    files = IdempotentFiles(
        RecordingFiles(inner, audit, integration="google_drive"), InMemoryIdempotencyStore()
    )
    with audit_scope(_ctx()):
        first = await files.put("a.pdf", b"same bytes", "RM1")
        replay = await files.put("a.pdf", b"same bytes", "RM1")
    assert replay == first  # original result returned
    assert len(inner.puts) == 1  # side effect performed at most once
    assert len(audit.records) == 1  # no second mutation record
    # a genuinely different write is a new key
    with audit_scope(_ctx()):
        await files.put("b.pdf", b"other bytes", "RM1")
    assert len(inner.puts) == 2


async def test_put_without_run_context_runs_undeduped() -> None:
    inner = FakeFiles()
    files = IdempotentFiles(
        RecordingFiles(inner, CollectingAuditLog(), integration="google_drive"),
        InMemoryIdempotencyStore(),
    )
    await files.put("a.pdf", b"x", "RM1")
    await files.put("a.pdf", b"x", "RM1")
    assert len(inner.puts) == 2  # no run to scope a key to (matches CRM/voice behavior)


async def test_reads_pass_through_both_seams() -> None:
    files = IdempotentFiles(
        RecordingFiles(FakeFiles(), CollectingAuditLog(), integration="google_drive"),
        InMemoryIdempotencyStore(),
    )
    assert [ref.name for ref in await files.list("RM1")] == ["a.pdf"]
    fetched = await files.get("f9")
    assert fetched is not None and fetched.file_id == "f9"
