from __future__ import annotations

import os
import unittest


RUN_DB_TESTS = (
    os.getenv(
        "SEEFIX_RUN_DB_TESTS",
        "false",
    )
    .strip()
    .lower()
    in {
        "1",
        "true",
        "yes",
        "on",
    }
)


EXPECTED_RELATIONS = {
    "Reports",
    "MaintenanceRequests",
    "MaintenanceReviews",

    "ProcurementHandoffs",
    "ProcurementOutcomes",

    "WorkOrders",
    "WorkOrderAssignments",

    "v_ReportPriorityLive",

    "v_MaintenanceReviewQueue",
    "v_MaintenanceActionCenter",

    "v_ProcurementInbox",
    "v_WorkerInbox",

    "v_CompletedWorkOrderKnowledge",
    "v_ReportTimeline",
}


EXPECTED_QUEUE_FUNCTIONS = {
    "ClaimReportForAgent",
    "ClaimNextPendingReport",
    "RequeueStaleAgentReports",

    "ClaimNextPendingCompletionWorkOrder",
    "RequeueStaleCompletionAgentWorkOrders",
}


@unittest.skipUnless(
    RUN_DB_TESTS,
    (
        "Set SEEFIX_RUN_DB_TESTS=true "
        "only for a known canonical "
        "SEEFIX test database."
    ),
)
class OptionalDatabaseContractTests(
    unittest.TestCase
):
    @classmethod
    def setUpClass(
        cls,
    ):
        from dotenv import load_dotenv

        load_dotenv()

        cls.database_url = (
            os.getenv(
                "DATABASE_URL"
            )
            or ""
        ).strip()

        if not cls.database_url:
            raise unittest.SkipTest(
                (
                    "DATABASE_URL "
                    "is not configured."
                )
            )

    def test_canonical_relations_exist(
        self,
    ):
        import psycopg

        from psycopg.rows import (
            dict_row,
        )

        with psycopg.connect(
            self.database_url,
            row_factory=dict_row,
        ) as conn:

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        c.relname AS name
                    FROM pg_catalog.pg_class c
                    JOIN pg_catalog.pg_namespace n
                      ON n.oid = c.relnamespace
                    WHERE n.nspname = 'dbo'
                      AND c.relkind IN
                      (
                          'r',
                          'p',
                          'v',
                          'm'
                      )
                    """
                )

                actual = {
                    str(
                        row["name"]
                    )
                    for row
                    in cur.fetchall()
                }

        missing = sorted(
            EXPECTED_RELATIONS
            -
            actual
        )

        self.assertEqual(
            [],
            missing,
            (
                "Missing dbo relations: "
                f"{missing}"
            ),
        )

        self.assertNotIn(
            "v_PpoActionCenter",
            actual,
        )

        self.assertIn(
            "v_MaintenanceActionCenter",
            actual,
        )

    def test_canonical_queue_functions_exist(
        self,
    ):
        import psycopg

        from psycopg.rows import (
            dict_row,
        )

        with psycopg.connect(
            self.database_url,
            row_factory=dict_row,
        ) as conn:

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT
                        p.proname AS name
                    FROM pg_catalog.pg_proc p
                    JOIN pg_catalog.pg_namespace n
                      ON n.oid = p.pronamespace
                    WHERE n.nspname = 'dbo'
                    """
                )

                actual = {
                    str(
                        row["name"]
                    )
                    for row
                    in cur.fetchall()
                }

        missing = sorted(
            EXPECTED_QUEUE_FUNCTIONS
            -
            actual
        )

        self.assertEqual(
            [],
            missing,
            (
                "Missing dbo queue "
                f"functions: {missing}"
            ),
        )

    def test_internal_work_order_procurement_outcome_is_nullable(
        self,
    ):
        import psycopg

        from psycopg.rows import (
            dict_row,
        )

        with psycopg.connect(
            self.database_url,
            row_factory=dict_row,
        ) as conn:

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = 'dbo'
                      AND table_name =
                          'WorkOrders'
                      AND column_name =
                          'ProcurementOutcomeId'
                    """
                )

                row = cur.fetchone()

        self.assertIsNotNone(
            row
        )

        self.assertEqual(
            "YES",
            row["is_nullable"],
        )

    def test_new_maintenance_review_columns_exist(
        self,
    ):
        import psycopg

        from psycopg.rows import (
            dict_row,
        )

        expected = {
            "ReportId",
            "MaintenanceRequestId",
            "Status",
            "Decision",
            "FinalCategory",
            "FinalUrgency",
            "ReviewedBy",
            "ReviewedAt",
        }

        with psycopg.connect(
            self.database_url,
            row_factory=dict_row,
        ) as conn:

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'dbo'
                      AND table_name =
                          'MaintenanceReviews'
                    """
                )

                actual = {
                    str(
                        row["column_name"]
                    )
                    for row
                    in cur.fetchall()
                }

        missing = sorted(
            expected
            -
            actual
        )

        self.assertEqual(
            [],
            missing,
            (
                "Missing "
                "MaintenanceReviews "
                f"columns: {missing}"
            ),
        )


if __name__ == "__main__":
    unittest.main()