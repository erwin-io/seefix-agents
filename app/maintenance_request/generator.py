from __future__ import annotations

import math

from ..providers.base import ProviderError
from ..schemas import AssessmentDatabaseContext, InspectionResult, MaintenanceRequestDraftResult
from .prompts import MAINTENANCE_REQUEST_PROMPT_VERSION


class MaintenanceRequestGenerator:
    def __init__(self, provider) -> None:
        self.provider = provider

    def generate(
        self,
        *,
        result: InspectionResult,
        context: AssessmentDatabaseContext,
    ) -> MaintenanceRequestDraftResult:
        if result.assessment is None or result.policy is None:
            raise ValueError("Maintenance Request generation requires an assessed facility issue.")

        assessment = result.assessment
        category_ref = context.category_reference
        bounded_context = self._bounded_context(result, context)

        generation_mode = "qwen"
        model_draft = None
        try:
            model_draft = self.provider.draft_maintenance_request(bounded_context)
        except Exception as exc:
            generation_mode = "deterministic_fallback"
            # This second-pass failure must never discard a valid inspection.
            print(f"[MAINTENANCE REQUEST] Qwen draft failed; using deterministic fallback: {str(exc)[:500]}", flush=True)

        if model_draft is not None:
            manpower_min = model_draft.estimated_manpower_min
            manpower_max = model_draft.estimated_manpower_max
            duration_min = model_draft.estimated_duration_days_min
            duration_max = model_draft.estimated_duration_days_max
            required_service = model_draft.required_service
            required_capability = model_draft.required_capability
            scope_of_work = model_draft.scope_of_work
            safety_requirements = model_draft.safety_requirements or category_ref.safety_guidance
            additional_materials = model_draft.additional_preliminary_materials
        else:
            manpower_min, manpower_max = self._fallback_manpower(context)
            duration_min, duration_max = self._fallback_duration_days(
                assessment.estimated_min_hours,
                assessment.estimated_max_hours,
                context,
            )
            required_service = category_ref.default_required_service or (
                f"Inspection and corrective maintenance for the reported {assessment.category.value.lower()} condition."
            )
            required_capability = category_ref.default_required_capability or (
                f"Qualified facility maintenance capability appropriate for {assessment.category.value.lower()}, "
                "including safe inspection and corrective work."
            )
            scope_of_work = (
                f"Inspect and verify the reported visible condition: {assessment.summary} "
                "Make the area safe as required, perform corrective maintenance within the Maintenance Office-reviewed scope, "
                "and document the completed work and materials actually used."
            )
            safety_requirements = category_ref.safety_guidance or self._fallback_safety(result)
            additional_materials = []

        material_names = [m.name for m in context.materials]
        material_notes_parts: list[str] = []
        if material_names:
            material_notes_parts.append("Category references: " + ", ".join(material_names[:12]) + ".")
        if additional_materials:
            material_notes_parts.append(
                "Additional cautious Agent suggestions: " + ", ".join(additional_materials[:8]) + "."
            )
        material_notes_parts.append(
            "Final material specification and quantity remain subject to qualified field inspection and Maintenance Office/Procurement confirmation."
        )
        preliminary_materials_notes = " ".join(material_notes_parts)

        knowledge_snapshot = {
            "policyVersion": result.policy.policy_version,
            "categoryReference": {
                "id": str(category_ref.id),
                "code": category_ref.code,
                "name": category_ref.name.value,
                "defaultUrgency": category_ref.default_urgency.value if category_ref.default_urgency else None,
                "defaultMinHours": category_ref.default_min_hours,
                "defaultMaxHours": category_ref.default_max_hours,
                "preferredTrade": category_ref.preferred_trade,
            },
            "recurrenceCount": context.recurrence_count,
            "verificationCount": context.verification_count,
            "historicalDuration": context.historical_duration.model_dump(mode="json") if context.historical_duration else None,
            "historicalManpower": context.historical_manpower.model_dump(mode="json") if context.historical_manpower else None,
            "skills": [item.model_dump(mode="json") for item in context.skills[:12]],
            "materials": [item.model_dump(mode="json") for item in context.materials[:12]],
            "historicalMaterials": [item.model_dump(mode="json") for item in context.historical_materials[:12]],
        }

        recommendation = {
            "generationMode": generation_mode,
            "sourceAssessmentPromptVersion": result.prompt_version,
            "maintenanceRequestPromptVersion": MAINTENANCE_REQUEST_PROMPT_VERSION,
            "rawUrgency": result.policy.raw_urgency.value,
            "effectiveUrgency": result.policy.effective_urgency.value,
            "policyReason": result.policy.reason,
            "visibleEvidence": assessment.observed_evidence[:8],
            "riskFlags": assessment.risk_flags.model_dump(mode="json"),
        }

        return MaintenanceRequestDraftResult(
            effective_category=assessment.category,
            effective_urgency=result.policy.effective_urgency,
            required_service=required_service,
            required_capability=required_capability,
            scope_of_work=scope_of_work,
            safety_requirements=safety_requirements,
            preliminary_materials_notes=preliminary_materials_notes,
            estimated_labor_hours_min=assessment.estimated_min_hours,
            estimated_labor_hours_max=assessment.estimated_max_hours,
            estimated_manpower_min=manpower_min,
            estimated_manpower_max=manpower_max,
            estimated_duration_days_min=duration_min,
            estimated_duration_days_max=duration_max,
            generation_mode=generation_mode,
            model_id=self.provider.model_id if generation_mode == "qwen" else None,
            prompt_version=MAINTENANCE_REQUEST_PROMPT_VERSION,
            skills=context.skills,
            materials=context.materials,
            historical_materials=context.historical_materials,
            additional_materials=additional_materials,
            knowledge_snapshot=knowledge_snapshot,
            recommendation=recommendation,
        )

    @staticmethod
    def _bounded_context(result: InspectionResult, context: AssessmentDatabaseContext) -> dict:
        assessment = result.assessment
        assert assessment is not None and result.policy is not None
        return {
            "assessment": {
                "category": assessment.category.value,
                "summary": assessment.summary,
                "observedEvidence": assessment.observed_evidence[:8],
                "riskFlags": assessment.risk_flags.model_dump(mode="json"),
                "estimatedMinHours": assessment.estimated_min_hours,
                "estimatedMaxHours": assessment.estimated_max_hours,
                "effectiveUrgency": result.policy.effective_urgency.value,
            },
            "categoryReference": context.category_reference.model_dump(mode="json"),
            "requiredSkills": [item.model_dump(mode="json") for item in context.skills[:12]],
            "categoryMaterials": [item.model_dump(mode="json") for item in context.materials[:12]],
            "historicalDuration": context.historical_duration.model_dump(mode="json") if context.historical_duration else None,
            "historicalManpower": context.historical_manpower.model_dump(mode="json") if context.historical_manpower else None,
            "historicalMaterials": [item.model_dump(mode="json") for item in context.historical_materials[:10]],
            "recurrenceCount": context.recurrence_count,
            "verificationCount": context.verification_count,
        }

    @staticmethod
    def _fallback_manpower(context: AssessmentDatabaseContext) -> tuple[int, int]:
        if context.historical_manpower:
            median = max(1, round(context.historical_manpower.median_actual_crew_size))
            return max(1, median - 1), max(median, median + 1)
        return 1, 2

    @staticmethod
    def _fallback_duration_days(
        min_hours: float,
        max_hours: float,
        context: AssessmentDatabaseContext,
    ) -> tuple[float, float]:
        if context.historical_duration and context.historical_duration.sample_count >= 2:
            historical_days = max(0.25, context.historical_duration.median_hours / 8.0)
            min_days = min(max(0.25, min_hours / 8.0), historical_days)
            max_days = max(max_hours / 8.0, historical_days)
        else:
            min_days = max(0.25, min_hours / 8.0)
            max_days = max(min_days, max_hours / 8.0)
        return round(min_days, 2), round(max_days, 2)

    @staticmethod
    def _fallback_safety(result: InspectionResult) -> str:
        assessment = result.assessment
        assert assessment is not None
        flags = assessment.risk_flags
        rules = ["Use qualified personnel and verify field conditions before corrective work."]
        if flags.electrical_exposure or flags.fire_or_smoke_indicator:
            rules.append("Isolate electrical/fire hazards using approved site procedures before contact or repair.")
        if flags.structural_instability_indicator:
            rules.append("Restrict access and obtain qualified structural assessment before work that could affect stability.")
        if flags.active_flooding:
            rules.append("Control water source and prevent electrical contact with wet areas before repair.")
        if flags.blocked_access_or_exit:
            rules.append("Maintain safe access/egress and establish temporary controls while correcting the obstruction.")
        return " ".join(rules)
