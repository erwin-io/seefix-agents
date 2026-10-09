from __future__ import annotations

import unittest

from app.procurement.consistency import (
    reconcile_field_inspection_requirement,
)
from app.schemas import CompletionModelResponse
from app.work_order.completion import (
    enforce_completion_human_authority,
)
from app.work_order.readiness import (
    check_work_order_readiness,
)


class CompletionAuthorityHardeningTests(
    unittest.TestCase
):
    def test_completion_summary_cannot_claim_final_completion(
        self,
    ):
        raw = CompletionModelResponse(
            visible_issue_improved=True,
            visible_issue_still_present=False,
            insufficient_completion_image=False,
            additional_visible_concern=False,

            summary=(
                "The repair is complete. "
                "The area is clean and safe. "
                "The railing is properly installed "
                "and the wall is no longer damaged."
            ),

            observed_changes=[
                (
                    "The work has been completed "
                    "and the railing is correctly installed."
                )
            ],

            limitations=[],

            needs_maintenance_supervisor_review=False,
        )

        normalized = (
            enforce_completion_human_authority(
                raw
            )
        )

        lowered = (
            normalized
            .summary
            .lower()
        )

        self.assertNotIn(
            "the repair is complete",
            lowered,
        )

        self.assertNotIn(
            "clean and safe",
            lowered,
        )

        self.assertNotIn(
            "properly installed",
            lowered,
        )

        self.assertNotIn(
            "is no longer damaged",
            lowered,
        )

        self.assertIn(
            (
                "final completion acceptance "
                "remains with the maintenance supervisor"
            ),
            lowered,
        )

        self.assertTrue(
            normalized
            .needs_maintenance_supervisor_review
        )

        self.assertTrue(
            any(
                "final completion"
                in value.lower()

                for value
                in normalized.limitations
            )
        )

    def test_needs_maintenance_supervisor_review_is_always_true(
        self,
    ):
        raw = CompletionModelResponse(
            visible_issue_improved=True,
            visible_issue_still_present=False,
            insufficient_completion_image=False,
            additional_visible_concern=False,

            summary=(
                "The visible issue appears improved "
                "in the completion image."
            ),

            observed_changes=[],
            limitations=[],

            needs_maintenance_supervisor_review=False,
        )

        normalized = (
            enforce_completion_human_authority(
                raw
            )
        )

        self.assertTrue(
            normalized
            .needs_maintenance_supervisor_review
        )


class ProcurementClarificationConsistencyTests(
    unittest.TestCase
):
    def test_inline_true_marker_cannot_disagree_with_boolean(
        self,
    ):
        response, required = (
            reconcile_field_inspection_requirement(
                (
                    "The record is incomplete. "
                    "field_inspection_required=true"
                ),
                False,
            )
        )

        self.assertTrue(
            required
        )

        self.assertNotIn(
            "field_inspection_required",
            response.lower(),
        )

    def test_positive_prose_raises_false_model_flag(
        self,
    ):
        response, required = (
            reconcile_field_inspection_requirement(
                (
                    "A field inspection is required "
                    "before the condition can be confirmed."
                ),
                False,
            )
        )

        self.assertTrue(
            required
        )

        self.assertIn(
            "field inspection",
            response.lower(),
        )

    def test_true_model_flag_is_not_downgraded_by_conflicting_negative_prose(
        self,
    ):
        response, required = (
            reconcile_field_inspection_requirement(
                (
                    "No field inspection is required "
                    "based on the available record."
                ),
                True,
            )
        )

        self.assertTrue(
            required
        )

        self.assertNotIn(
            "no field inspection is required",
            response.lower(),
        )

        self.assertIn(
            "field inspection is required",
            response.lower(),
        )

    def test_explicit_negative_prose_can_remain_false(
        self,
    ):
        response, required = (
            reconcile_field_inspection_requirement(
                (
                    "No field inspection is required "
                    "because the requested fact is "
                    "already recorded."
                ),
                False,
            )
        )

        self.assertFalse(
            required
        )

        self.assertIn(
            "no field inspection is required",
            response.lower(),
        )


