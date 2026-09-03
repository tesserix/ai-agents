"""Reusable evidence-first document agent contracts."""

from document_agent.contracts import (
    Citation,
    DocumentRequest,
    DocumentResult,
    ExtractedField,
    ProcessingDecision,
    decide,
)

__all__ = [
    "Citation",
    "DocumentRequest",
    "DocumentResult",
    "ExtractedField",
    "ProcessingDecision",
    "decide",
]
