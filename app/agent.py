from __future__ import annotations

import time

from .config import Settings
from .image_utils import prepare_image
from .prompts import PROMPT_VERSION
from .providers import MockProvider, ModelProvider, OllamaProvider
from .schemas import AnalysisStatus, InspectionResult
from .scoring import calculate_priority


class FacilityInspectionAgent:
    def __init__(self, settings: Settings, provider: ModelProvider | None = None) -> None:
        self.settings = settings
        self.provider = provider or self._create_provider(settings.provider)

    def _create_provider(self, provider_name: str) -> ModelProvider:
        if provider_name == "mock":
            return MockProvider()
        if provider_name == "ollama":
            return OllamaProvider(
                base_url=self.settings.ollama_base_url,
                model=self.settings.ollama_model,
                timeout_seconds=self.settings.request_timeout_seconds,
                model_context=self.settings.model_context,
                max_output_tokens=self.settings.model_max_output_tokens,
                keep_alive=self.settings.model_keep_alive,
            )
        raise ValueError("SEEFIX_PROVIDER must be either 'mock' or 'ollama'.")

    def analyze(self, image_bytes: bytes) -> InspectionResult:
        started = time.perf_counter()
        prepared = prepare_image(
            image_bytes,
            max_upload_bytes=self.settings.max_upload_mb * 1024 * 1024,
            max_dimension=self.settings.max_image_dimension,
        )
        provider_result = self.provider.analyze(prepared.jpeg_bytes)
        assessment = provider_result.assessment
        priority = calculate_priority(assessment) if assessment is not None else None
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
            provider=self.provider.name,
            model_id=self.provider.model_id,
            prompt_version=PROMPT_VERSION,
            original_width=prepared.original_width,
            original_height=prepared.original_height,
            processed_width=prepared.processed_width,
            processed_height=prepared.processed_height,
            processing_time_ms=elapsed_ms,
        )
