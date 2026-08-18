from kora_agents.safety import MedicalSafetyGuard


async def test_medical_safety_blocks_diagnostic_or_dosage_claims() -> None:
    guard = MedicalSafetyGuard()

    diagnosis = await guard.check_output("You definitely have diabetes.")
    dosage = await guard.check_output("Take 500 mg of this supplement every day.")
    general = await guard.check_output("Include a variety of vegetables when practical.")

    assert diagnosis.code == "medical_advice_blocked"
    assert dosage.code == "medical_advice_blocked"
    assert general.code == ""
