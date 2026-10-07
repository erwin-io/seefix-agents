from __future__ import annotations

import unittest
from uuid import uuid4

from pydantic import ValidationError

from app.inspection.policy import apply_urgency_policy
from app.maintenance_request.generator import MaintenanceRequestGenerator
from app.remote_image import RemoteImageError, _validate_url
from app.schemas import (
    AgentAssessment,
    AnalysisCertainty,
    AnalysisStatus,
    AssessmentDatabaseContext,
    CategoryReference,
    FacilityCategory,
    InspectionResult,
    MaterialReference,
    PolicyResult,
    PriorityBreakdown,
    RiskFlags,
    ScopeDecision,
    ScopeValidation,
    SkillReference,
    Urgency,
)
from app.scoring import PriorityContext, calculate_priority
from app.work_order.readiness import check_work_order_readiness
from app.work_order.variance import calculate_work_order_variance


def assessment(
    category=FacilityCategory.FIXTURES_EQUIPMENT,
    urgency=Urgency.LOW,
    flags: RiskFlags | None = None,
    needs_review=False,
) -> AgentAssessment:
    return AgentAssessment(
        category=category,
        summary="Visible maintenance issue requires preliminary review.",
        observed_evidence=["Visible damaged component."],
        possible_causes=[],
        safety_indicators=[],
        risk_flags=flags or RiskFlags(),
        recommended_urgency=urgency,
        urgency_reasons=["Model preliminary recommendation."],
        estimated_min_hours=2,
        estimated_max_hours=8,
        duration_assumptions=[],
        analysis_certainty=AnalysisCertainty.MEDIUM,
        needs_review=needs_review,
        follow_up_questions=[],
        limitations=[],
    )


def category_ref(category: FacilityCategory, default: Urgency | None = None, review=False):
    return CategoryReference(
        id=uuid4(),
        code="TEST",
        name=category,
        default_urgency=default,
        requires_ppo_review=review,
    )


class ScopeAndCategoryTests(unittest.TestCase):
    def test_all_eight_categories_exist(self):
        self.assertEqual(len(FacilityCategory), 8)

    def test_arbitrary_category_rejected(self):
        with self.assertRaises(ValueError):
            FacilityCategory("Random New Category")

    def test_scope_contract(self):
        valid = ScopeValidation(
            decision=ScopeDecision.FACILITY_ISSUE,
            should_analyze=True,
            reason="Visible defect is present.",
        )
        self.assertTrue(valid.should_analyze)
        with self.assertRaises(ValidationError):
            ScopeValidation(
                decision=ScopeDecision.OUT_OF_SCOPE,
                should_analyze=True,
                reason="Not a facility issue.",
            )


class PolicyTests(unittest.TestCase):
    def test_electrical_medium_becomes_high(self):
        updated, policy = apply_urgency_policy(
            assessment(FacilityCategory.ELECTRICAL_FIRE, Urgency.MEDIUM)
        )
        self.assertEqual(updated.recommended_urgency, Urgency.HIGH)
        self.assertEqual(policy.raw_urgency, Urgency.MEDIUM)

    def test_electrical_high_stays_high(self):
        updated, _ = apply_urgency_policy(
            assessment(FacilityCategory.ELECTRICAL_FIRE, Urgency.HIGH)
        )
        self.assertEqual(updated.recommended_urgency, Urgency.HIGH)

    def test_critical_never_downgraded(self):
        updated, _ = apply_urgency_policy(
            assessment(FacilityCategory.STRUCTURAL_SURFACE, Urgency.CRITICAL)
        )
        self.assertEqual(updated.recommended_urgency, Urgency.CRITICAL)

    def test_immediate_danger_critical(self):
        updated, policy = apply_urgency_policy(
            assessment(
                FacilityCategory.ELECTRICAL_FIRE,
                Urgency.HIGH,
                RiskFlags(immediate_danger=True),
            )
        )
        self.assertEqual(updated.recommended_urgency, Urgency.CRITICAL)
        self.assertTrue(policy.needs_review)

    def test_structural_instability_high_review(self):
        updated, policy = apply_urgency_policy(
            assessment(
                FacilityCategory.STRUCTURAL_SURFACE,
                Urgency.MEDIUM,
                RiskFlags(structural_instability_indicator=True),
            )
        )
        self.assertEqual(updated.recommended_urgency, Urgency.HIGH)
        self.assertTrue(policy.needs_review)

    def test_plumbing_low_medium_and_flooding_high(self):
        updated, _ = apply_urgency_policy(
            assessment(FacilityCategory.PLUMBING_WATER, Urgency.LOW)
        )
        self.assertEqual(updated.recommended_urgency, Urgency.MEDIUM)
        flooded, _ = apply_urgency_policy(
            assessment(
                FacilityCategory.PLUMBING_WATER,
                Urgency.LOW,
                RiskFlags(active_flooding=True),
            )
        )
        self.assertEqual(flooded.recommended_urgency, Urgency.HIGH)

    def test_access_medium_high(self):
        updated, _ = apply_urgency_policy(
            assessment(FacilityCategory.ACCESS_SAFETY, Urgency.MEDIUM)
        )
        self.assertEqual(updated.recommended_urgency, Urgency.HIGH)

    def test_other_uncertain_review(self):
        updated, policy = apply_urgency_policy(
            assessment(FacilityCategory.OTHER_UNCERTAIN, Urgency.LOW)
        )
        self.assertEqual(updated.recommended_urgency, Urgency.LOW)
        self.assertTrue(policy.needs_review)

    def test_db_default_can_raise_but_not_weaken(self):
        a = assessment(FacilityCategory.STRUCTURAL_SURFACE, Urgency.MEDIUM)
        updated, _ = apply_urgency_policy(
            a,
            category_ref(FacilityCategory.STRUCTURAL_SURFACE, Urgency.HIGH),
        )
        self.assertEqual(updated.recommended_urgency, Urgency.HIGH)
        critical = assessment(FacilityCategory.STRUCTURAL_SURFACE, Urgency.CRITICAL)
        updated, _ = apply_urgency_policy(
            critical,
            category_ref(FacilityCategory.STRUCTURAL_SURFACE, Urgency.LOW),
        )
        self.assertEqual(updated.recommended_urgency, Urgency.CRITICAL)


