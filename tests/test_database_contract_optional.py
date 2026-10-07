from __future__ import annotations

import os
import unittest


@unittest.skipUnless(
    os.getenv("SEEFIX_RUN_DB_TESTS", "").lower() in {"1", "true", "yes"},
    "Set SEEFIX_RUN_DB_TESTS=true only for a known canonical SEEFIX test database.",
)
class OptionalDatabaseContractTests(unittest.TestCase):
    def test_canonical_relations_exist(self):
        import psycopg
        from dotenv import load_dotenv

        load_dotenv()
        dsn = os.environ["DATABASE_URL"]
        names = [
            "Reports", "ReportImages", "DamageCategories", "ReportDuplicateCandidates",
            "MaintenanceRequests", "MaintenanceRequestSkills", "MaintenanceRequestMaterials",
            "ProcurementHandoffs", "ProcurementClarifications", "ProcurementOutcomes",
            "WorkOrders", "WorkOrderImages", "WorkOrderMaterials", "WorkflowActionItems", "OutboxEvents",
        ]
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            for name in names:
                cur.execute("SELECT to_regclass(%s)", (f'dbo."{name}"',))
                self.assertIsNotNone(cur.fetchone()[0], name)

    def test_canonical_queue_functions_exist(self):
        import psycopg
        from dotenv import load_dotenv

        load_dotenv()
        dsn = os.environ["DATABASE_URL"]
        names = [
            "ClaimReportForAgent", "ClaimNextPendingReport", "RequeueStaleAgentReports",
            "ClaimNextPendingCompletionWorkOrder", "RequeueStaleCompletionAgentWorkOrders",
        ]
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            for name in names:
                cur.execute(
                    """SELECT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                       WHERE n.nspname='dbo' AND p.proname=%s)""",
                    (name,),
                )
                self.assertTrue(cur.fetchone()[0], name)
