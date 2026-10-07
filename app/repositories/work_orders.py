from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..database import Database
from ..schemas import CompletionAssessmentResult
from ..work_order.readiness import check_work_order_readiness
from ..work_order.variance import calculate_work_order_variance


@dataclass(frozen=True)
class ClaimedCompletionWorkOrder:
    id: UUID
    work_order_no: str
    attempt_count: int


@dataclass(frozen=True)
class CompletionImageReference:
    id: UUID
    secure_url: str
    image_type: str


@dataclass(frozen=True)
class CompletionBundle:
    work_order_id: UUID
    work_order_no: str
    report_id: UUID
    repair_notes: str
    original_image_url: str
    completion_image_urls: tuple[str, ...]
    planned_duration_days: float | None
    actual_duration_days: float | None
    planned_crew_size: int | None
    actual_crew_size: int | None
    planned_labor_hours: float | None
    actual_labor_hours: float | None
    planned_materials: tuple[dict, ...]
    actual_materials: tuple[dict, ...]

    def model_context(self) -> dict:
        return {
            "workOrderNo": self.work_order_no,
            "repairNotes": self.repair_notes,
            "plannedDurationDays": self.planned_duration_days,
            "actualDurationDays": self.actual_duration_days,
            "plannedCrewSize": self.planned_crew_size,
            "actualCrewSize": self.actual_crew_size,
            "plannedLaborHours": self.planned_labor_hours,
            "actualLaborHours": self.actual_labor_hours,
            "plannedMaterials": list(self.planned_materials),
            "actualMaterials": list(self.actual_materials),
        }


