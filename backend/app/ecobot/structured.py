from __future__ import annotations

import json
import re
from typing import Any


class StructuredOutputError(ValueError):
    pass


def parse_json_object(text: str) -> dict[str, Any]:
    source = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", source, re.IGNORECASE)
    if fence:
        source = fence.group(1).strip()
    else:
        start = source.find("{")
        end = source.rfind("}")
        if start >= 0 and end > start:
            source = source[start : end + 1]
    try:
        value = json.loads(source)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(f"invalid JSON output: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise StructuredOutputError("structured output must be a JSON object")
    return value
