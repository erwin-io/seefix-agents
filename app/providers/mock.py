from __future__ import annotations

from ..schemas import (
    ProviderAnalysis,
    ScopeDecision,
    ScopeValidation,
)
from .base import ModelProvider


class MockProvider(ModelProvider):
    """Development provider. It does not perform real visual inference."""

    name = "mock"
    model_id = "mock-facility-agent-v1"

    def analyze(self, image_bytes: bytes) -> ProviderAnalysis:
        return ProviderAnalysis(
            scope_validation=ScopeValidation(
                decision=ScopeDecision.INSUFFICIENT_IMAGE,
                should_analyze=False,
                reason="Mock mode does not inspect image pixels.",
                detected_subjects=[],
            ),
            assessment=None,
        )
