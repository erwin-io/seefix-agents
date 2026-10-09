from __future__ import annotations

from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..database import Database
from ..schemas import MaintenanceRequestDraftResult


class MaintenanceRequestRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save_draft(self, *, report_id: UUID, draft: MaintenanceRequestDraftResult) -> dict:
        """Create/update exactly one DRAFT per Report; never overwrite an authorized request."""
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            # Same parent lock order as Reporter cancellation and human review.
            # Rechecking under FOR UPDATE stops a late Agent DRAFT from
            # appearing after a cancellation has committed.
            cur.execute(
                'SELECT "Status" FROM "dbo"."Reports" WHERE "Id"=%s FOR UPDATE',
                (report_id,),
            )
            report = cur.fetchone()
            if report is None:
                raise LookupError("Report was not found.")
            if report["Status"] != "PENDING_REVIEW":
                raise ValueError("Maintenance Request DRAFT is not allowed for this report state.")
            cur.execute(
                'SELECT * FROM "dbo"."MaintenanceRequests" WHERE "ReportId"=%s FOR UPDATE',
                (report_id,),
            )
            existing = cur.fetchone()
            if existing is not None and existing["Status"] != "DRAFT":
                return {
                    "Id": existing["Id"],
                    "RequestNo": existing["RequestNo"],
                    "Status": existing["Status"],
                    "updated": False,
                }

            values = (
                draft.effective_category.value,
                draft.effective_urgency.value,
                draft.required_service,
                draft.required_capability,
                draft.scope_of_work,
                draft.safety_requirements,
                draft.preliminary_materials_notes,
                draft.estimated_labor_hours_min,
                draft.estimated_labor_hours_max,
                draft.estimated_manpower_min,
                draft.estimated_manpower_max,
                draft.estimated_duration_days_min,
                draft.estimated_duration_days_max,
                draft.model_id,
                draft.prompt_version,
                Jsonb(draft.recommendation),
                Jsonb(draft.knowledge_snapshot),
            )
            if existing is None:
                cur.execute(
                    '''
                    INSERT INTO "dbo"."MaintenanceRequests"
                    ("ReportId","Status","EffectiveCategory","EffectiveUrgency",
                     "RequiredService","RequiredCapability","ScopeOfWork","SafetyRequirements",
                     "PreliminaryMaterialsNotes","EstimatedLaborHoursMin","EstimatedLaborHoursMax",
                     "EstimatedManpowerMin","EstimatedManpowerMax","EstimatedDurationDaysMin",
                     "EstimatedDurationDaysMax","AgentGenerated","AgentGeneratedAt","AgentModelId",
                     "AgentPromptVersion","AgentRecommendationJson","KnowledgeSnapshotJson")
                    VALUES (%s,'DRAFT',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,NOW(),%s,%s,%s,%s)
                    RETURNING "Id","RequestNo","Status"
                    ''',
                    (report_id,) + values,
                )
            else:
                cur.execute(
                    '''
                    UPDATE "dbo"."MaintenanceRequests" SET
                      "EffectiveCategory"=%s,"EffectiveUrgency"=%s,"RequiredService"=%s,
                      "RequiredCapability"=%s,"ScopeOfWork"=%s,"SafetyRequirements"=%s,
                      "PreliminaryMaterialsNotes"=%s,"EstimatedLaborHoursMin"=%s,"EstimatedLaborHoursMax"=%s,
                      "EstimatedManpowerMin"=%s,"EstimatedManpowerMax"=%s,
                      "EstimatedDurationDaysMin"=%s,"EstimatedDurationDaysMax"=%s,
                      "AgentGenerated"=TRUE,"AgentGeneratedAt"=NOW(),"AgentModelId"=%s,
                      "AgentPromptVersion"=%s,"AgentRecommendationJson"=%s,"KnowledgeSnapshotJson"=%s
                    WHERE "Id"=%s AND "Status"='DRAFT'
                    RETURNING "Id","RequestNo","Status"
                    ''',
                    values + (existing["Id"],),
                )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("Unable to save Maintenance Request DRAFT.")
            request_id = row["Id"]

            # Regeneration of a DRAFT replaces only Agent/category-reference children.
            cur.execute(
                'DELETE FROM "dbo"."MaintenanceRequestSkills" WHERE "MaintenanceRequestId"=%s AND "Source" IN (\'CATEGORY_REFERENCE\',\'AGENT\',\'HISTORICAL\')',
                (request_id,),
            )
            for skill in draft.skills:
                cur.execute(
                    '''
                    INSERT INTO "dbo"."MaintenanceRequestSkills"
                    ("MaintenanceRequestId","SkillId","SkillName","MinimumProficiencyLevel","IsRequired","IsLeadSkill","Source","Notes")
                    VALUES (%s,%s,%s,%s,%s,%s,'CATEGORY_REFERENCE',%s)
                    ON CONFLICT ("MaintenanceRequestId","SkillName") DO NOTHING
                    ''',
                    (
                        request_id, skill.skill_id, skill.skill_name, skill.minimum_proficiency_level,
                        skill.is_required, skill.is_lead_skill, skill.notes,
                    ),
                )

            cur.execute(
                'DELETE FROM "dbo"."MaintenanceRequestMaterials" WHERE "MaintenanceRequestId"=%s AND "Source" IN (\'CATEGORY_REFERENCE\',\'AGENT\',\'HISTORICAL\')',
                (request_id,),
            )
            known_materials: set[str] = set()
            for material in draft.materials:
                known_materials.add(material.name.strip().lower())
                cur.execute(
                    '''
                    INSERT INTO "dbo"."MaintenanceRequestMaterials"
                    ("MaintenanceRequestId","MaterialId","MaterialName","Unit","QuantityMin","QuantityMax","IsPreliminary","Source","Notes")
                    VALUES (%s,%s,%s,%s,%s,%s,TRUE,'CATEGORY_REFERENCE',%s)
                    ''',
                    (
                        request_id, material.material_id, material.name, material.unit,
                        material.default_qty_min, material.default_qty_max, material.notes,
                    ),
                )

            for material in draft.historical_materials:
                key = material.material_name.strip().lower()
                if not key or key in known_materials:
                    continue
                known_materials.add(key)
                note = (
                    f"Historical completed-work reference: {material.use_count} use(s); "
                    f"average quantity={material.average_quantity}; median quantity={material.median_quantity}. "
                    "Preliminary only; final specification/quantity requires field confirmation."
                )
                cur.execute(
                    '''
                    INSERT INTO "dbo"."MaintenanceRequestMaterials"
                    ("MaintenanceRequestId","MaterialId","MaterialName","Unit","QuantityMin","QuantityMax","IsPreliminary","Source","Notes")
                    VALUES (%s,NULL,%s,%s,NULL,NULL,TRUE,'HISTORICAL',%s)
                    ''',
                    (request_id, material.material_name, material.unit, note),
                )

            for material_name in draft.additional_materials:
                clean_name = str(material_name).strip()[:200]
                key = clean_name.lower()
                if not clean_name or key in known_materials:
                    continue
                known_materials.add(key)
                cur.execute(
                    '''
                    INSERT INTO "dbo"."MaintenanceRequestMaterials"
                    ("MaintenanceRequestId","MaterialId","MaterialName","IsPreliminary","Source","Notes")
                    VALUES (%s,NULL,%s,TRUE,'AGENT',
                            'Cautious Agent suggestion only; final specification and quantity require qualified field confirmation.')
                    ''',
                    (request_id, clean_name),
                )

            dedupe = f"maintenance_request:{request_id}:draft_ready:{draft.prompt_version}"
            cur.execute(
                '''
                INSERT INTO "dbo"."OutboxEvents"
                ("AggregateType","AggregateId","Transport","ChannelName","EventName","Payload","DeduplicationKey")
                VALUES ('MAINTENANCE_REQUEST',%s,'PUSHER',%s,'maintenance_request.draft_ready',%s,%s)
                ON CONFLICT ("DeduplicationKey") WHERE "DeduplicationKey" IS NOT NULL DO NOTHING
                ''',
                (
                    request_id, f"report-{report_id}",
                    Jsonb({"maintenanceRequestId": str(request_id), "reportId": str(report_id), "status": "DRAFT"}),
                    dedupe,
                ),
            )
            return {"Id": row["Id"], "RequestNo": row["RequestNo"], "Status": row["Status"], "updated": True}

    def get_by_report(self, report_id: UUID) -> dict | None:
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute('SELECT * FROM "dbo"."MaintenanceRequests" WHERE "ReportId"=%s', (report_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def create_revision_snapshot(
        self,
        *,
        maintenance_request_id: UUID,
        reason: str,
        snapshot: dict,
        created_by: UUID | None = None,
    ) -> UUID:
        """Repository support for future human clarification edits; Agent does not authorize."""
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                INSERT INTO "dbo"."MaintenanceRequestRevisions"
                ("MaintenanceRequestId","RevisionNo","Reason","SnapshotJson","CreatedBy")
                VALUES (%s,NULL,%s,%s,%s) RETURNING "Id"
                ''',
                (maintenance_request_id, reason, Jsonb(snapshot), created_by),
            )
            # RevisionNo is normalized by canonical DB triggers.
            return cur.fetchone()["Id"]
