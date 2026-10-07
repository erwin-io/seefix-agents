from __future__ import annotations

from uuid import UUID

from ..repositories.procurement import ProcurementRepository
from ..schemas import ProcurementClarificationDraft
from .consistency import reconcile_field_inspection_requirement

CLARIFICATION_PROMPT_VERSION = "procurement-clarification-v2-consistent-field-inspection"


class ProcurementClarificationService:
    def __init__(self, *, repository: ProcurementRepository, provider) -> None:
        self.repository = repository
        self.provider = provider

    def generate_draft(self, *, handoff_id: UUID, clarification_id: UUID) -> ProcurementClarificationDraft:
        context = self.repository.get_clarification_context(
            handoff_id=handoff_id,
            clarification_id=clarification_id,
        )
        mode = "qwen"
        try:
            model = self.provider.draft_procurement_clarification(context)
            response, field_required = reconcile_field_inspection_requirement(
                model.response_draft,
                model.field_inspection_required,
            )
            uncertainties = model.uncertainties
            model_id = self.provider.model_id
        except Exception as exc:
            mode = "deterministic_fallback"
            model_id = None
            mr = context["maintenanceRequest"]
            response = (
                f"Recorded SEEFIX scope: {mr['scopeOfWork']} "
                f"Required service: {mr['requiredService']} "
                "The available record does not establish facts beyond the submitted image assessment and approved request. "
                "PPO Head should confirm any scope detail that requires field inspection before sending a final response."
            )
            uncertainties = ["The clarification could not be fully answered from recorded SEEFIX facts alone."]
            field_required = True
            print(f"[PROCUREMENT CLARIFICATION] Qwen draft failed; fallback used: {str(exc)[:500]}", flush=True)

        response, field_required = reconcile_field_inspection_requirement(response, field_required)

        result = ProcurementClarificationDraft(
            clarification_id=clarification_id,
            response_draft=response,
            uncertainties=uncertainties,
            field_inspection_required=field_required,
            generation_mode=mode,
            model_id=model_id,
            prompt_version=CLARIFICATION_PROMPT_VERSION,
        )
        self.repository.save_clarification_draft(
            clarification_id=clarification_id,
            response_draft=result.response_draft,
            draft_json=result.model_dump(mode="json"),
        )
        return result
