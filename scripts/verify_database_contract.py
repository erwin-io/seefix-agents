"""Read-only verification of the canonical SEEFIX 2026-09-16 database objects.

This script does not create, alter, truncate, or delete anything.
"""
from __future__ import annotations

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    import psycopg
except ImportError:
    raise SystemExit("Install project requirements first: pip install -r requirements.txt")

REQUIRED_TABLES = [
    "Users", "SystemSettings", "DamageCategories", "Skills", "CategorySkillRequirements",
    "Materials", "CategoryMaterialReferences", "Reports", "ReportImages",
    "ReportAssessmentHistory", "ReportDuplicateCandidates", "ReportVerifications",
    "ReportStatusHistory", "MaintenanceRequests", "MaintenanceRequestSkills",
    "MaintenanceRequestMaterials", "MaintenanceRequestRevisions",
    "MaintenanceRequestStatusHistory", "ProcurementHandoffs", "ProcurementClarifications",
    "ProcurementDocuments", "ProcurementOutcomes", "ProcurementHandoffStatusHistory",
    "WorkOrders", "WorkOrderPeople", "WorkOrderMaterials", "WorkOrderImages",
    "WorkOrderUpdates", "WorkOrderStatusHistory", "WorkflowActionItems", "Notifications",
    "OutboxEvents", "AuditLogs",
]
REQUIRED_VIEWS = [
    "v_PendingAgentReports", "v_ReportEffectiveClassification", "v_ReportPriorityLive",
    "v_CompletedWorkOrderKnowledge", "v_ProcurementInbox", "v_PpoActionCenter",
]
REQUIRED_FUNCTIONS = [
    "ClaimReportForAgent", "ClaimNextPendingReport", "RequeueStaleAgentReports",
    "ClaimNextPendingCompletionWorkOrder", "RequeueStaleCompletionAgentWorkOrders",
]


def main() -> int:
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        print("DATABASE_URL is required.")
        return 2

    missing: list[str] = []
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for name in REQUIRED_TABLES + REQUIRED_VIEWS:
            cur.execute("SELECT to_regclass(%s)", (f'dbo."{name}"',))
            if cur.fetchone()[0] is None:
                missing.append(f"relation dbo.{name}")
        for name in REQUIRED_FUNCTIONS:
            cur.execute(
                """SELECT EXISTS(
                    SELECT 1 FROM pg_proc p
                    JOIN pg_namespace n ON n.oid=p.pronamespace
                    WHERE n.nspname='dbo' AND p.proname=%s
                )""",
                (name,),
            )
            if not cur.fetchone()[0]:
                missing.append(f"function dbo.{name}")

    if missing:
        print("Canonical database contract is NOT fully available:")
        for item in missing:
            print(" -", item)
        return 1
    print("Canonical SEEFIX database contract verified (read-only object check).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
