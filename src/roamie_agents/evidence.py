import asyncio
import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, ValidationError
from tesserix_adk.adapters.mcp import McpSession
from tesserix_adk.core import AdkError

from roamie_agents.base import Contract
from roamie_agents.contracts import Evidence, RecommendationRequest, Specialist
from roamie_agents.exchange import ExchangeQuote, ReferenceRate
from roamie_agents.runtime import TravelFailure


class EvidenceBatch(Contract):
    reference_rate: ReferenceRate | None = None
    exchange_quotes: Annotated[list[ExchangeQuote], Field(max_length=40)] = Field(
        default_factory=list
    )
    status: Literal["ok", "unavailable"]
    facts: Annotated[list[Evidence], Field(max_length=40)] = Field(default_factory=list)


class MCPSource:
    def __init__(self, *, session: McpSession, tool_name: str, schema_digest: str) -> None:
        self._session = session
        self._tool = tool_name
        self._digest = schema_digest

    async def search(self, specialist: Specialist, request: RecommendationRequest) -> EvidenceBatch:
        try:
            async with asyncio.timeout(20):
                info = await self._session.initialize()
                if "tools" not in info.capabilities:
                    raise TravelFailure("tools_unavailable")
                tools = await self._session.list_tools()
                tool = next((item for item in tools if item.name == self._tool), None)
                if tool is None:
                    raise TravelFailure("tool_unavailable")
                digest = hashlib.sha256(
                    json.dumps(
                        {"input": tool.input_schema, "output": tool.output_schema},
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ).encode()
                ).hexdigest()
                if digest != self._digest:
                    raise TravelFailure("schema_mismatch")
                result = await self._session.call_tool(
                    self._tool,
                    {
                        "specialist": specialist.value,
                        "request": request.model_dump(
                            mode="json",
                            exclude={
                                "destination_country",
                                "passport_country",
                                "residence_country",
                                "travel_purpose",
                            },
                        ),
                    },
                    meta={},
                    timeout_seconds=25,
                )
                if result.is_error or result.structured_content is None:
                    raise TravelFailure("provider_unavailable")
                return EvidenceBatch.model_validate(result.structured_content)
        except (TimeoutError, ConnectionError, AdkError, ValidationError) as error:
            raise TravelFailure("provider_unavailable") from error