class WorkOrderRouteReadinessTests(
    unittest.TestCase
):
    @staticmethod
    def _base_context(
        **overrides,
    ):
        context = {
            "reportAssessmentExists":
                True,

            "maintenanceReviewCompleted":
                True,

            "maintenanceReviewDecision":
                "INTERNAL",

            "maintenanceRequestReady":
                True,

            "routeType":
                "INTERNAL",

            "workOrderStatus":
                "PENDING_ASSIGNMENT",

            "procurementHandoffStatus":
                None,

            "procurementOutcomeExists":
                False,

            "procurementReferenceNo":
                None,

            "executionType":
                "INTERNAL",

            "assignedPartyName":
                None,

            "responsibleLeadUserId":
                None,

            "responsibleLeadName":
                None,

            "responsibleLeadContact":
                None,

            "responsibleLeadEmail":
                None,

            "assignedAt":
                None,

            "requiredService":
                "Corrective facility maintenance",

            "requestSafetyRequirements":
                None,

            "workOrderSafetyRequirements":
                None,

            "plannedStartAt":
                None,

            "deadline":
                None,
        }

        context.update(
            overrides
        )

        return context

    def test_internal_pending_assignment_does_not_require_procurement(
        self,
    ):
        result = (
            check_work_order_readiness(
                self._base_context()
            )
        )

        self.assertTrue(
            result.ready
        )

        self.assertEqual(
            [],
            result.blocking_errors,
        )

        self.assertTrue(
            any(
                "PENDING_ASSIGNMENT"
                in item

                for item
                in result.information
            )
        )

    def test_internal_route_rejects_procurement_outcome(
        self,
    ):
        result = (
            check_work_order_readiness(
                self._base_context(
                    procurementOutcomeExists=True,
                )
            )
        )

        self.assertFalse(
            result.ready
        )

        self.assertTrue(
            any(
                (
                    "must not reference a "
                    "Procurement Outcome"
                )
                in item

                for item
                in result.blocking_errors
            )
        )

    def test_procurement_route_requires_completed_handoff_and_outcome(
        self,
    ):
        result = (
            check_work_order_readiness(
                self._base_context(
                    routeType="PROCUREMENT",

                    maintenanceReviewDecision=(
                        "PROCUREMENT"
                    ),

                    executionType=(
                        "CONTRACTOR"
                    ),

                    procurementHandoffStatus=(
                        "IN_PROCESS"
                    ),

                    procurementOutcomeExists=(
                        False
                    ),
                )
            )
        )

        self.assertFalse(
            result.ready
        )

        self.assertTrue(
            any(
                (
                    "Procurement handoff "
                    "is not completed"
                )
                in item

                for item
                in result.blocking_errors
            )
        )

        self.assertTrue(
            any(
                (
                    "Procurement Outcome "
                    "is missing"
                )
                in item

                for item
                in result.blocking_errors
            )
        )

    def test_procurement_pending_assignment_is_ready_after_completed_outcome(
        self,
    ):
        result = (
            check_work_order_readiness(
                self._base_context(
                    routeType="PROCUREMENT",

                    maintenanceReviewDecision=(
                        "PROCUREMENT"
                    ),

                    executionType=(
                        "CONTRACTOR"
                    ),

                    procurementHandoffStatus=(
                        "COMPLETED"
                    ),

                    procurementOutcomeExists=(
                        True
                    ),

                    procurementReferenceNo=(
                        "UC-PROC-TEST-001"
                    ),
                )
            )
        )

        self.assertTrue(
            result.ready
        )

        self.assertEqual(
            [],
            result.blocking_errors,
        )

    def test_assigned_work_order_requires_assignment_and_assigned_at(
        self,
    ):
        missing = (
            check_work_order_readiness(
                self._base_context(
                    workOrderStatus=(
                        "ASSIGNED"
                    )
                )
            )
        )

        self.assertFalse(
            missing.ready
        )

        assigned_without_timestamp = (
            check_work_order_readiness(
                self._base_context(
                    workOrderStatus=(
                        "ASSIGNED"
                    ),

                    assignedPartyName=(
                        "Building Maintenance"
                    ),

                    responsibleLeadName=(
                        "Maintenance Lead"
                    ),
                )
            )
        )

        self.assertFalse(
            assigned_without_timestamp.ready
        )

        self.assertTrue(
            any(
                "AssignedAt is required"
                in item

                for item
                in (
                    assigned_without_timestamp
                    .blocking_errors
                )
            )
        )


if __name__ == "__main__":
    unittest.main()