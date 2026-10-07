from __future__ import annotations

import re

_INLINE_FLAG = re.compile(
    r"(?i)(?:^|\s)[\[(]?field_inspection_required\s*=\s*(true|false)[\])]?\s*[.,;:]?"
)
_POSITIVE_PATTERNS = (
    re.compile(r"(?i)\bfield inspection is required\b"),
    re.compile(r"(?i)\brequires? (?:a )?field inspection\b"),
    re.compile(r"(?i)\bmust be (?:verified|confirmed|checked|inspected) (?:in|on) the field\b"),
    re.compile(r"(?i)\bfield verification is required\b"),
)
_NEGATIVE_PATTERNS = (
    re.compile(r"(?i)\bfield inspection is not required\b"),
    re.compile(r"(?i)\bno field inspection is required\b"),
    re.compile(r"(?i)\bdoes not require (?:a )?field inspection\b"),
)


def reconcile_field_inspection_requirement(
    response_draft: str,
    model_flag: bool,
) -> tuple[str, bool]:
    """Make the structured flag agree with the human-readable draft.

    Qwen occasionally writes ``field_inspection_required=true`` inside the
    prose while returning the structured boolean as false. The prose marker is
    removed and positive/negative natural-language statements are reconciled
    deterministically. Mixed or unsafe wording resolves to ``True``.
    """
    text = " ".join(str(response_draft).split()).strip()
    inline_values = [value.lower() == "true" for value in _INLINE_FLAG.findall(text)]
    text = _INLINE_FLAG.sub(" ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,.;")

    negative = any(pattern.search(text) for pattern in _NEGATIVE_PATTERNS)
    positive_text = text
    for pattern in _NEGATIVE_PATTERNS:
        positive_text = pattern.sub(" ", positive_text)
    positive = any(pattern.search(positive_text) for pattern in _POSITIVE_PATTERNS)

    # ``True`` is sticky: when any structured/prose signal says that a field
    # inspection is required, never normalize it down to false just because a
    # conflicting sentence says otherwise. A false result is allowed only when
    # the model flag is false and the prose is consistently negative/false.
    if bool(model_flag) or True in inline_values or positive:
        required = True
    elif negative or (inline_values and all(value is False for value in inline_values)):
        required = False
    else:
        required = False

    # A mixed positive/negative answer is ambiguous; keep the safer requirement.
    if positive and negative:
        required = True

    if required:
        # Remove any explicit negative sentence fragment when a safer/structured
        # signal requires field inspection; do not persist contradictory prose.
        for pattern in _NEGATIVE_PATTERNS:
            text = pattern.sub("", text)
        text = re.sub(r"\s{2,}", " ", text).strip(" ,.;")
        if not positive:
            suffix = "A field inspection is required before PPO Head finalizes any fact not established in the recorded SEEFIX evidence."
            text = f"{text}. {suffix}" if text else suffix

    return text[:1800], required
