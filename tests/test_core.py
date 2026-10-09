from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.request import Request
from uuid import UUID

from pydantic import ValidationError

from app.inspection.policy import apply_urgency_policy
from app.maintenance_request.generator import MaintenanceRequestGenerator
from app.remote_image import (
    RemoteImageError,
    _SafeRedirectHandler,
    _validate_url,
)
from app.schemas import (
    AgentAssessment,
    AnalysisCertainty,
    AnalysisStatus,
    AssessmentDatabaseContext,
    CategoryReference,
    FacilityCategory,
    InspectionResult,
    ModelInspectionResponse,
    RiskFlags,
    ScopeDecision,
    ScopeValidation,
    Urgency,
)
from app.scoring import PriorityContext, calculate_priority
from app.work_order.readiness import check_work_order_readiness
from app.work_order.variance import calculate_work_order_variance


CATEGORY_ID = UUID(
    "11111111-1111-1111-1111-111111111111"
)


def assessment(
    category: FacilityCategory,
    urgency: Urgency,
    *,
    flags: RiskFlags | None = None,
    needs_review: bool = False,
) -> AgentAssessment:
    return AgentAssessment(
        category=category,
        summary=(
            "Visible facility maintenance condition "
            "requires review."
        ),
        observed_evidence=[
            "A visible maintenance condition is present."
        ],
        possible_causes=[],
        safety_indicators=[],
        risk_flags=flags or RiskFlags(),
        recommended_urgency=urgency,
        urgency_reasons=[
            "Initial visible-condition assessment."
        ],
        estimated_min_hours=1,
        estimated_max_hours=4,
        duration_assumptions=[],
        analysis_certainty=AnalysisCertainty.MEDIUM,
        needs_review=needs_review,
        follow_up_questions=[],
        limitations=[],
    )


def category_ref(
    category: FacilityCategory,
    default_urgency: Urgency | None,
    *,
    requires_maintenance_review: bool = False,
) -> CategoryReference:
    return CategoryReference(
        id=CATEGORY_ID,
        code="TEST",
        name=category,
        description="Test category reference.",
        default_urgency=default_urgency,
        urgency_guidance=(
            "Use deterministic urgency policy."
        ),
        default_min_hours=1,
        default_max_hours=4,
        default_required_service=(
            "Qualified corrective maintenance"
        ),
        default_required_capability=(
            "Qualified maintenance capability"
        ),
        safety_guidance=(
            "Verify field conditions before "
            "corrective work."
        ),
        preferred_trade="Maintenance",
        requires_maintenance_review=(
            requires_maintenance_review
        ),
    )


def assessed_result(
    category: FacilityCategory,
    urgency: Urgency,
) -> InspectionResult:
    raw = assessment(
        category,
        urgency,
    )

    updated, policy = apply_urgency_policy(
        raw
    )

    priority = calculate_priority(
        updated
    )

    return InspectionResult(
        analysis_status=AnalysisStatus.ASSESSED,
        scope_validation=ScopeValidation(
            decision=ScopeDecision.FACILITY_ISSUE,
            should_analyze=True,
            reason=(
                "A visible facility issue is present."
            ),
            detected_subjects=[
                "facility"
            ],
        ),
        assessment=updated,
        priority=priority,
        policy=policy,
        provider="test",
        model_id="test-model",
        prompt_version="test-prompt",
        original_width=100,
        original_height=100,
        processed_width=100,
        processed_height=100,
        processing_time_ms=1,
    )


def context_for(
    category: FacilityCategory,
    urgency: Urgency,
) -> AssessmentDatabaseContext:
    return AssessmentDatabaseContext(
        recurrence_count=0,
        is_recurring=False,
        verification_count=0,
        age_days=0,
        recurrence_threshold=2,
        verification_threshold=2,
        category_reference=category_ref(
            category,
            urgency,
        ),
        skills=[],
        materials=[],
        historical_duration=None,
        historical_manpower=None,
        historical_materials=[],
        duplicate_candidates=[],
    )


