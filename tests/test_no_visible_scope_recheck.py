"""Unit tests for scope/assessment repair, with mocked Ollama responses.

Run from the root of the existing seefix-agents project:
    python -m unittest discover -s tests -p 'test_no_visible_scope_recheck.py' -v
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import Mock

from app.providers.base import ProviderError
from app.providers.ollama import OllamaProvider
from app.schemas import ScopeDecision


def response(data: dict) -> dict:
    return {"message": {"content": json.dumps(data)}}


def provider(*results: dict) -> OllamaProvider:
    instance = OllamaProvider(
        base_url="http://127.0.0.1:11434",
        model="qwen3-vl:2b-instruct-q4_K_M",
        timeout_seconds=60,
        model_context=4096,
        max_output_tokens=2048,
        keep_alive="5m",
    )
    instance._chat = Mock(side_effect=[response(value) for value in results])
    return instance


NO_ISSUE = {
    "scope_decision": "No Visible Maintenance Issue",
    "scope_reason": "No obvious maintenance defect appears in this image.",
    "detected_subjects": ["staircase", "railing"],
    "assessment": None,
}

MISMATCH = {
    "scope_decision": "Facility Issue",
    "scope_reason": "A staircase is shown but no specific defect is identifiable.",
    "detected_subjects": ["staircase"],
    "assessment": None,
}

ASSESSMENT = {
    "category": "Structural and Surface Damage",
    "summary": "Several broken concrete steps are visibly damaged.",
    "observed_evidence": ["Two broken steps with visible cracks and concrete fragments."],
    "possible_causes": ["Repeated heavy loading."],
    "risk_flags": {"structural_instability_indicator": True},
    "recommended_urgency": "High",
    "urgency_reason": "Broken steps create a possible fall hazard.",
    "estimated_min_hours": 4,
    "estimated_max_hours": 12,
    "analysis_certainty": "Medium",
    "needs_review": True,
    "limitation": "A photo does not establish hidden structural conditions.",
}

ISSUE = {
    "scope_decision": "Facility Issue",
    "scope_reason": "Broken concrete steps are clearly visible.",
    "detected_subjects": ["damaged staircase"],
    "assessment": ASSESSMENT,
}


class VisualScopeRepairTests(unittest.TestCase):
    def test_conflicting_scope_gets_image_based_reinspection(self):
        p = provider(MISMATCH, NO_ISSUE)
        result = p.analyze(
            b"synthetic-image-bytes",
            report_context={"description": "Visible facility issue requiring maintenance."},
        )
        self.assertEqual(result.scope_validation.decision, ScopeDecision.NO_VISIBLE_ISSUE)
        self.assertFalse(result.scope_validation.should_analyze)
        self.assertIsNone(result.assessment)
        self.assertEqual(p._chat.call_count, 2)
        original, correction = [call.args[0] for call in p._chat.call_args_list]
        self.assertIn("images", original["messages"][-1])
        self.assertIn("images", correction["messages"][-1])
        self.assertNotIn(
            "Visible facility issue requiring maintenance.",
            correction["messages"][-1]["content"],
        )
        self.assertIn("FROM THE IMAGE ONLY", correction["messages"][-1]["content"])

    def test_actual_damage_is_not_silently_downgraded(self):
        p = provider(MISMATCH, ISSUE)
        result = p.analyze(b"synthetic-image-bytes")
        self.assertEqual(result.scope_validation.decision, ScopeDecision.FACILITY_ISSUE)
        self.assertIsNotNone(result.assessment)
        self.assertEqual(result.assessment.category.value, "Structural and Surface Damage")

    def test_reinspection_cannot_auto_accept_persistently_invalid_output(self):
        p = provider(MISMATCH, MISMATCH)
        with self.assertRaises(ProviderError):
            p.analyze(b"synthetic-image-bytes")
        self.assertEqual(p._chat.call_count, 2)

    def test_regular_json_shape_repair_avoids_image_resend(self):
        incomplete = dict(NO_ISSUE)
        del incomplete["scope_reason"]
        p = provider(incomplete, NO_ISSUE)
        result = p.analyze(b"synthetic-image-bytes")
        self.assertEqual(result.scope_validation.decision, ScopeDecision.NO_VISIBLE_ISSUE)
        original, correction = [call.args[0] for call in p._chat.call_args_list]
        self.assertIn("images", original["messages"][-1])
        self.assertNotIn("images", correction["messages"][-1])

    def test_valid_no_issue_requires_only_one_model_call(self):
        p = provider(NO_ISSUE)
        result = p.analyze(b"synthetic-image-bytes")
        self.assertEqual(result.scope_validation.decision, ScopeDecision.NO_VISIBLE_ISSUE)
        self.assertEqual(p._chat.call_count, 1)

    def test_non_issue_with_assessment_is_also_reinspected(self):
        invalid = dict(NO_ISSUE, assessment=ASSESSMENT)
        p = provider(invalid, NO_ISSUE)
        result = p.analyze(b"synthetic-image-bytes")
        self.assertEqual(result.scope_validation.decision, ScopeDecision.NO_VISIBLE_ISSUE)
        _, correction = [call.args[0] for call in p._chat.call_args_list]
        self.assertIn("images", correction["messages"][-1])


if __name__ == "__main__":
    unittest.main()
