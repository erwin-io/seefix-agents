from __future__ import annotations

import unittest
from io import BytesIO

from PIL import Image

from app.agent import FacilityInspectionAgent
from app.config import Settings
from app.image_utils import InvalidImageError, prepare_image
from app.json_utils import extract_json_object
from app.schemas import (
    AnalysisCertainty,
    AnalysisStatus,
    FacilityCategory,
    ModelAssessment,
    ModelInspectionResponse,
    RiskFlags,
    ScopeDecision,
    Urgency,
)


def make_image(width: int = 1800, height: int = 900) -> bytes:
    image = Image.new("RGB", (width, height), color=(80, 90, 100))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class ImageTests(unittest.TestCase):
    def test_resize_preserves_aspect_ratio(self) -> None:
        prepared = prepare_image(
            make_image(), max_upload_bytes=10 * 1024 * 1024, max_dimension=1280
        )
        self.assertEqual((prepared.original_width, prepared.original_height), (1800, 900))
        self.assertEqual((prepared.processed_width, prepared.processed_height), (1280, 640))

    def test_invalid_image_is_rejected(self) -> None:
        with self.assertRaises(InvalidImageError):
            prepare_image(b"not an image", max_upload_bytes=1024, max_dimension=1280)


class JsonTests(unittest.TestCase):
    def test_markdown_fence_is_removed(self) -> None:
        self.assertEqual(extract_json_object('```json\n{"ok": true}\n```'), {"ok": True})

    def test_missing_comma_between_object_fields_is_repaired(self) -> None:
        malformed = '{\n  "summary": "Visible wall crack"\n  "needs_review": true\n}'
        self.assertEqual(
            extract_json_object(malformed),
            {"summary": "Visible wall crack", "needs_review": True},
        )

    def test_trailing_comma_is_repaired(self) -> None:
        self.assertEqual(extract_json_object('{"ok": true,}'), {"ok": True})

    def test_unclosed_containers_are_repaired(self) -> None:
        self.assertEqual(
            extract_json_object('{"items": ["crack"]'),
            {"items": ["crack"]},
        )


class AgentTests(unittest.TestCase):
    def test_compact_model_assessment_expands_to_public_schema(self) -> None:
        compact = ModelAssessment(
            category=FacilityCategory.STRUCTURAL_SURFACE,
            summary="A visible wall crack requires inspection.",
            observed_evidence=["A diagonal crack is visible above the doorway."],
            possible_causes=["Material movement is possible."],
            risk_flags=RiskFlags(public_access_exposure=True),
            recommended_urgency=Urgency.MEDIUM,
            urgency_reason="The defect is located above a public hallway.",
            estimated_min_hours=1,
            estimated_max_hours=4,
            analysis_certainty=AnalysisCertainty.MEDIUM,
            needs_review=True,
            limitation="Depth cannot be confirmed from one photograph.",
        )

        assessment = compact.to_agent_assessment()
        self.assertEqual(assessment.recommended_urgency, Urgency.MEDIUM)
        self.assertEqual(len(assessment.urgency_reasons), 1)
        self.assertTrue(assessment.risk_flags.public_access_exposure)
        self.assertTrue(assessment.needs_review)

    def test_in_scope_model_response_requires_assessment(self) -> None:
        with self.assertRaises(ValueError):
            ModelInspectionResponse(
                scope_decision=ScopeDecision.FACILITY_ISSUE,
                scope_reason="A damaged facility component is visible.",
                detected_subjects=["wall"],
                assessment=None,
            )

    def test_out_of_scope_model_response_becomes_no_assessment(self) -> None:
        model_result = ModelInspectionResponse(
            scope_decision=ScopeDecision.OUT_OF_SCOPE,
            scope_reason="The image shows a dog and no facility issue.",
            detected_subjects=["dog", "grass"],
            assessment=None,
        )

        provider_result = model_result.to_provider_result()
        self.assertFalse(provider_result.scope_validation.should_analyze)
        self.assertIsNone(provider_result.assessment)

    def test_mock_agent_returns_valid_structured_result(self) -> None:
        agent = FacilityInspectionAgent(Settings(provider="mock"))
        result = agent.analyze(make_image(640, 480))
        self.assertEqual(result.provider, "mock")
        self.assertEqual(result.analysis_status, AnalysisStatus.NO_ASSESSMENT)
        self.assertEqual(
            result.scope_validation.decision,
            ScopeDecision.INSUFFICIENT_IMAGE,
        )
        self.assertIsNone(result.assessment)
        self.assertIsNone(result.priority)


if __name__ == "__main__":
    unittest.main()
