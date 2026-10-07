from __future__ import annotations

import unittest

from app.procurement.consistency import reconcile_field_inspection_requirement
from app.schemas import CompletionModelResponse
from app.work_order.completion import enforce_completion_human_authority


class CompletionAuthorityHardeningTests(unittest.TestCase):
    def test_completion_summary_cannot_claim_final_completion(self):
        raw = CompletionModelResponse(
            visible_issue_improved=True,
            visible_issue_still_present=False,
            insufficient_completion_image=False,
            additional_visible_concern=False,
            summary=(
                "The repair is complete and the work is ready for inspection. "
                "The images are sufficient to confirm the repair."
            ),
            observed_changes=[],
            limitations=[],
            needs_ppo_review=False,
        )
        normalized = enforce_completion_human_authority(raw)
        lowered = normalized.summary.lower()
        self.assertNotIn("repair is complete", lowered)
        self.assertNotIn("work is ready for inspection", lowered)
        self.assertIn("final completion acceptance remains with ppo head", lowered)
        self.assertTrue(normalized.needs_ppo_review)
        self.assertTrue(
            any("final completion" in item.lower() for item in normalized.limitations)
        )

    def test_needs_ppo_review_is_always_true(self):
        raw = CompletionModelResponse(
            visible_issue_improved=False,
            visible_issue_still_present=True,
            insufficient_completion_image=False,
            additional_visible_concern=False,
            summary="The original visible issue remains visible in the completion image.",
            observed_changes=[],
            limitations=["Only visible appearance was compared."],
            needs_ppo_review=False,
        )
        self.assertTrue(enforce_completion_human_authority(raw).needs_ppo_review)


class ProcurementClarificationConsistencyTests(unittest.TestCase):
    def test_inline_true_marker_cannot_disagree_with_boolean(self):
        response, required = reconcile_field_inspection_requirement(
            "The electrical status must be confirmed in the field. Field_inspection_required=true",
            False,
        )
        self.assertTrue(required)
        self.assertNotIn("field_inspection_required", response.lower())

    def test_positive_prose_raises_false_model_flag(self):
        response, required = reconcile_field_inspection_requirement(
            "A field inspection is required before the electrical condition can be confirmed.",
            False,
        )
        self.assertTrue(required)
        self.assertIn("field inspection is required", response.lower())


    def test_true_model_flag_is_not_downgraded_by_conflicting_negative_prose(self):
        text, required = reconcile_field_inspection_requirement(
            "No field inspection is required.", True
        )
        self.assertTrue(required)
        self.assertIn("field inspection is required", text.lower())

    def test_explicit_negative_prose_can_remain_false(self):
        response, required = reconcile_field_inspection_requirement(
            "The recorded facts answer this question and no field inspection is required.",
            False,
        )
        self.assertFalse(required)
        self.assertIn("no field inspection is required", response.lower())


if __name__ == "__main__":
    unittest.main()