class AdditionalContractTests(
    unittest.TestCase
):
    def test_agent_code_does_not_write_human_authority_fields(
        self,
    ):
        app_root = (
            Path(__file__)
            .resolve()
            .parents[1]
            / "app"
        )

        production = "\n".join(
            path.read_text(
                encoding="utf-8"
            )
            for path
            in app_root.rglob("*.py")
        )

        forbidden_mutations = (
            (
                'INSERT INTO '
                '"dbo"."MaintenanceReviews"'
            ),
            (
                'UPDATE '
                '"dbo"."MaintenanceReviews"'
            ),
            'SET "AssignedBy"=',
            'SET "CompletedBy"=',
        )

        for token in forbidden_mutations:
            self.assertNotIn(
                token,
                production,
            )

        self.assertNotIn(
            "PPO_HEAD",
            production,
        )

        self.assertNotIn(
            "PPO_STAFF",
            production,
        )

    def test_completed_history_uses_completed_status(
        self,
    ):
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            / "app"
            / "repositories"
            / "reports.py"
        ).read_text(
            encoding="utf-8"
        )

        self.assertIn(
            'wo."Status" = \'COMPLETED\'',
            source,
        )

    def test_recurrence_query_uses_final_category(
        self,
    ):
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            / "app"
            / "repositories"
            / "reports.py"
        ).read_text(
            encoding="utf-8"
        )

        self.assertIn(
            (
                'COALESCE('
                'r."FinalCategory", '
                'r."AiCategory"'
                ')'
            ),
            source,
        )

    def test_redirect_target_revalidation_rejects_unapproved_host(
        self,
    ):
        handler = _SafeRedirectHandler(
            (
                "res.cloudinary.com",
            ),
            3,
        )

        request = Request(
            (
                "https://"
                "res.cloudinary.com/"
                "demo/image.jpg"
            )
        )

        with self.assertRaises(
            RemoteImageError
        ):
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                (
                    "https://"
                    "example.com/"
                    "redirected.jpg"
                ),
            )


class MaintenanceFallbackTests(
    unittest.TestCase
):
    def test_deterministic_fallback_produces_draft(
        self,
    ):
        provider = Mock()

        provider.model_id = (
            "test-model"
        )

        provider.draft_maintenance_request.side_effect = (
            RuntimeError(
                "simulated model failure"
            )
        )

        generator = (
            MaintenanceRequestGenerator(
                provider
            )
        )

        result = assessed_result(
            FacilityCategory.PLUMBING_WATER,
            Urgency.MEDIUM,
        )

        context = context_for(
            FacilityCategory.PLUMBING_WATER,
            Urgency.MEDIUM,
        )

        draft = generator.generate(
            result=result,
            context=context,
        )

        self.assertEqual(
            "deterministic_fallback",
            draft.generation_mode,
        )

        self.assertEqual(
            FacilityCategory.PLUMBING_WATER,
            draft.effective_category,
        )

        self.assertEqual(
            Urgency.MEDIUM,
            draft.effective_urgency,
        )

        self.assertGreaterEqual(
            (
                draft
                .estimated_manpower_min
                or 0
            ),
            1,
        )

        self.assertGreaterEqual(
            (
                draft
                .estimated_manpower_max
                or 0
            ),
            (
                draft
                .estimated_manpower_min
                or 0
            ),
        )

        self.assertTrue(
            draft.required_service
        )

        self.assertTrue(
            draft.scope_of_work
        )


