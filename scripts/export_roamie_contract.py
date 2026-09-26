import copy
import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from roamie_agents.contracts import RecommendationRequest, Specialist
from roamie_agents.evidence import EvidenceBatch


class Search(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    specialist: Specialist
    request: RecommendationRequest


def bounded(value):
    if isinstance(value, dict):
        value.pop("pattern", None)
        if value.get("type") == "string" and "enum" not in value:
            value.setdefault("maxLength", 2048)
        if value.get("type") == "integer":
            value.setdefault("maximum", 10**12)
        for child in value.values():
            bounded(child)
    elif isinstance(value, list):
        for child in value:
            bounded(child)


if __name__ == "__main__":
    contract = {"input": Search.model_json_schema(), "output": EvidenceBatch.model_json_schema()}
    # The existing travel MCP does not handle manager-owned planning providers.
    for schema in contract.values():
        definitions = schema.get("$defs", {})
        definitions.get("Specialist", {}).get("enum", []).remove("weather")
        definitions.get("Specialist", {}).get("enum", []).remove("entry-guidance")
        for name in ("WeatherDetails", "EntryDetails"):
            definitions.pop(name, None)
        for name in ("weather", "entry"):
            definitions.get("Evidence", {}).get("properties", {}).pop(name, None)
        for name in (
            "destination_country",
            "passport_country",
            "residence_country",
            "travel_purpose",
        ):
            definitions.get("RecommendationRequest", {}).get("properties", {}).pop(name, None)
    validation = copy.deepcopy(contract)
    bounded(contract)
    target = Path("contracts/roamie-travel.json")
    target.parent.mkdir(exist_ok=True)
    target.write_text(
        json.dumps({**contract, "validation": validation}, indent=2, sort_keys=True) + "\n"
    )
    digest = hashlib.sha256(
        json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    target.with_suffix(".sha256").write_text(digest + "\n")
