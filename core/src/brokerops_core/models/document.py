"""Transaction document metadata (BOP-021).

A `Document` row ties a `FileRef` (a pointer into the office file store — the
bytes stay there) to a transaction, and optionally to a specific milestone.
`DocumentKind` is the closed vocabulary the milestone expected-document check
matches on (`Milestone.expected_document`).
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from brokerops_core.models.files import FileRef


class DocumentKind(StrEnum):
    PURCHASE_AGREEMENT = "purchase_agreement"
    DISCLOSURES = "disclosures"
    INSPECTION_REPORT = "inspection_report"
    APPRAISAL_REPORT = "appraisal_report"
    LOAN_APPROVAL = "loan_approval"
    CLOSING_STATEMENT = "closing_statement"
    OTHER = "other"


class Document(BaseModel):
    # Stamped by the scoped data layer (BOP-006 pattern); "" means "use the
    # ambient bound tenant".
    tenant_id: str = ""
    id: str
    transaction_id: str
    # None = attached at the transaction level; set = attached to one milestone.
    milestone_id: str | None = None
    kind: DocumentKind = DocumentKind.OTHER
    title: str
    file: FileRef
    uploaded_by: str = ""
    created_at: datetime