class PolicyTests(
    unittest.TestCase
):
    def test_access_medium_high(
        self,
    ):
        updated, _ = apply_urgency_policy(
            assessment(
                FacilityCategory.ACCESS_SAFETY,
                Urgency.MEDIUM,
            )
        )

        self.assertEqual(
            Urgency.HIGH,
            updated.recommended_urgency,
        )

    def test_critical_never_downgraded(
        self,
    ):
        updated, _ = apply_urgency_policy(
            assessment(
                FacilityCategory.STRUCTURAL_SURFACE,
                Urgency.CRITICAL,
            ),
            category_ref(
                FacilityCategory.STRUCTURAL_SURFACE,
                Urgency.MEDIUM,
            ),
        )

        self.assertEqual(
            Urgency.CRITICAL,
            updated.recommended_urgency,
        )

    def test_db_default_can_raise_but_not_weaken(
        self,
    ):
        raised, _ = apply_urgency_policy(
            assessment(
                FacilityCategory.STRUCTURAL_SURFACE,
                Urgency.MEDIUM,
            ),
            category_ref(
                FacilityCategory.STRUCTURAL_SURFACE,
                Urgency.HIGH,
            ),
        )

        self.assertEqual(
            Urgency.HIGH,
            raised.recommended_urgency,
        )

        preserved, _ = apply_urgency_policy(
            assessment(
                FacilityCategory.STRUCTURAL_SURFACE,
                Urgency.CRITICAL,
            ),
            category_ref(
                FacilityCategory.STRUCTURAL_SURFACE,
                Urgency.HIGH,
            ),
        )

        self.assertEqual(
            Urgency.CRITICAL,
            preserved.recommended_urgency,
        )

    def test_electrical_high_stays_high(
        self,
    ):
        updated, _ = apply_urgency_policy(
            assessment(
                FacilityCategory.ELECTRICAL_FIRE,
                Urgency.HIGH,
            )
        )

        self.assertEqual(
            Urgency.HIGH,
            updated.recommended_urgency,
        )

    def test_electrical_medium_becomes_high(
        self,
    ):
        updated, _ = apply_urgency_policy(
            assessment(
                FacilityCategory.ELECTRICAL_FIRE,
                Urgency.MEDIUM,
            )
        )

        self.assertEqual(
            Urgency.HIGH,
            updated.recommended_urgency,
        )

    def test_immediate_danger_critical(
        self,
    ):
        updated, policy = (
            apply_urgency_policy(
                assessment(
                    FacilityCategory
                    .FIXTURES_EQUIPMENT,
                    Urgency.MEDIUM,
                    flags=RiskFlags(
                        immediate_danger=True
                    ),
                )
            )
        )

        self.assertEqual(
            Urgency.CRITICAL,
            updated.recommended_urgency,
        )

        self.assertTrue(
            updated.needs_review
        )

        self.assertTrue(
            policy.needs_review
        )

    def test_other_uncertain_review(
        self,
    ):
        updated, policy = (
            apply_urgency_policy(
                assessment(
                    FacilityCategory
                    .OTHER_UNCERTAIN,
                    Urgency.LOW,
                )
            )
        )

        self.assertTrue(
            updated.needs_review
        )

        self.assertTrue(
            policy.needs_review
        )

    def test_plumbing_low_medium_and_flooding_high(
        self,
    ):
        normal, _ = (
            apply_urgency_policy(
                assessment(
                    FacilityCategory
                    .PLUMBING_WATER,
                    Urgency.LOW,
                )
            )
        )

        self.assertEqual(
            Urgency.MEDIUM,
            normal.recommended_urgency,
        )

        flooding, _ = (
            apply_urgency_policy(
                assessment(
                    FacilityCategory
                    .PLUMBING_WATER,
                    Urgency.MEDIUM,
                    flags=RiskFlags(
                        active_flooding=True
                    ),
                )
            )
        )

        self.assertEqual(
            Urgency.HIGH,
            flooding.recommended_urgency,
        )

    def test_structural_instability_high_review(
        self,
    ):
        updated, _ = (
            apply_urgency_policy(
                assessment(
                    FacilityCategory
                    .STRUCTURAL_SURFACE,
                    Urgency.MEDIUM,
                    flags=RiskFlags(
                        structural_instability_indicator=True
                    ),
                )
            )
        )

        self.assertEqual(
            Urgency.HIGH,
            updated.recommended_urgency,
        )

        self.assertTrue(
            updated.needs_review
        )


class PriorityTests(
    unittest.TestCase
):
    def test_age_boundaries_are_strict(
        self,
    ):
        item = assessment(
            FacilityCategory.FIXTURES_EQUIPMENT,
            Urgency.MEDIUM,
        )

        self.assertEqual(
            0,
            calculate_priority(
                item,
                PriorityContext(
                    age_days=3
                ),
            ).age_points,
        )

        self.assertEqual(
            10,
            calculate_priority(
                item,
                PriorityContext(
                    age_days=3.01
                ),
            ).age_points,
        )

        self.assertEqual(
            10,
            calculate_priority(
                item,
                PriorityContext(
                    age_days=7
                ),
            ).age_points,
        )

        self.assertEqual(
            20,
            calculate_priority(
                item,
                PriorityContext(
                    age_days=7.01
                ),
            ).age_points,
        )

    def test_exact_priority_points(
        self,
    ):
        item = assessment(
            FacilityCategory.ACCESS_SAFETY,
            Urgency.CRITICAL,
            flags=RiskFlags(
                public_access_exposure=True
            ),
        )

        result = calculate_priority(
            item,
            PriorityContext(
                recurrence_count=2,
                verification_count=2,
                age_days=8,
                recurrence_threshold=2,
                verification_threshold=2,
            ),
        )

        self.assertEqual(
            100,
            result.urgency_points,
        )

        self.assertEqual(
            15,
            result.recurrence_points,
        )

        self.assertEqual(
            10,
            result.verification_points,
        )

        self.assertEqual(
            20,
            result.age_points,
        )

        self.assertEqual(
            10,
            result.public_exposure_points,
        )

        self.assertEqual(
            155,
            result.total,
        )


