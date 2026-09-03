"""Strict agent boundary for untrusted Document Intelligence results."""

import json
from enum import StrEnum
from typing import Annotated, Final, Literal, assert_never

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

OpaqueUploadID = Annotated[str, Field(pattern=r"^upl_[A-Za-z0-9_]{1,64}$")]
OpaqueJobID = Annotated[str, Field(pattern=r"^job_[A-Za-z0-9_]{1,64}$")]
DocumentVersion = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
ObservationID = Annotated[str, Field(pattern=r"^obs_[A-Za-z0-9_]{1,64}$")]
OpaqueDocumentID = Annotated[str, Field(pattern=r"^doc_[A-Za-z0-9_]{1,64}$")]
TableID = Annotated[str, Field(pattern=r"^tbl_[A-Za-z0-9_]{1,64}$")]
StableCode = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
VersionName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
Coordinate = Annotated[float, Field(ge=0, le=1)]
Point = tuple[Coordinate, Coordinate]
DocumentType = Literal[
    "auto",
    "general",
    "invoice",
    "receipt",
    "purchase_order",
    "identity_document",
    "contract",
    "bank_statement",
    "medical_form",
    "application_form",
    "resume",
]
OutputFormat = Literal["structured", "text", "markdown"]
JobStatus = Literal[
    "accepted",
    "inspecting",
    "processing",
    "validating",
    "cancelling",
    "cancelled",
    "rejected",
    "partial",
    "review_required",
    "completed",
]


class ContractModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ExtractionSchema(ContractModel):
    schema_id: Annotated[str, Field(min_length=1, max_length=128)]
    schema_version: Annotated[str, Field(min_length=1, max_length=64)]


class DocumentRequest(ContractModel):
    upload_id: OpaqueUploadID | None = None
    job_id: OpaqueJobID | None = None
    document_type: DocumentType
    output_format: OutputFormat
    extraction_schema: ExtractionSchema | None = Field(
        default=None, alias="schema", serialization_alias="schema"
    )
    language_hints: Annotated[
        list[Annotated[str, Field(min_length=2, max_length=35)]], Field(max_length=8)
    ] = Field(default_factory=list)
    include_evidence: bool

    @model_validator(mode="after")
    def exactly_one_service_reference(self) -> DocumentRequest:
        if (self.upload_id is None) == (self.job_id is None):
            raise ValueError("provide exactly one of upload_id or job_id")
        return self


class Citation(ContractModel):
    document_version: DocumentVersion
    page: Annotated[int, Field(ge=1)]
    polygon: Annotated[list[Point], Field(min_length=3, max_length=16)]
    observation_id: ObservationID

    @model_validator(mode="after")
    def polygon_has_area(self) -> Citation:
        area = sum(
            left[0] * right[1] - right[0] * left[1]
            for left, right in zip(self.polygon, self.polygon[1:] + self.polygon[:1], strict=True)
        )
        if abs(area) <= 1e-12:
            raise ValueError("citation polygon must have non-zero area")
        return self


class TextObservation(ContractModel):
    observation_id: ObservationID
    level: Literal["page", "paragraph", "line", "word"]
    text: Annotated[str, Field(min_length=1, max_length=65_536)]
    confidence: Annotated[float, Field(ge=0, le=1)]
    polygon: Annotated[list[Point], Field(min_length=3, max_length=16)]
    reading_order: Annotated[int, Field(ge=0)]
    parent_observation_id: ObservationID | None = None

    @field_validator("text")
    @classmethod
    def text_is_not_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("observation text must not be whitespace")
        return value

    @model_validator(mode="after")
    def polygon_has_area(self) -> TextObservation:
        area = sum(
            left[0] * right[1] - right[0] * left[1]
            for left, right in zip(self.polygon, self.polygon[1:] + self.polygon[:1], strict=True)
        )
        if abs(area) <= 1e-12:
            raise ValueError("observation polygon must have non-zero area")
        if self.parent_observation_id == self.observation_id:
            raise ValueError("observation cannot parent itself")
        return self


