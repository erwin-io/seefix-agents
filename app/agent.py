from __future__ import annotations

import time

from .config import Settings
from .image_utils import prepare_image
from .inspection.policy import apply_urgency_policy
from .inspection.prompts import PROMPT_VERSION
from .providers import OllamaProvider
from .schemas import AnalysisStatus, CategoryReference, InspectionResult
from .scoring import PriorityContext, calculate_priority


class FacilityInspectionAgent:
    """Local SEEFIX image/model engine. Database orchestration stays outside."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.provider = OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout_seconds=settings.request_timeout_seconds,
            model_context=settings.model_context,
            max_output_tokens=settings.model_max_output_tokens,
            keep_alive=settings.model_keep_alive,
        )

    def analyze(
        self,
        image_bytes: bytes,
        *,
        report_context: dict | None = None,
    ) -> InspectionResult:
        started = time.perf_counter()
        prepared = prepare_image(
            image_bytes,
            max_upload_bytes=self.settings.max_upload_mb * 1024 * 1024,
            max_dimension=self.settings.max_image_dimension,
        )
        provider_result = self.provider.analyze(
            prepared.jpeg_bytes,
            report_context=report_context,
        )

        assessment = provider_result.assessment
        policy = None
        priority = None
        if assessment is not None:
            # Apply hard policy immediately; DB reference can only raise/strengthen it later.
            assessment, policy = apply_urgency_policy(assessment)
            priority = calculate_priority(assessment)

        elapsed_ms = round((time.perf_counter() - started) * 1000)
        return InspectionResult(
            analysis_status=(
                AnalysisStatus.ASSESSED
                if provider_result.scope_validation.should_analyze
                else AnalysisStatus.NO_ASSESSMENT
            ),
            scope_validation=provider_result.scope_validation,
            assessment=assessment,
            priority=priority,
            policy=policy,
            provider=self.provider.name,
            model_id=self.provider.model_id,
            prompt_version=PROMPT_VERSION,
            original_width=prepared.original_width,
            original_height=prepared.original_height,
            processed_width=prepared.processed_width,
            processed_height=prepared.processed_height,
            processing_time_ms=elapsed_ms,
        )

    def apply_database_context(
        self,
        result: InspectionResult,
        *,
        priority_context: PriorityContext,
        category_reference: CategoryReference,
        duration_reference: str | None = None,
    ) -> InspectionResult:
        """Apply DB reference policy and deterministic DB-derived scoring context."""
        if result.assessment is None:
            return result

        assessment = result.assessment
        # Re-run policy with the current active category reference. The policy is
        # non-downgrading, so this can only preserve or raise model/hard-policy urgency.
        assessment, policy = apply_urgency_policy(
            assessment,
            category_reference=category_reference,
        )

        if duration_reference:
            assumptions = list(assessment.duration_assumptions)
            if duration_reference not in assumptions:
                assumptions.append(duration_reference)
            assessment = assessment.model_copy(
                update={"duration_assumptions": assumptions[:8]}
            )

        priority = calculate_priority(assessment, priority_context)
        return result.model_copy(
            update={
                "assessment": assessment,
                "policy": policy,
                "priority": priority,
            }
        )
