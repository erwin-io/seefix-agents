from __future__ import annotations

import re
import time

from ..schemas import CompletionAssessmentResult, CompletionModelResponse
from .prompts import COMPLETION_PROMPT_VERSION

_FINAL_REVIEW_SENTENCE = (
    "Visual comparison only; final completion acceptance remains with PPO Head."
)
_HIDDEN_LIMITATION = (
    "Images cannot establish hidden repair quality, code compliance, or final completion acceptance."
)


def planned_vs_actual(bundle) -> dict:
    def compare(label: str, planned, actual) -> dict:
        if planned is None or actual is None:
            return {"label": label, "planned": planned, "actual": actual, "difference": None}
        return {
            "label": label,
            "planned": planned,
            "actual": actual,
            "difference": round(float(actual) - float(planned), 3),
        }

    return {
        "durationDays": compare("Duration days", bundle.planned_duration_days, bundle.actual_duration_days),
        "crewSize": compare("Crew size", bundle.planned_crew_size, bundle.actual_crew_size),
        "laborHours": compare("Labor hours", bundle.planned_labor_hours, bundle.actual_labor_hours),
        "plannedMaterialCount": len(bundle.planned_materials),
        "actualMaterialCount": len(bundle.actual_materials),
    }


def choose_visual_result(model: CompletionModelResponse) -> str:
    if model.insufficient_completion_image:
        return "INSUFFICIENT_IMAGE"
    if model.visible_issue_still_present:
        return "ISSUE_STILL_VISIBLE"
    if model.additional_visible_concern:
        return "ADDITIONAL_CONCERN"
    if model.visible_issue_improved:
        return "VISIBLE_IMPROVEMENT"
    return "PPO_REVIEW_REQUIRED"


def _normalize_authority_wording(value: str) -> str:
    """Remove completion-authority wording while preserving visual observations."""
    text = " ".join(str(value).split())
    replacements = (
        (r"(?i)\bthe repair is complete\b", "the visible repair appears improved"),
        (r"(?i)\bthe work is complete\b", "the visible work appears improved"),
        (r"(?i)\bwork is ready for inspection\b", "the visible result is ready for PPO Head review"),
        (r"(?i)\brepair is ready for inspection\b", "the visible result is ready for PPO Head review"),
        (r"(?i)\bthe repair is in the final stage of completion\b", "the visible repair appears substantially improved"),
        (
            r"(?i)\bthe images? (?:are|is) sufficient to confirm the repair\b",
            "the images support a visible before/after comparison",
        ),
        (r"(?i)\bconfirm(?:s|ed)? the repair\b", "supports the visible repair comparison"),
        (r"(?i)\bthe repair has been completed\b", "the submitted image shows visible repair work"),
        (r"(?i)\bthe work has been completed\b", "the submitted image shows visible work"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    return text.strip()


def _normalize_summary(summary: str) -> str:
    text = _normalize_authority_wording(summary)
    if _FINAL_REVIEW_SENTENCE.lower() not in text.lower():
        max_body = max(0, 500 - len(_FINAL_REVIEW_SENTENCE) - 1)
        text = text[:max_body].rstrip(" ,.;")
        text = f"{text}. {_FINAL_REVIEW_SENTENCE}" if text else _FINAL_REVIEW_SENTENCE
    return text[:500]


def enforce_completion_human_authority(model: CompletionModelResponse) -> CompletionModelResponse:
    """
    PPO Head review is always mandatory in SEEFIX.

    The model may assess visible change, but it cannot declare the Work Order
    complete or waive the human close-out gate. This post-processing makes the
    persisted structured result deterministic even if the model phrases its
    response too strongly.
    """
    limitations = [" ".join(str(item).split()) for item in model.limitations if str(item).strip()]
    if not any("final completion" in item.lower() for item in limitations):
        limitations.append(_HIDDEN_LIMITATION)

    return model.model_copy(
        update={
            "summary": _normalize_summary(model.summary),
            "observed_changes": [
                _normalize_authority_wording(item)[:300]
                for item in model.observed_changes
                if str(item).strip()
            ][:8],
            "limitations": limitations[:8],
            "needs_ppo_review": True,
        }
    )


class CompletionAssessmentService:
    def __init__(self, provider) -> None:
        self.provider = provider

    def assess(self, *, bundle, images: list[bytes]) -> CompletionAssessmentResult:
        started = time.perf_counter()
        raw_model = self.provider.compare_completion(images=images, context=bundle.model_context())
        model = enforce_completion_human_authority(raw_model)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        return CompletionAssessmentResult(
            work_order_id=bundle.work_order_id,
            visual_result=choose_visual_result(model),
            model_response=model,
            planned_vs_actual=planned_vs_actual(bundle),
            provider=self.provider.name,
            model_id=self.provider.model_id,
            prompt_version=COMPLETION_PROMPT_VERSION,
            processing_time_ms=elapsed_ms,
        )