class DocumentPage(ContractModel):
    page: Annotated[int, Field(ge=1)]
    width: Annotated[int, Field(ge=1)]
    height: Annotated[int, Field(ge=1)]
    observations: Annotated[list[TextObservation], Field(max_length=100_000)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def observation_hierarchy_is_ordered_and_unique(self) -> DocumentPage:
        seen_ids: set[str] = set()
        seen_orders: set[int] = set()
        for observation in self.observations:
            if observation.observation_id in seen_ids:
                raise ValueError("observation identifiers must be unique per page")
            if observation.reading_order in seen_orders:
                raise ValueError("observation reading order must be unique per page")
            if (
                observation.parent_observation_id is not None
                and observation.parent_observation_id not in seen_ids
            ):
                raise ValueError("observation parent must precede its child")
            seen_ids.add(observation.observation_id)
            seen_orders.add(observation.reading_order)
        return self


class ExtractedField(ContractModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    value_json: Annotated[str, Field(min_length=1, max_length=100_000)]
    confidence: Annotated[float, Field(ge=0, le=1)]
    citations: Annotated[list[Citation], Field(min_length=1, max_length=100)]

    @field_validator("value_json")
    @classmethod
    def value_is_strict_json(cls, value: str) -> str:
        def reject_constant(constant: str) -> None:
            raise ValueError(f"non-finite JSON constant {constant} is not allowed")

        json.loads(value, parse_constant=reject_constant)
        return value


class TableCell(ContractModel):
    row: Annotated[int, Field(ge=0)]
    column: Annotated[int, Field(ge=0)]
    text: Annotated[str, Field(max_length=1_000_000)]
    confidence: Annotated[float, Field(ge=0, le=1)]
    citations: Annotated[list[Citation], Field(min_length=1, max_length=100)]


class Table(ContractModel):
    table_id: TableID
    cells: Annotated[list[TableCell], Field(min_length=1, max_length=10_000)]


class Confidence(ContractModel):
    input_quality: Annotated[float, Field(ge=0, le=1)]
    ocr: Annotated[float, Field(ge=0, le=1)]
    classification: Annotated[float, Field(ge=0, le=1)]
    extraction: Annotated[float, Field(ge=0, le=1)]
    validation: Annotated[float, Field(ge=0, le=1)]
    overall: Annotated[float, Field(ge=0, le=1)]


class ValidationFailure(ContractModel):
    code: StableCode
    severity: Literal["warning", "error"]


class Cost(ContractModel):
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    decimal: Annotated[str, Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$")]


class DocumentResult(ContractModel):
    job_id: OpaqueJobID
    status: JobStatus
    content_trust: Literal["untrusted"]
    result_schema_version: Literal["1.0"] | None = None
    document_id: OpaqueDocumentID | None = None
    document_version: DocumentVersion | None = None
    text: Annotated[str, Field(max_length=8_000_000)] = ""
    markdown: Annotated[str, Field(max_length=8_000_000)] = ""
    pages: Annotated[list[DocumentPage], Field(max_length=300)] = Field(default_factory=list)
    fields: Annotated[list[ExtractedField], Field(max_length=5_000)] = Field(default_factory=list)
    tables: Annotated[list[Table], Field(max_length=100)] = Field(default_factory=list)
    confidence: Confidence | None = None
    citations: Annotated[list[Citation], Field(max_length=10_000)] = Field(default_factory=list)
    warnings: Annotated[list[StableCode], Field(max_length=500)] = Field(default_factory=list)
    validation_failures: Annotated[list[ValidationFailure], Field(max_length=500)] = Field(
        default_factory=list
    )
    provider: VersionName | None = None
    model_version: VersionName | None = None
    processing_profile_version: VersionName | None = None
    duration_ms: Annotated[int, Field(ge=0)] | None = None
    cost: Cost | None = None

    @model_validator(mode="after")
    def result_evidence_is_consistent(self) -> DocumentResult:
        terminal_with_result = self.status in {"completed", "partial", "review_required"}
        if terminal_with_result and (
            self.result_schema_version is None
            or self.document_id is None
            or self.document_version is None
        ):
            raise ValueError("result-bearing status requires immutable document identity")
        if (self.text or self.markdown) and not self.citations:
            raise ValueError("document content requires source citations")
        evidence = [*self.citations]
        evidence.extend(citation for field in self.fields for citation in field.citations)
        evidence.extend(
            citation for table in self.tables for cell in table.cells for citation in cell.citations
        )
        if self.document_version is not None and any(
            citation.document_version != self.document_version for citation in evidence
        ):
            raise ValueError("citation document version does not match result")
        page_numbers = [page.page for page in self.pages]
        if len(page_numbers) != len(set(page_numbers)):
            raise ValueError("page numbers must be unique")
        return self


class ReviewPolicy(ContractModel):
    minimum_overall_confidence: Annotated[float, Field(ge=0, le=1)] = 0.85
    minimum_critical_field_confidence: Annotated[float, Field(ge=0, le=1)] = 0.90
    required_fields: frozenset[Annotated[str, Field(min_length=1, max_length=200)]] = frozenset()
    critical_fields: frozenset[Annotated[str, Field(min_length=1, max_length=200)]] = frozenset()
    review_warning_codes: frozenset[StableCode] = frozenset(
        {
            "illegible_document",
            "incomplete_document",
            "low_input_quality",
            "provider_disagreement",
            "unknown_document_type",
        }
    )


class ProcessingDecision(StrEnum):
    WAIT = "wait"
    REVIEW = "review"
    REJECT = "reject"
    COMPLETE = "complete"


DEFAULT_REVIEW_POLICY: Final = ReviewPolicy()


def decide(
    result: DocumentResult, policy: ReviewPolicy = DEFAULT_REVIEW_POLICY
) -> ProcessingDecision:
    match result.status:
        case "accepted" | "inspecting" | "processing" | "validating" | "cancelling":
            return ProcessingDecision.WAIT
        case "partial" | "review_required":
            return ProcessingDecision.REVIEW
        case "cancelled" | "rejected":
            return ProcessingDecision.REJECT
        case "completed":
            fields = {field.name: field for field in result.fields}
            if (
                result.confidence is None
                or result.confidence.overall < policy.minimum_overall_confidence
            ):
                return ProcessingDecision.REVIEW
            if any(failure.severity == "error" for failure in result.validation_failures):
                return ProcessingDecision.REVIEW
            if set(result.warnings) & policy.review_warning_codes:
                return ProcessingDecision.REVIEW
            if not policy.required_fields.issubset(fields):
                return ProcessingDecision.REVIEW
            if any(
                name not in fields
                or fields[name].confidence < policy.minimum_critical_field_confidence
                for name in policy.critical_fields
            ):
                return ProcessingDecision.REVIEW
            return ProcessingDecision.COMPLETE
        case unreachable:
            assert_never(unreachable)


__all__ = [
    "DEFAULT_REVIEW_POLICY",
    "Citation",
    "Confidence",
    "Cost",
    "DocumentPage",
    "DocumentRequest",
    "DocumentResult",
    "ExtractedField",
    "ProcessingDecision",
    "ReviewPolicy",
    "Table",
    "TableCell",
    "TextObservation",
    "ValidationFailure",
    "decide",
]
