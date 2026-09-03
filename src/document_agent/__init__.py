"""Reusable evidence-first document agent contracts."""

from document_agent.contracts import (
    DEFAULT_REVIEW_POLICY,
    Citation,
    Confidence,
    Cost,
    DocumentPage,
    DocumentRequest,
    DocumentResult,
    ExtractedField,
    ProcessingDecision,
    ReviewPolicy,
    Table,
    TableCell,
    TextObservation,
    ValidationFailure,
    decide,
)

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
