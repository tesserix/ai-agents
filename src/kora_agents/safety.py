"""Deterministic safety checks applied inside every ADK run."""

import re

from tesserix_adk.guardrails import Guard, GuardResult

_DIAGNOSIS = re.compile(
    r"\b(?:you (?:definitely )?have|you are suffering from|the diagnosis is)\b",
    re.IGNORECASE,
)
_DOSAGE = re.compile(
    r"\b(?:take|use)\s+\d+(?:\.\d+)?\s*(?:mcg|mg|g|ml)\b",
    re.IGNORECASE,
)


class MedicalSafetyGuard(Guard):
    """Fail closed when an answer makes a diagnosis or dosage instruction."""

    name = "medical_safety"

    async def check_output(self, content: str) -> GuardResult:
        """Block high-risk medical claims without retaining the answer."""
        if _DIAGNOSIS.search(content) or _DOSAGE.search(content):
            return GuardResult.blocked(
                code="medical_advice_blocked",
                detail="diagnostic or dosage instruction",
            )
        return GuardResult.allow()