class PriorityTests(unittest.TestCase):
    def test_exact_priority_points(self):
        a = assessment(
            FacilityCategory.SANITATION_ENVIRONMENTAL,
            Urgency.HIGH,
            RiskFlags(public_access_exposure=True),
        )
        result = calculate_priority(
            a,
            PriorityContext(
                recurrence_count=2,
                verification_count=2,
                age_days=8,
                recurrence_threshold=2,
                verification_threshold=2,
            ),
        )
        self.assertEqual(result.urgency_points, 75)
        self.assertEqual(result.recurrence_points, 15)
        self.assertEqual(result.verification_points, 10)
        self.assertEqual(result.age_points, 20)
        self.assertEqual(result.public_exposure_points, 10)
        self.assertEqual(result.total, 130)

    def test_age_boundaries_are_strict(self):
        a = assessment(urgency=Urgency.LOW)
        self.assertEqual(calculate_priority(a, PriorityContext(age_days=3)).age_points, 0)
        self.assertEqual(calculate_priority(a, PriorityContext(age_days=3.01)).age_points, 10)
        self.assertEqual(calculate_priority(a, PriorityContext(age_days=7)).age_points, 10)
        self.assertEqual(calculate_priority(a, PriorityContext(age_days=7.01)).age_points, 20)


class SecurityTests(unittest.TestCase):
    def test_http_rejected(self):
        with self.assertRaises(RemoteImageError):
            _validate_url("http://res.cloudinary.com/demo/image.jpg", ("res.cloudinary.com",))

    def test_unapproved_host_rejected(self):
        with self.assertRaises(RemoteImageError):
            _validate_url("https://example.com/image.jpg", ("res.cloudinary.com",))

    def test_credentials_rejected(self):
        with self.assertRaises(RemoteImageError):
            _validate_url("https://user:pw@res.cloudinary.com/image.jpg", ("res.cloudinary.com",))

    def test_approved_subdomain_allowed(self):
        _validate_url("https://assets.res.cloudinary.com/image.jpg", ("res.cloudinary.com",))


