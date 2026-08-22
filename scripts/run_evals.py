"""Run reviewed evaluation suites through Agent Gateway and Vertex."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx
import yaml
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from kora_agents.evaluation import EvaluationSuite, evaluate, parse_a2a_response


class EvalSettings(BaseSettings):
    """Secrets and endpoint for an identity-aware live evaluation."""

    model_config = SettingsConfigDict(
        env_prefix="KORA_EVAL_",
        frozen=True,
        extra="forbid",
    )

    gateway_origin: str
    gateway_api_key: SecretStr
    end_user_token: SecretStr
    timeout_seconds: float = Field(default=75.0, gt=0, le=120)


def _bearer(value: SecretStr) -> str:
    token = value.get_secret_value().strip()
    return token if token.lower().startswith("bearer ") else f"Bearer {token}"


def _load_suite(path: Path) -> EvaluationSuite:
    return EvaluationSuite.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


async def _run(paths: list[Path], settings: EvalSettings) -> int:
    headers = {
        "Authorization": _bearer(settings.gateway_api_key),
        "X-Kora-End-User-Token": _bearer(settings.end_user_token),
    }
    passed = 0
    failed = 0
    origin = settings.gateway_origin.rstrip("/")

    async with httpx.AsyncClient(timeout=settings.timeout_seconds, headers=headers) as client:
        for path in paths:
            suite = _load_suite(path)
            endpoint = f"{origin}/a2a/v1/{suite.suite}"
            for index, case in enumerate(suite.cases, start=1):
                body = {
                    "jsonrpc": "2.0",
                    "id": f"eval-{suite.suite}-{index}",
                    "method": "message/send",
                    "params": {
                        "message": {
                            "role": "user",
                            "parts": [{"kind": "text", "text": case.input}],
                        }
                    },
                }
                try:
                    response = await client.post(endpoint, json=body)
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise ValueError("A2A response is not an object")
                    output, refused = parse_a2a_response(payload)
                    failures = evaluate(case, output, refused=refused)
                except (httpx.HTTPError, json.JSONDecodeError, ValueError) as error:
                    failures = (f"evaluation request failed: {type(error).__name__}",)

                label = f"{suite.suite}/{case.name}"
                if failures:
                    failed += 1
                    print(f"FAIL {label}: {'; '.join(failures)}")
                else:
                    passed += 1
                    print(f"PASS {label}")

    print(f"Evaluation result: {passed} passed, {failed} failed")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Kora's YAML evaluation suites through the live Agent Gateway.",
    )
    parser.add_argument(
        "suites",
        nargs="*",
        type=Path,
        default=sorted(Path("evals").glob("*.yaml")),
        help="suite YAML files; defaults to evals/*.yaml",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.suites, EvalSettings()))


if __name__ == "__main__":
    raise SystemExit(main())
