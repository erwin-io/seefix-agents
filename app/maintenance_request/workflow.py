from __future__ import annotations

from uuid import UUID

from ..repositories.maintenance_requests import MaintenanceRequestRepository
from ..schemas import AssessmentDatabaseContext, InspectionResult
from .generator import MaintenanceRequestGenerator


class MaintenanceRequestWorkflow:
    def __init__(self, *, generator: MaintenanceRequestGenerator, repository: MaintenanceRequestRepository) -> None:
        self.generator = generator
        self.repository = repository

    def generate_for_report(
        self,
        *,
        report_id: UUID,
        result: InspectionResult,
        context: AssessmentDatabaseContext,
    ) -> dict:
        if result.assessment is None:
            raise ValueError("No-assessment reports do not produce Maintenance Request drafts.")
        draft = self.generator.generate(result=result, context=context)
        return self.repository.save_draft(report_id=report_id, draft=draft)
