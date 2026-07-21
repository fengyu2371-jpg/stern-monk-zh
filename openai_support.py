from __future__ import annotations

from typing import Any


def reasoning_options(model: str) -> dict[str, dict[str, str]]:
    normalized = model.strip().lower()
    if normalized == "gpt-5-nano" or normalized.startswith("gpt-5-nano-"):
        return {"reasoning": {"effort": "minimal"}}
    return {}


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def response_diagnostics(response: Any) -> str:
    incomplete_details = _field(response, "incomplete_details")
    usage = _field(response, "usage")
    output_details = _field(usage, "output_tokens_details")
    output = _field(response, "output", []) or []
    output_types = [str(_field(item, "type", "unknown")) for item in output]

    return (
        f"status={_field(response, 'status', 'unknown')} "
        f"incomplete_reason={_field(incomplete_details, 'reason', None)} "
        f"output_types={output_types} "
        f"output_tokens={_field(usage, 'output_tokens', None)} "
        f"reasoning_tokens={_field(output_details, 'reasoning_tokens', None)}"
    )
