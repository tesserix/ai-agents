"""Strict agent boundary for untrusted Document Intelligence results."""

from enum import StrEnum
from typing import Annotated, Literal, assert_never

from pydantic import BaseModel, ConfigDict, Field, model_validator

OpaqueUploadID = Annotated[str, Field(pattern=r"^upl_[A-Za-z0-9_]{1,64}$")]
OpaqueJobID = Annotated[str, Field(pattern=r"^job_[A-Za-z0-9_]{1,64}$")]
DocumentVersion = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
ObservationID = Annotated[str, Field(pattern=r"^obs_[A-Za-z0-9_]{1,64}$")]
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


class ExtractedField(ContractModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    value_json: Annotated[str, Field(min_length=1, max_length=100_000)]
    confidence: Annotated[float, Field(ge=0, le=1)]
    citations: Annotated[list[Citation], Field(min_length=1, max_length=100)]


class DocumentResult(ContractModel):
    job_id: OpaqueJobID
    status: JobStatus
    content_trust: Literal["untrusted"]
    fields: Annotated[list[ExtractedField], Field(max_length=5_000)]
    warnings: Annotated[list[str], Field(max_length=500)] = Field(default_factory=list)
    validation_failures: Annotated[list[str], Field(max_length=500)] = Field(default_factory=list)


class ProcessingDecision(StrEnum):
    WAIT = "wait"
    REVIEW = "review"
    REJECT = "reject"
    COMPLETE = "complete"


def decide(result: DocumentResult) -> ProcessingDecision:
    match result.status:
        case "accepted" | "inspecting" | "processing" | "validating" | "cancelling":
            return ProcessingDecision.WAIT
        case "partial" | "review_required":
            return ProcessingDecision.REVIEW
        case "cancelled" | "rejected":
            return ProcessingDecision.REJECT
        case "completed":
            return ProcessingDecision.COMPLETE
        case unreachable:
            assert_never(unreachable)


__all__ = [
    "Citation",
    "DocumentRequest",
    "DocumentResult",
    "ExtractedField",
    "ProcessingDecision",
    "decide",
]
