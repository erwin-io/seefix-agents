from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..database import Database


class ProcurementRepository:
    def __init__(
        self,
        database: Database,
        *,
        expected_days: int,
        followup_hours: int,
    ) -> None:
        self.database = database
        self.expected_days = expected_days
        self.followup_hours = followup_hours

    def _setting_int(self, key: str, fallback: int) -> int:
        try:
            with self.database.connect() as conn, conn.cursor() as cur:
                cur.execute('SELECT "dbo"."GetSettingInt"(%s,%s)', (key, fallback))
                row = cur.fetchone()
                return max(1, int(row[0] if row else fallback))
        except Exception:
            return fallback

    def build_package_preview(self, maintenance_request_id: UUID) -> dict:
        """Build a portable Procurement package without submitting or authorizing it."""
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                SELECT mr.*, r."ReportNo", r."Description", r."Building", r."Floor", r."RoomOrArea",
                       COALESCE(r."FinalCategory", r."AiCategory") AS "ReportEffectiveCategory",
                       COALESCE(r."FinalUrgency", r."AiRecommendedUrgency") AS "ReportEffectiveUrgency"
                FROM "dbo"."MaintenanceRequests" mr
                INNER JOIN "dbo"."Reports" r ON r."Id"=mr."ReportId"
                WHERE mr."Id"=%s
                ''',
                (maintenance_request_id,),
            )
            request = cur.fetchone()
            if request is None:
                raise LookupError("Maintenance Request was not found.")
            if request["Status"] in {"DRAFT", "CANCELLED"} or request["AuthorizedBy"] is None or request["AuthorizedAt"] is None:
                raise ValueError("Procurement package preparation requires an authorized Maintenance Request.")

            cur.execute(
                '''SELECT "SkillName","MinimumProficiencyLevel","IsRequired","IsLeadSkill","Source","Notes"
                   FROM "dbo"."MaintenanceRequestSkills" WHERE "MaintenanceRequestId"=%s
                   ORDER BY "IsLeadSkill" DESC,"IsRequired" DESC,"SkillName"''',
                (maintenance_request_id,),
            )
            skills = [dict(row) for row in cur.fetchall()]
            cur.execute(
                '''SELECT "MaterialName","Unit","QuantityMin","QuantityMax","IsPreliminary","Source","Notes"
                   FROM "dbo"."MaintenanceRequestMaterials" WHERE "MaintenanceRequestId"=%s
                   ORDER BY "MaterialName"''',
                (maintenance_request_id,),
            )
            materials = [dict(row) for row in cur.fetchall()]

        now = datetime.now(timezone.utc)
        expected_days = self._setting_int("ProcurementExpectedDays", self.expected_days)
        expected_at = now + timedelta(days=expected_days)
        next_followup_at = now + timedelta(hours=self.followup_hours)

        snapshot = {
            "maintenanceRequestId": str(request["Id"]),
            "requestNo": request["RequestNo"],
            "reportId": str(request["ReportId"]),
            "reportNo": request["ReportNo"],
            "effectiveCategory": request["EffectiveCategory"],
            "effectiveUrgency": request["EffectiveUrgency"],
            "requiredService": request["RequiredService"],
            "requiredCapability": request["RequiredCapability"],
            "scopeOfWork": request["ScopeOfWork"],
            "safetyRequirements": request["SafetyRequirements"],
            "preliminaryMaterialsNotes": request["PreliminaryMaterialsNotes"],
            "estimatedLaborHoursMin": request["EstimatedLaborHoursMin"],
            "estimatedLaborHoursMax": request["EstimatedLaborHoursMax"],
            "estimatedManpowerMin": request["EstimatedManpowerMin"],
            "estimatedManpowerMax": request["EstimatedManpowerMax"],
            "estimatedDurationDaysMin": request["EstimatedDurationDaysMin"],
            "estimatedDurationDaysMax": request["EstimatedDurationDaysMax"],
            "location": {
                "building": request["Building"],
                "floor": request["Floor"],
                "roomOrArea": request["RoomOrArea"],
            },
            "skills": skills,
            "materials": materials,
            "revisionNo": request["CurrentRevisionNo"],
        }
        subject = f"SEEFIX Maintenance Request {request['RequestNo']} - {request['EffectiveCategory']}"
        email_body = (
            f"Maintenance Request {request['RequestNo']} is authorized and ready for the University's existing Procurement process.\n\n"
            f"Category: {request['EffectiveCategory']}\nUrgency: {request['EffectiveUrgency']}\n"
            f"Required service: {request['RequiredService']}\n"
            f"Scope: {request['ScopeOfWork']}\n\n"
            "SEEFIX does not perform bidding, quotation comparison, provider ranking, or award decisions. "
            "Please continue those activities in the existing Procurement process and return only the final execution outcome to SEEFIX."
        )
        return {
            "maintenanceRequestId": str(maintenance_request_id),
            "requestNo": request["RequestNo"],
            "requestSnapshot": snapshot,
            "emailSubject": subject,
            "emailMessage": email_body,
            "expectedResponseAt": expected_at.isoformat(),
            "nextFollowUpAt": next_followup_at.isoformat(),
            "operationalExpectedDays": expected_days,
        }

    def get_clarification_context(self, *, handoff_id: UUID, clarification_id: UUID) -> dict:
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                SELECT pc."Id" AS "ClarificationId", pc."Status" AS "ClarificationStatus", pc."Question",
                       ph."Id" AS "HandoffId", ph."HandoffNo", ph."Status" AS "HandoffStatus",
                       mr."Id" AS "MaintenanceRequestId", mr."RequestNo", mr."RequiredService",
                       mr."RequiredCapability", mr."ScopeOfWork", mr."SafetyRequirements",
                       mr."EffectiveCategory", mr."EffectiveUrgency",
                       r."Id" AS "ReportId", r."ReportNo", r."AiSummary", r."AssessmentJson",
                       r."FinalCategory", r."FinalUrgency"
                FROM "dbo"."ProcurementClarifications" pc
                INNER JOIN "dbo"."ProcurementHandoffs" ph ON ph."Id"=pc."ProcurementHandoffId"
                INNER JOIN "dbo"."MaintenanceRequests" mr ON mr."Id"=ph."MaintenanceRequestId"
                INNER JOIN "dbo"."Reports" r ON r."Id"=mr."ReportId"
                WHERE pc."Id"=%s AND ph."Id"=%s
                ''',
                (clarification_id, handoff_id),
            )
            row = cur.fetchone()
        if row is None:
            raise LookupError("Procurement clarification was not found for this handoff.")
        if row["ClarificationStatus"] != "OPEN":
            raise ValueError("Only OPEN Procurement clarifications can receive an AI draft.")
        assessment = row["AssessmentJson"] if isinstance(row["AssessmentJson"], dict) else None
        evidence = []
        limitations = []
        if assessment:
            inner = assessment.get("assessment") or {}
            evidence = inner.get("observed_evidence") or []
            limitations = inner.get("limitations") or []
        return {
            "clarificationId": str(row["ClarificationId"]),
            "handoffId": str(row["HandoffId"]),
            "question": row["Question"],
            "report": {
                "id": str(row["ReportId"]),
                "reportNo": row["ReportNo"],
                "summary": row["AiSummary"],
                "finalCategory": row["FinalCategory"],
                "finalUrgency": row["FinalUrgency"],
                "recordedVisibleEvidence": evidence[:8],
                "limitations": limitations[:8],
            },
            "maintenanceRequest": {
                "id": str(row["MaintenanceRequestId"]),
                "requestNo": row["RequestNo"],
                "effectiveCategory": row["EffectiveCategory"],
                "effectiveUrgency": row["EffectiveUrgency"],
                "requiredService": row["RequiredService"],
                "requiredCapability": row["RequiredCapability"],
                "scopeOfWork": row["ScopeOfWork"],
                "safetyRequirements": row["SafetyRequirements"],
            },
        }

    def save_clarification_draft(self, *, clarification_id: UUID, response_draft: str, draft_json: dict) -> None:
        with self.database.connect() as conn, conn.cursor() as cur:
            cur.execute(
                '''
                UPDATE "dbo"."ProcurementClarifications"
                SET "AiResponseDraft"=%s,"AiDraftJson"=%s
                WHERE "Id"=%s AND "Status"='OPEN'
                ''',
                (response_draft, Jsonb(draft_json), clarification_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError("Unable to save clarification draft; record is no longer OPEN.")
            dedupe = f"procurement_clarification:{clarification_id}:draft_ready"
            cur.execute(
                '''
                INSERT INTO "dbo"."OutboxEvents"
                ("AggregateType","AggregateId","Transport","EventName","Payload","DeduplicationKey")
                VALUES ('PROCUREMENT_CLARIFICATION',%s,'PUSHER','procurement.clarification.draft_ready',%s,%s)
                ON CONFLICT ("DeduplicationKey") WHERE "DeduplicationKey" IS NOT NULL DO NOTHING
                ''',
                (clarification_id, Jsonb({"clarificationId": str(clarification_id)}), dedupe),
            )

    def run_monitor(self) -> dict[str, int]:
        """Create missing deterministic action items. Re-running is idempotent for OPEN items."""
        counts: dict[str, int] = {}
        with self.database.connect() as conn, conn.cursor() as cur:
            counts["procurementOverdue"] = self._insert_actions(
                cur,
                '''
                INSERT INTO "dbo"."WorkflowActionItems"
                ("EntityType","EntityId","ActionType","AssignedRole","Priority","Title","Message","DueAt")
                SELECT 'PROCUREMENT_HANDOFF',ph."Id",'FOLLOW_UP_PROCUREMENT','PPO_HEAD','HIGH',
                       'Procurement follow-up required',
                       'The operational expected-response target has passed. Review/follow up; this is not a legal deadline.',
                       ph."ExpectedResponseAt"
                FROM "dbo"."ProcurementHandoffs" ph
                WHERE ph."Status" NOT IN ('COMPLETED','CANCELLED')
                  AND ph."ExpectedResponseAt" IS NOT NULL AND ph."ExpectedResponseAt" < NOW()
                  AND NOT EXISTS (SELECT 1 FROM "dbo"."WorkflowActionItems" wai
                    WHERE wai."EntityType"='PROCUREMENT_HANDOFF' AND wai."EntityId"=ph."Id"
                      AND wai."ActionType"='FOLLOW_UP_PROCUREMENT' AND wai."Status"='OPEN')
                ''',
            )
            counts["clarifications"] = self._insert_actions(
                cur,
                '''
                INSERT INTO "dbo"."WorkflowActionItems"
                ("EntityType","EntityId","ActionType","AssignedRole","Priority","Title","Message","DueAt")
                SELECT 'PROCUREMENT_CLARIFICATION',pc."Id",'ANSWER_PROCUREMENT_CLARIFICATION','PPO_HEAD','HIGH',
                       'Procurement clarification requires response',pc."Question",pc."AskedAt" + INTERVAL '24 hours'
                FROM "dbo"."ProcurementClarifications" pc
                WHERE pc."Status"='OPEN'
                  AND NOT EXISTS (SELECT 1 FROM "dbo"."WorkflowActionItems" wai
                    WHERE wai."EntityType"='PROCUREMENT_CLARIFICATION' AND wai."EntityId"=pc."Id"
                      AND wai."ActionType"='ANSWER_PROCUREMENT_CLARIFICATION' AND wai."Status"='OPEN')
                ''',
            )
            counts["workOrderMissing"] = self._insert_actions(
                cur,
                '''
                INSERT INTO "dbo"."WorkflowActionItems"
                ("EntityType","EntityId","ActionType","AssignedRole","Priority","Title","Message")
                SELECT 'MAINTENANCE_REQUEST',mr."Id",'CREATE_OR_CONFIRM_WORK_ORDER','PPO_HEAD','HIGH',
                       'Final Work Order requires review','Procurement is complete but no Work Order exists yet.'
                FROM "dbo"."MaintenanceRequests" mr
                INNER JOIN "dbo"."ProcurementHandoffs" ph ON ph."MaintenanceRequestId"=mr."Id" AND ph."Status"='COMPLETED'
                INNER JOIN "dbo"."ProcurementOutcomes" po ON po."MaintenanceRequestId"=mr."Id"
                LEFT JOIN "dbo"."WorkOrders" wo ON wo."MaintenanceRequestId"=mr."Id"
                WHERE wo."Id" IS NULL
                  AND NOT EXISTS (SELECT 1 FROM "dbo"."WorkflowActionItems" wai
                    WHERE wai."EntityType"='MAINTENANCE_REQUEST' AND wai."EntityId"=mr."Id"
                      AND wai."ActionType"='CREATE_OR_CONFIRM_WORK_ORDER' AND wai."Status"='OPEN')
                ''',
            )
            counts["delayedStart"] = self._insert_actions(
                cur,
                '''
                INSERT INTO "dbo"."WorkflowActionItems"
                ("EntityType","EntityId","ActionType","AssignedRole","Priority","Title","Message","DueAt")
                SELECT 'WORK_ORDER',wo."Id",'CHECK_DELAYED_START','PPO_HEAD','HIGH',
                       'Confirmed Work Order has not started','Planned start has passed and StartedAt is still empty.',wo."PlannedStartAt"
                FROM "dbo"."WorkOrders" wo
                WHERE wo."Status"='CONFIRMED' AND wo."StartedAt" IS NULL
                  AND wo."PlannedStartAt" IS NOT NULL AND wo."PlannedStartAt" < NOW()
                  AND NOT EXISTS (SELECT 1 FROM "dbo"."WorkflowActionItems" wai
                    WHERE wai."EntityType"='WORK_ORDER' AND wai."EntityId"=wo."Id"
                      AND wai."ActionType"='CHECK_DELAYED_START' AND wai."Status"='OPEN')
                ''',
            )
            counts["deadline"] = self._insert_actions(
                cur,
                '''
                INSERT INTO "dbo"."WorkflowActionItems"
                ("EntityType","EntityId","ActionType","AssignedRole","Priority","Title","Message","DueAt")
                SELECT 'WORK_ORDER',wo."Id",'CHECK_WORK_ORDER_DEADLINE','PPO_HEAD',
                       CASE WHEN wo."Deadline" < NOW() THEN 'HIGH' ELSE 'NORMAL' END,
                       'Work Order deadline review',
                       CASE WHEN wo."Deadline" < NOW() THEN 'Work Order deadline has passed.' ELSE 'Work Order deadline is approaching.' END,
                       wo."Deadline"
                FROM "dbo"."WorkOrders" wo
                WHERE wo."Status" IN ('CONFIRMED','IN_PROGRESS','PENDING_PARTS','ON_HOLD','REWORK_REQUIRED')
                  AND wo."Deadline" IS NOT NULL AND wo."Deadline" <= NOW() + INTERVAL '24 hours'
                  AND NOT EXISTS (SELECT 1 FROM "dbo"."WorkflowActionItems" wai
                    WHERE wai."EntityType"='WORK_ORDER' AND wai."EntityId"=wo."Id"
                      AND wai."ActionType"='CHECK_WORK_ORDER_DEADLINE' AND wai."Status"='OPEN')
                ''',
            )
            counts["completionReview"] = self._insert_actions(
                cur,
                '''
                INSERT INTO "dbo"."WorkflowActionItems"
                ("EntityType","EntityId","ActionType","AssignedRole","Priority","Title","Message")
                SELECT 'WORK_ORDER',wo."Id",'REVIEW_COMPLETION','PPO_HEAD','HIGH',
                       'Completion requires PPO Head review','Completion was submitted; AI assistance does not close the Work Order.'
                FROM "dbo"."WorkOrders" wo
                WHERE wo."Status"='COMPLETION_SUBMITTED'
                  AND NOT EXISTS (SELECT 1 FROM "dbo"."WorkflowActionItems" wai
                    WHERE wai."EntityType"='WORK_ORDER' AND wai."EntityId"=wo."Id"
                      AND wai."ActionType"='REVIEW_COMPLETION' AND wai."Status"='OPEN')
                ''',
            )
        return counts

    @staticmethod
    def _insert_actions(cur, sql: str) -> int:
        cur.execute(sql)
        return cur.rowcount