class WorkOrderRepository:
    def __init__(self, database: Database, *, completion_worker_name: str) -> None:
        self.database = database
        self.completion_worker_name = completion_worker_name

    def requeue_stale_completion(self, stale_minutes: int) -> int:
        with self.database.connect() as conn, conn.cursor() as cur:
            cur.execute(
                'SELECT "dbo"."RequeueStaleCompletionAgentWorkOrders"(%s)',
                (stale_minutes,),
            )
            row = cur.fetchone()
            return int(row[0] if row else 0)

    def claim_next_pending_completion(self) -> ClaimedCompletionWorkOrder | None:
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                'SELECT * FROM "dbo"."ClaimNextPendingCompletionWorkOrder"(%s)',
                (self.completion_worker_name,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return ClaimedCompletionWorkOrder(
            id=row["Id"],
            work_order_no=str(row["WorkOrderNo"]),
            attempt_count=int(row["CompletionAgentAttemptCount"]),
        )

    def request_completion_processing(self, work_order_id: UUID) -> dict:
        """Queue completion assistance without changing the business WorkOrder status."""
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                SELECT "Id","WorkOrderNo","Status","CompletionAgentStatus"
                FROM "dbo"."WorkOrders" WHERE "Id"=%s FOR UPDATE
                ''',
                (work_order_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise LookupError("Work Order was not found.")
            if row["Status"] != "COMPLETION_SUBMITTED":
                raise ValueError("Completion assistance requires Work Order status COMPLETION_SUBMITTED.")
            if row["CompletionAgentStatus"] in {"PROCESSING", "PENDING"}:
                return dict(row)
            if row["CompletionAgentStatus"] == "COMPLETED":
                return dict(row)
            cur.execute(
                '''
                UPDATE "dbo"."WorkOrders"
                SET "CompletionAgentStatus"='PENDING',
                    "CompletionAgentClaimedAt"=NULL,"CompletionAgentClaimedBy"=NULL,
                    "CompletionAgentStartedAt"=NULL,"CompletionAgentCompletedAt"=NULL,
                    "CompletionAgentLastError"=NULL
                WHERE "Id"=%s
                RETURNING "Id","WorkOrderNo","Status","CompletionAgentStatus"
                ''',
                (work_order_id,),
            )
            return dict(cur.fetchone())

    def get_completion_status(self, work_order_id: UUID) -> dict | None:
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                SELECT "Id","WorkOrderNo","Status","CompletionAgentStatus",
                       "CompletionAgentAttemptCount","CompletionAgentStartedAt",
                       "CompletionAgentCompletedAt","CompletionAgentLastError","CompletionVisualResult"
                FROM "dbo"."WorkOrders" WHERE "Id"=%s
                ''',
                (work_order_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_completion_bundle(self, work_order_id: UUID) -> CompletionBundle:
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                SELECT wo."Id",wo."WorkOrderNo",wo."ReportId",wo."Status",wo."RepairNotes",
                       wo."PlannedDurationDays",wo."ActualDurationDays",
                       wo."PlannedCrewSize",wo."ActualCrewSize",
                       wo."PlannedLaborHours",wo."ActualLaborHours"
                FROM "dbo"."WorkOrders" wo WHERE wo."Id"=%s
                ''',
                (work_order_id,),
            )
            wo = cur.fetchone()
            if wo is None:
                raise LookupError("Work Order was not found.")
            if wo["Status"] != "COMPLETION_SUBMITTED":
                raise ValueError("Completion worker requires COMPLETION_SUBMITTED status.")
            if not wo["RepairNotes"] or not str(wo["RepairNotes"]).strip():
                raise ValueError("Completion submission has no repair notes.")

            cur.execute(
                '''
                SELECT "SecureUrl" FROM "dbo"."ReportImages"
                WHERE "ReportId"=%s ORDER BY "IsPrimary" DESC,"CreatedAt" ASC LIMIT 1
                ''',
                (wo["ReportId"],),
            )
            original = cur.fetchone()
            if original is None:
                raise RuntimeError("Original Report image is missing.")

            cur.execute(
                '''
                SELECT "SecureUrl" FROM "dbo"."WorkOrderImages"
                WHERE "WorkOrderId"=%s AND "ImageType"='COMPLETION'
                ORDER BY "CreatedAt" ASC
                ''',
                (work_order_id,),
            )
            completion_rows = cur.fetchall()
            if not completion_rows:
                raise RuntimeError("Completion image is missing.")

            cur.execute(
                '''
                SELECT "Stage","MaterialName","Unit","Quantity","Notes"
                FROM "dbo"."WorkOrderMaterials" WHERE "WorkOrderId"=%s
                ORDER BY "Stage","CreatedAt"
                ''',
                (work_order_id,),
            )
            material_rows = cur.fetchall()

        planned = tuple(
            {
                "materialName": row["MaterialName"], "unit": row["Unit"],
                "quantity": float(row["Quantity"]) if row["Quantity"] is not None else None,
                "notes": row["Notes"],
            }
            for row in material_rows if row["Stage"] == "PLANNED"
        )
        actual = tuple(
            {
                "materialName": row["MaterialName"], "unit": row["Unit"],
                "quantity": float(row["Quantity"]) if row["Quantity"] is not None else None,
                "notes": row["Notes"],
            }
            for row in material_rows if row["Stage"] == "ACTUAL"
        )
        return CompletionBundle(
            work_order_id=wo["Id"], work_order_no=str(wo["WorkOrderNo"]), report_id=wo["ReportId"],
            repair_notes=str(wo["RepairNotes"]), original_image_url=str(original["SecureUrl"]),
            completion_image_urls=tuple(str(row["SecureUrl"]) for row in completion_rows),
            planned_duration_days=float(wo["PlannedDurationDays"]) if wo["PlannedDurationDays"] is not None else None,
            actual_duration_days=float(wo["ActualDurationDays"]) if wo["ActualDurationDays"] is not None else None,
            planned_crew_size=int(wo["PlannedCrewSize"]) if wo["PlannedCrewSize"] is not None else None,
            actual_crew_size=int(wo["ActualCrewSize"]) if wo["ActualCrewSize"] is not None else None,
            planned_labor_hours=float(wo["PlannedLaborHours"]) if wo["PlannedLaborHours"] is not None else None,
            actual_labor_hours=float(wo["ActualLaborHours"]) if wo["ActualLaborHours"] is not None else None,
            planned_materials=planned, actual_materials=actual,
        )

    def complete_completion_assessment(self, result: CompletionAssessmentResult) -> None:
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                UPDATE "dbo"."WorkOrders" SET
                  "CompletionAgentStatus"='COMPLETED',"CompletionAgentCompletedAt"=NOW(),
                  "CompletionAgentLastError"=NULL,"CompletionVisualResult"=%s,
                  "CompletionAssessmentJson"=%s,"CompletionAiProvider"=%s,
                  "CompletionAiModelId"=%s,"CompletionAiPromptVersion"=%s,
                  "CompletionAiProcessingTimeMs"=%s
                WHERE "Id"=%s AND "CompletionAgentStatus"='PROCESSING'
                RETURNING "WorkOrderNo","CompletionAgentAttemptCount"
                ''',
                (
                    result.visual_result, Jsonb(result.model_dump(mode="json")), result.provider,
                    result.model_id, result.prompt_version, result.processing_time_ms, result.work_order_id,
                ),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("Unable to persist completion assessment; queue claim is no longer PROCESSING.")
            dedupe = f"work_order:{result.work_order_id}:completion:{row['CompletionAgentAttemptCount']}:completed"
            cur.execute(
                '''
                INSERT INTO "dbo"."OutboxEvents"
                ("AggregateType","AggregateId","Transport","ChannelName","EventName","Payload","DeduplicationKey")
                VALUES ('WORK_ORDER',%s,'PUSHER',%s,'work_order.completion.assessed',%s,%s)
                ON CONFLICT ("DeduplicationKey") WHERE "DeduplicationKey" IS NOT NULL DO NOTHING
                ''',
                (
                    result.work_order_id, f"work-order-{result.work_order_id}",
                    Jsonb({"workOrderId": str(result.work_order_id), "completionAgentStatus": "COMPLETED"}),
                    dedupe,
                ),
            )
            notification_key = f"work-order:{result.work_order_id}:completion-agent:{row['CompletionAgentAttemptCount']}:ready"
            notification_payload = Jsonb({
                "workOrderId": str(result.work_order_id),
                "workOrderNo": str(row["WorkOrderNo"]),
                "visualResult": result.visual_result,
                "deduplicationKey": notification_key,
            })
            cur.execute(
                '''
                INSERT INTO "dbo"."Notifications"
                  ("UserId","Type","Title","Message","EntityType","EntityId","Payload")
                SELECT u."Id",'COMPLETION_ASSESSMENT_READY','Completion assessment ready',%s,
                       'WORK_ORDER',%s,%s
                FROM "dbo"."Users" u
                WHERE u."Role"='PPO_HEAD' AND u."IsActive"=TRUE
                  AND NOT EXISTS (
                    SELECT 1 FROM "dbo"."Notifications" n
                    WHERE n."UserId"=u."Id" AND n."Payload"->>'deduplicationKey'=%s
                  )
                ''',
                (
                    f"{row['WorkOrderNo']} completion visual assistance is ready for PPO Head review.",
                    result.work_order_id,
                    notification_payload,
                    notification_key,
                ),
            )

    def fail_completion(self, *, work_order_id: UUID, error: str) -> None:
        message = str(error).strip()[:2000]
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                UPDATE "dbo"."WorkOrders" SET
                  "CompletionAgentStatus"='FAILED',"CompletionAgentCompletedAt"=NOW(),
                  "CompletionAgentLastError"=%s
                WHERE "Id"=%s
                RETURNING "WorkOrderNo","CompletionAgentAttemptCount"
                ''',
                (message, work_order_id),
            )
            row = cur.fetchone()
            if not row:
                return
            dedupe = f"work_order:{work_order_id}:completion:{row['CompletionAgentAttemptCount']}:failed"
            cur.execute(
                '''
                INSERT INTO "dbo"."OutboxEvents"
                ("AggregateType","AggregateId","Transport","EventName","Payload","DeduplicationKey")
                VALUES ('WORK_ORDER',%s,'PUSHER','work_order.completion.failed',%s,%s)
                ON CONFLICT ("DeduplicationKey") WHERE "DeduplicationKey" IS NOT NULL DO NOTHING
                ''',
                (work_order_id, Jsonb({"workOrderId": str(work_order_id), "completionAgentStatus": "FAILED"}), dedupe),
            )
            notification_key = f"work-order:{work_order_id}:completion-agent:{row['CompletionAgentAttemptCount']}:failed"
            cur.execute(
                '''
                INSERT INTO "dbo"."Notifications"
                  ("UserId","Type","Title","Message","EntityType","EntityId","Payload")
                SELECT u."Id",'COMPLETION_ASSESSMENT_FAILED','Completion assessment needs manual review',%s,
                       'WORK_ORDER',%s,%s
                FROM "dbo"."Users" u
                WHERE u."Role"='PPO_HEAD' AND u."IsActive"=TRUE
                  AND NOT EXISTS (
                    SELECT 1 FROM "dbo"."Notifications" n
                    WHERE n."UserId"=u."Id" AND n."Payload"->>'deduplicationKey'=%s
                  )
                ''',
                (
                    f"Automated completion assistance failed for {row['WorkOrderNo']}; PPO Head review is still required.",
                    work_order_id,
                    Jsonb({
                        "workOrderId": str(work_order_id),
                        "workOrderNo": str(row["WorkOrderNo"]),
                        "deduplicationKey": notification_key,
                    }),
                    notification_key,
                ),
            )

    def get_review_context(self, work_order_id: UUID) -> dict:
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                SELECT wo.*, mr."Status" AS "MaintenanceRequestStatus", mr."RequiredService",
                       mr."SafetyRequirements" AS "RequestSafetyRequirements",
                       mr."EstimatedLaborHoursMin",mr."EstimatedLaborHoursMax",
                       mr."EstimatedManpowerMin",mr."EstimatedManpowerMax",
                       mr."EstimatedDurationDaysMin",mr."EstimatedDurationDaysMax",
                       r."AgentStatus" AS "ReportAgentStatus",r."ScopeDecision",r."ReviewedAt",
                       ph."Status" AS "ProcurementHandoffStatus",
                       po."Id" AS "OutcomeId"
                FROM "dbo"."WorkOrders" wo
                INNER JOIN "dbo"."MaintenanceRequests" mr ON mr."Id"=wo."MaintenanceRequestId"
                INNER JOIN "dbo"."Reports" r ON r."Id"=wo."ReportId"
                INNER JOIN "dbo"."ProcurementOutcomes" po ON po."Id"=wo."ProcurementOutcomeId"
                INNER JOIN "dbo"."ProcurementHandoffs" ph ON ph."Id"=po."ProcurementHandoffId"
                WHERE wo."Id"=%s
                ''',
                (work_order_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise LookupError("Work Order was not found.")
        return {
            "reportAssessmentExists": row["ReportAgentStatus"] == "COMPLETED" and row["ScopeDecision"] == "Facility Issue",
            "maintenanceRequestAuthorized": row["MaintenanceRequestStatus"] in {
                "AUTHORIZED", "SUBMITTED_TO_PROCUREMENT", "PROCUREMENT_COMPLETED", "WORK_ORDER_CREATED"
            },
            "procurementHandoffStatus": row["ProcurementHandoffStatus"],
            "procurementOutcomeExists": row["OutcomeId"] is not None,
            "executionType": row["ExecutionType"],
            "assignedPartyName": row["AssignedPartyName"],
            "responsibleLeadUserId": str(row["ResponsibleLeadUserId"]) if row["ResponsibleLeadUserId"] else None,
            "responsibleLeadName": row["ResponsibleLeadName"],
            "responsibleLeadContact": row["ResponsibleLeadContact"],
            "responsibleLeadEmail": row["ResponsibleLeadEmail"],
            "requiredService": row["RequiredService"],
            "plannedStartAt": row["PlannedStartAt"],
            "deadline": row["Deadline"],
            "procurementReferenceNo": row["ProcurementReferenceNo"],
            "requestSafetyRequirements": row["RequestSafetyRequirements"],
            "workOrderSafetyRequirements": row["SafetyRequirements"],
            "estimatedLaborHoursMin": row["EstimatedLaborHoursMin"],
            "estimatedLaborHoursMax": row["EstimatedLaborHoursMax"],
            "estimatedManpowerMin": row["EstimatedManpowerMin"],
            "estimatedManpowerMax": row["EstimatedManpowerMax"],
            "estimatedDurationDaysMin": row["EstimatedDurationDaysMin"],
            "estimatedDurationDaysMax": row["EstimatedDurationDaysMax"],
            "plannedLaborHours": row["PlannedLaborHours"],
            "plannedCrewSize": row["PlannedCrewSize"],
            "plannedDurationDays": row["PlannedDurationDays"],
        }

    def review_work_order(self, work_order_id: UUID) -> dict:
        context = self.get_review_context(work_order_id)
        readiness = check_work_order_readiness(context)
        variance = calculate_work_order_variance(context)
        payload = {
            "readiness": readiness.model_dump(mode="json"),
            "variance": variance.model_dump(mode="json"),
        }
        with self.database.connect() as conn, conn.cursor() as cur:
            cur.execute(
                'UPDATE "dbo"."WorkOrders" SET "PlanningVarianceJson"=%s WHERE "Id"=%s',
                (Jsonb(variance.model_dump(mode="json")), work_order_id),
            )
            dedupe = f"work_order:{work_order_id}:review_ready"
            cur.execute(
                '''
                INSERT INTO "dbo"."OutboxEvents"
                ("AggregateType","AggregateId","Transport","EventName","Payload","DeduplicationKey")
                VALUES ('WORK_ORDER',%s,'PUSHER','work_order.review_ready',%s,%s)
                ON CONFLICT ("DeduplicationKey") WHERE "DeduplicationKey" IS NOT NULL DO NOTHING
                ''',
                (work_order_id, Jsonb({"workOrderId": str(work_order_id), "ready": readiness.ready}), dedupe),
            )
        return payload

    def build_draft_preview_for_outcome(self, procurement_outcome_id: UUID) -> dict:
        """Return validated draft data. Node/PPO remains responsible for business creation/confirmation."""
        with self.database.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                '''
                SELECT po.*, ph."Status" AS "HandoffStatus", mr."ReportId",mr."Id" AS "MaintenanceRequestId",
                       mr."Status" AS "MaintenanceRequestStatus",mr."RequiredService",mr."ScopeOfWork",
                       mr."SafetyRequirements",mr."EstimatedLaborHoursMin",mr."EstimatedLaborHoursMax",
                       mr."EstimatedManpowerMin",mr."EstimatedManpowerMax",
                       mr."EstimatedDurationDaysMin",mr."EstimatedDurationDaysMax",
                       r."AgentStatus" AS "ReportAgentStatus",r."ScopeDecision"
                FROM "dbo"."ProcurementOutcomes" po
                INNER JOIN "dbo"."ProcurementHandoffs" ph ON ph."Id"=po."ProcurementHandoffId"
                INNER JOIN "dbo"."MaintenanceRequests" mr ON mr."Id"=po."MaintenanceRequestId"
                INNER JOIN "dbo"."Reports" r ON r."Id"=mr."ReportId"
                WHERE po."Id"=%s
                ''',
                (procurement_outcome_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise LookupError("Procurement Outcome was not found.")
        context = {
            "reportAssessmentExists": row["ReportAgentStatus"] == "COMPLETED" and row["ScopeDecision"] == "Facility Issue",
            "maintenanceRequestAuthorized": row["MaintenanceRequestStatus"] in {"AUTHORIZED","SUBMITTED_TO_PROCUREMENT","PROCUREMENT_COMPLETED","WORK_ORDER_CREATED"},
            "procurementHandoffStatus": row["HandoffStatus"],
            "procurementOutcomeExists": True,
            "executionType": row["ExecutionType"],
            "assignedPartyName": row["AssignedPartyName"],
            "responsibleLeadUserId": str(row["ResponsibleLeadUserId"]) if row["ResponsibleLeadUserId"] else None,
            "responsibleLeadName": row["ResponsibleLeadName"],
            "responsibleLeadContact": row["ResponsibleLeadContact"],
            "responsibleLeadEmail": row["ResponsibleLeadEmail"],
            "requiredService": row["RequiredService"],
            "plannedStartAt": row["PlannedStartAt"],
            "deadline": row["PlannedDeadlineAt"],
            "procurementReferenceNo": row["ProcurementReferenceNo"],
            "requestSafetyRequirements": row["SafetyRequirements"],
            "workOrderSafetyRequirements": row["SafetyRequirements"],
            "estimatedLaborHoursMin": row["EstimatedLaborHoursMin"],
            "estimatedLaborHoursMax": row["EstimatedLaborHoursMax"],
            "estimatedManpowerMin": row["EstimatedManpowerMin"],
            "estimatedManpowerMax": row["EstimatedManpowerMax"],
            "estimatedDurationDaysMin": row["EstimatedDurationDaysMin"],
            "estimatedDurationDaysMax": row["EstimatedDurationDaysMax"],
            "plannedLaborHours": None,
            "plannedCrewSize": row["PlannedCrewSize"],
            "plannedDurationDays": row["AgreedDurationDays"],
        }
        readiness = check_work_order_readiness(context)
        variance = calculate_work_order_variance(context)
        return {
            "reportId": str(row["ReportId"]),
            "maintenanceRequestId": str(row["MaintenanceRequestId"]),
            "procurementOutcomeId": str(row["Id"]),
            "status": "PENDING_CONFIRMATION",
            "executionType": row["ExecutionType"],
            "assignedPartyName": row["AssignedPartyName"],
            "responsibleLeadUserId": str(row["ResponsibleLeadUserId"]) if row["ResponsibleLeadUserId"] else None,
            "responsibleLeadName": row["ResponsibleLeadName"],
            "responsibleLeadContact": row["ResponsibleLeadContact"],
            "responsibleLeadEmail": row["ResponsibleLeadEmail"],
            "plannedStartAt": row["PlannedStartAt"].isoformat() if row["PlannedStartAt"] else None,
            "deadline": row["PlannedDeadlineAt"].isoformat() if row["PlannedDeadlineAt"] else None,
            "plannedDurationDays": float(row["AgreedDurationDays"]) if row["AgreedDurationDays"] is not None else None,
            "plannedCrewSize": int(row["PlannedCrewSize"]) if row["PlannedCrewSize"] is not None else None,
            "plannedLaborHours": None,
            "instructions": row["ScopeOfWork"],
            "safetyRequirements": row["SafetyRequirements"],
            "procurementReferenceNo": row["ProcurementReferenceNo"],
            "readiness": readiness.model_dump(mode="json"),
            "variance": variance.model_dump(mode="json"),
        }