class WorkOrderTests(unittest.TestCase):
    def base_context(self):
        return {
            "reportAssessmentExists": True,
            "maintenanceRequestAuthorized": True,
            "procurementHandoffStatus": "COMPLETED",
            "procurementOutcomeExists": True,
            "executionType": "CONTRACTOR",
            "assignedPartyName": "Example Contractor",
            "responsibleLeadName": "Lead",
            "responsibleLeadContact": "contact",
            "requiredService": "Repair",
            "plannedStartAt": "2026-09-17T08:00:00+08:00",
            "deadline": "2026-09-18T17:00:00+08:00",
            "procurementReferenceNo": "PR-1",
            "requestSafetyRequirements": "Use PPE.",
            "workOrderSafetyRequirements": "Use PPE.",
            "estimatedDurationDaysMin": 1,
            "estimatedDurationDaysMax": 2,
            "plannedDurationDays": 5,
            "estimatedManpowerMin": 1,
            "estimatedManpowerMax": 2,
            "plannedCrewSize": 4,
            "estimatedLaborHoursMin": 4,
            "estimatedLaborHoursMax": 16,
            "plannedLaborHours": 24,
        }

    def test_readiness_missing_required_value_blocks(self):
        ctx = self.base_context()
        ctx["assignedPartyName"] = None
        result = check_work_order_readiness(ctx)
        self.assertFalse(result.ready)
        self.assertTrue(result.blocking_errors)

    def test_variance_is_warning_not_rejection(self):
        result = calculate_work_order_variance(self.base_context())
        self.assertTrue(result.has_warnings)
        self.assertTrue(all(item.severity in {"INFO", "WARNING"} for item in result.items))


class MaintenanceFallbackTests(unittest.TestCase):
    class FailingProvider:
        model_id = "test-model"
        def draft_maintenance_request(self, context):
            raise RuntimeError("simulated model failure")

    def test_deterministic_fallback_produces_draft(self):
        a = assessment(FacilityCategory.PLUMBING_WATER, Urgency.MEDIUM)
        normalized, policy = apply_urgency_policy(a)
        result = InspectionResult(
            analysis_status=AnalysisStatus.ASSESSED,
            scope_validation=ScopeValidation(
                decision=ScopeDecision.FACILITY_ISSUE,
                should_analyze=True,
                reason="Visible plumbing issue is present.",
            ),
            assessment=normalized,
            priority=PriorityBreakdown(
                urgency_points=50,
                recurrence_points=0,
                verification_points=0,
                age_points=0,
                public_exposure_points=0,
                total=50,
            ),
            policy=policy,
            provider="ollama",
            model_id="test",
            prompt_version="test",
            original_width=100,
            original_height=100,
            processed_width=100,
            processed_height=100,
            processing_time_ms=1,
        )
        ref = category_ref(FacilityCategory.PLUMBING_WATER, Urgency.MEDIUM)
        context = AssessmentDatabaseContext(
            recurrence_count=0,
            is_recurring=False,
            verification_count=0,
            age_days=0,
            recurrence_threshold=2,
            verification_threshold=2,
            category_reference=ref,
            skills=[],
            materials=[],
            historical_duration=None,
            historical_manpower=None,
            historical_materials=[],
            duplicate_candidates=[],
        )
        draft = MaintenanceRequestGenerator(self.FailingProvider()).generate(
            result=result,
            context=context,
        )
        self.assertEqual(draft.generation_mode, "deterministic_fallback")
        self.assertEqual(draft.effective_category, FacilityCategory.PLUMBING_WATER)
        self.assertTrue(draft.required_service)
        self.assertTrue(draft.scope_of_work)


if __name__ == "__main__":
    unittest.main()

class AdditionalContractTests(unittest.TestCase):
    def test_redirect_target_revalidation_rejects_unapproved_host(self):
        from app.remote_image import _SafeRedirectHandler
        from urllib.request import Request
        handler = _SafeRedirectHandler(("res.cloudinary.com",), 3)
        with self.assertRaises(RemoteImageError):
            handler.redirect_request(
                Request("https://res.cloudinary.com/demo/a.jpg"),
                None,
                302,
                "Found",
                {},
                "https://example.com/redirected.jpg",
            )

    def test_recurrence_query_uses_final_category(self):
        from pathlib import Path
        source = Path("app/repositories/reports.py").read_text(encoding="utf-8")
        self.assertIn('COALESCE(r."FinalCategory", r."AiCategory") = %s', source)

    def test_completed_history_uses_completed_status(self):
        from pathlib import Path
        source = Path("app/repositories/reports.py").read_text(encoding="utf-8")
        self.assertIn('wo."Status" = \'COMPLETED\'', source)
        self.assertNotIn('wo."Status" = \'RESOLVED\'', source)

    def test_agent_code_does_not_write_human_authority_fields(self):
        from pathlib import Path
        all_source = "\n".join(
            p.read_text(encoding="utf-8")
            for p in Path("app").rglob("*.py")
        )
        for forbidden_update in (
            'SET "FinalCategory"',
            'SET "FinalUrgency"',
            'SET "AuthorizedBy"',
            'SET "ConfirmedBy"',
            'SET "CompletedBy"',
        ):
            self.assertNotIn(forbidden_update, all_source)