class ScopeAndCategoryTests(
    unittest.TestCase
):
    def test_all_eight_categories_exist(
        self,
    ):
        self.assertEqual(
            {
                (
                    "Electrical and "
                    "Fire Safety"
                ),
                (
                    "Structural and "
                    "Surface Damage"
                ),
                (
                    "Plumbing and "
                    "Water Damage"
                ),
                (
                    "Access and "
                    "Safety Hazards"
                ),
                (
                    "Building Fixtures "
                    "and Equipment"
                ),
                (
                    "Sanitation and "
                    "Environmental"
                ),
                (
                    "Roads, Walkways "
                    "and Grounds"
                ),
                "Other or Uncertain",
            },
            {
                item.value
                for item
                in FacilityCategory
            },
        )

    def test_arbitrary_category_rejected(
        self,
    ):
        with self.assertRaises(
            ValidationError
        ):
            ModelInspectionResponse.model_validate(
                {
                    "scope_decision":
                        "Facility Issue",

                    "scope_reason":
                        (
                            "A visible issue "
                            "exists in the facility."
                        ),

                    "detected_subjects":
                        [
                            "wall"
                        ],

                    "assessment":
                        {
                            "category":
                                (
                                    "Random Unsupported "
                                    "Category"
                                ),

                            "summary":
                                (
                                    "Visible maintenance "
                                    "issue is present."
                                ),

                            "observed_evidence":
                                [
                                    "Damage is visible."
                                ],

                            "possible_causes":
                                [],

                            "risk_flags":
                                {},

                            "recommended_urgency":
                                "Medium",

                            "urgency_reason":
                                (
                                    "Visible condition "
                                    "needs maintenance."
                                ),

                            "estimated_min_hours":
                                1,

                            "estimated_max_hours":
                                2,

                            "analysis_certainty":
                                "Medium",

                            "needs_review":
                                False,

                            "limitation":
                                None,
                        },
                }
            )

    def test_scope_contract(
        self,
    ):
        with self.assertRaises(
            ValidationError
        ):
            ModelInspectionResponse(
                scope_decision=(
                    ScopeDecision.OUT_OF_SCOPE
                ),

                scope_reason=(
                    "The image is unrelated "
                    "to a facility issue."
                ),

                detected_subjects=[
                    "object"
                ],

                assessment={
                    "category":
                        FacilityCategory
                        .OTHER_UNCERTAIN,

                    "summary":
                        (
                            "This should not be "
                            "allowed for out of scope."
                        ),

                    "observed_evidence":
                        [
                            "Unrelated object."
                        ],

                    "possible_causes":
                        [],

                    "risk_flags":
                        {},

                    "recommended_urgency":
                        Urgency.LOW,

                    "urgency_reason":
                        "Not applicable.",

                    "estimated_min_hours":
                        1,

                    "estimated_max_hours":
                        1,

                    "analysis_certainty":
                        AnalysisCertainty.LOW,

                    "needs_review":
                        False,

                    "limitation":
                        None,
                },
            )


class SecurityTests(
    unittest.TestCase
):
    def test_approved_subdomain_allowed(
        self,
    ):
        _validate_url(
            (
                "https://"
                "assets.res.cloudinary.com/"
                "demo/image.jpg"
            ),
            (
                "res.cloudinary.com",
            ),
        )

    def test_credentials_rejected(
        self,
    ):
        with self.assertRaises(
            RemoteImageError
        ):
            _validate_url(
                (
                    "https://"
                    "user:password@"
                    "res.cloudinary.com/"
                    "demo/image.jpg"
                ),
                (
                    "res.cloudinary.com",
                ),
            )

    def test_http_rejected(
        self,
    ):
        with self.assertRaises(
            RemoteImageError
        ):
            _validate_url(
                (
                    "http://"
                    "res.cloudinary.com/"
                    "demo/image.jpg"
                ),
                (
                    "res.cloudinary.com",
                ),
            )

    def test_unapproved_host_rejected(
        self,
    ):
        with self.assertRaises(
            RemoteImageError
        ):
            _validate_url(
                (
                    "https://"
                    "example.com/"
                    "image.jpg"
                ),
                (
                    "res.cloudinary.com",
                ),
            )


class WorkOrderTests(
    unittest.TestCase
):
    def test_readiness_missing_required_value_blocks(
        self,
    ):
        result = (
            check_work_order_readiness(
                {
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

                    "procurementOutcomeExists":
                        False,

                    "executionType":
                        "INTERNAL",

                    "requiredService":
                        None,

                    "requestSafetyRequirements":
                        None,

                    "workOrderSafetyRequirements":
                        None,
                }
            )
        )

        self.assertFalse(
            result.ready
        )

        self.assertTrue(
            any(
                (
                    "Required service "
                    "is missing"
                )
                in item
                for item
                in result.blocking_errors
            )
        )

    def test_variance_is_warning_not_rejection(
        self,
    ):
        result = (
            calculate_work_order_variance(
                {
                    "estimatedDurationDaysMin":
                        1,

                    "estimatedDurationDaysMax":
                        2,

                    "plannedDurationDays":
                        5,

                    "estimatedManpowerMin":
                        1,

                    "estimatedManpowerMax":
                        2,

                    "plannedCrewSize":
                        2,

                    "estimatedLaborHoursMin":
                        8,

                    "estimatedLaborHoursMax":
                        16,

                    "plannedLaborHours":
                        12,
                }
            )
        )

        self.assertTrue(
            result.has_warnings
        )

        self.assertEqual(
            "WARNING",
            result.items[0].severity,
        )

        self.assertEqual(
            3,
            len(result.items),
        )


if __name__ == "__main__":
    unittest.main()