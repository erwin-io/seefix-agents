from __future__ import annotations

import json
import re


_MISSING_OBJECT_COMMA = re.compile(
    r'("|\]|\}|true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)'
    r'([ \t]*\r?\n[ \t]*"(?:[^"\\]|\\.)+"[ \t]*:)'
)


def _strip_markdown_fence(text: str) -> str:
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned

    lines = cleaned.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _object_candidate(text: str) -> str:
    start = text.find("{")
    if start < 0:
        raise ValueError("Model response did not contain a JSON object.")

    end = text.rfind("}")
    return text[start : end + 1] if end > start else text[start:]


def _close_open_containers(text: str) -> str:
    """Close only clearly open JSON objects/arrays; never repair an open string."""
    stack: list[str] = []
    in_string = False
    escaped = False

    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue

        if character == '"':
            in_string = True
        elif character in "{[":
            stack.append(character)
        elif character == "}" and stack and stack[-1] == "{":
            stack.pop()
        elif character == "]" and stack and stack[-1] == "[":
            stack.pop()

    if in_string:
        return text

    closers = {"{": "}", "[": "]"}
    return text + "".join(closers[item] for item in reversed(stack))


def _repair_common_json_issues(text: str) -> str:
    repaired = _MISSING_OBJECT_COMMA.sub(r"\1,\2", text)
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    return _close_open_containers(repaired)


def extract_json_object(text: str) -> dict:
    cleaned = _strip_markdown_fence(text)
    candidates = [cleaned]

    object_candidate = _object_candidate(cleaned)
    if object_candidate != cleaned:
        candidates.append(object_candidate)

    repaired_candidate = _repair_common_json_issues(object_candidate)
    if repaired_candidate not in candidates:
        candidates.append(repaired_candidate)

    last_error: json.JSONDecodeError | None = None
    value = None
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            break
        except json.JSONDecodeError as exc:
            last_error = exc

    if value is None:
        raise ValueError(f"Model response contained invalid JSON: {last_error}")

    if not isinstance(value, dict):
        raise ValueError("Model response JSON must be an object.")
    return value
