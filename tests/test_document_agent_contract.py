import pytest
from pydantic import ValidationError

from document_agent.contracts import (
    Citation,
    DocumentRequest,
    DocumentResult,
    ExtractedField,
    ProcessingDecision,
    decide,
)


def test_document_request_accepts_exactly_one_opaque_service_reference() -> None:
    request = DocumentRequest(
        upload_id="upl_SYNTHETIC",
        document_type="invoice",
        output_format="structured",
        include_evidence=True,
    )

    assert request.upload_id == "upl_SYNTHETIC"
    assert request.job_id is None

    with pytest.raises(ValidationError):
        DocumentRequest(
            upload_id="upl_SYNTHETIC",
            job_id="job_EXISTING",
            document_type="auto",
            output_format="structured",
            include_evidence=True,
        )
    with pytest.raises(ValidationError):
        DocumentRequest.model_validate(
            {
                "upload_id": "upl_SYNTHETIC",
                "document_type": "auto",
                "output_format": "structured",
                "include_evidence": True,
                "tenant_id": "ten_ATTACKER",
            }
        )


def test_extracted_fields_require_valid_source_evidence() -> None:
    with pytest.raises(ValidationError):
        ExtractedField(
            name="invoice_number", value_json='"INV-1048"', confidence=0.98, citations=[]
        )

    field = ExtractedField(
        name="invoice_number",
        value_json='"INV-1048"',
        confidence=0.98,
        citations=[citation()],
    )
    result = DocumentResult(
        job_id="job_SYNTHETIC",
        status="completed",
        content_trust="untrusted",
        fields=[field],
    )

    assert result.fields[0].citations[0].page == 1


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("accepted", ProcessingDecision.WAIT),
        ("processing", ProcessingDecision.WAIT),
        ("partial", ProcessingDecision.REVIEW),
        ("review_required", ProcessingDecision.REVIEW),
        ("rejected", ProcessingDecision.REJECT),
        ("cancelled", ProcessingDecision.REJECT),
        ("completed", ProcessingDecision.COMPLETE),
    ],
)
def test_result_state_deterministically_routes_agent_behavior(
    status: str, expected: ProcessingDecision
) -> None:
    result = DocumentResult.model_validate(
        {
            "job_id": "job_SYNTHETIC",
            "status": status,
            "content_trust": "untrusted",
            "fields": [],
        }
    )

    assert decide(result) is expected


def citation() -> Citation:
    return Citation(
        document_version=f"sha256:{'a' * 64}",
        page=1,
        polygon=[[0.1, 0.1], [0.3, 0.1], [0.3, 0.2], [0.1, 0.2]],
        observation_id="obs_invoice_number_1",
    )
