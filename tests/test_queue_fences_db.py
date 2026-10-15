"""seefix-agents#7: an old or late Agent attempt must never revive or overwrite closed/reviewed work.

Runs the real repository SQL and the real triggers against a disposable local SEEFIX copy.
Everything happens inside one transaction that is rolled back, so the database is left untouched.
Opt-in: SEEFIX_TEST_DATABASE_URL=postgresql://...@127.0.0.1/... (localhost only, never DATABASE_URL).
"""
from __future__ import annotations

import os
import unittest
from urllib.parse import urlparse
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "postgresql://test@127.0.0.1:1/test")
os.environ.setdefault("SEEFIX_AGENT_API_KEY", "test-only")

import psycopg
from psycopg.rows import dict_row

from app.repositories.reports import ReportRepository
from app.repositories.work_orders import WorkOrderRepository
from app.schemas import (
    CompletionAssessmentResult,
    CompletionModelResponse,
    FacilityCategory,
    Urgency,
)

try:
    from tests.test_core import assessed_result
except ImportError:  # unittest discover -s tests puts tests/ itself on sys.path
    from test_core import assessed_result

TEST_URL = os.getenv("SEEFIX_TEST_DATABASE_URL", "").strip()
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
TERMINAL = ("RESOLVED", "CANCELLED", "NO_ACTION", "DUPLICATE")


class _Savepoint:
    """Database.connect() stand-in: each repository call gets a savepoint on one shared connection."""

    def __init__(self, conn, row_factory):
        self.conn, self.row_factory = conn, row_factory

    def __enter__(self):
        self.conn.row_factory = self.row_factory or psycopg.rows.tuple_row
        self.tx = self.conn.transaction()
        self.tx.__enter__()
        return self.conn

    def __exit__(self, *exc):
        return self.tx.__exit__(*exc)


class _RolledBackDatabase:
    def __init__(self, conn):
        self.conn = conn

    def connect(self, *, row_factory=None):
        return _Savepoint(self.conn, row_factory)


@unittest.skipUnless(
    TEST_URL and urlparse(TEST_URL).hostname in LOCAL_HOSTS,
    "Set SEEFIX_TEST_DATABASE_URL to a disposable localhost SEEFIX copy.",
)
class QueueFenceDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(TEST_URL, row_factory=dict_row)
        self.conn.execute("SELECT 1")  # open the outer transaction that tearDown rolls back
        db = _RolledBackDatabase(self.conn)
        self.reports = ReportRepository(
            database=db, worker_name="fence-test", recurrence_threshold=2,
            recurrence_lookback_days=365, verification_threshold=2, duplicate_lookback_days=30,
        )
        self.work_orders = WorkOrderRepository(db, completion_worker_name="fence-test")

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def sql(self, query, params=()):
        self.conn.row_factory = dict_row
        return self.conn.execute(query, params)

    # ------------------------------------------------------------------ completion

    def _submitted_work_order(self):
        row = self.sql('SELECT "Id" FROM "dbo"."WorkOrders" ORDER BY "CreatedAt" LIMIT 1').fetchone()
        if row is None:
            self.skipTest("No Work Order rows in the test copy.")
        # Only this WO may be claimable so the poll claim is deterministic.
        self.sql('''UPDATE "dbo"."WorkOrders" SET "CompletionAgentStatus"='COMPLETED'
                    WHERE "CompletionAgentStatus" IN ('PENDING','PROCESSING') AND "Id"<>%s''', (row["Id"],))
        self.sql('''UPDATE "dbo"."WorkOrders" SET "Status"='COMPLETION_SUBMITTED',"CompletionAgentStatus"='PENDING',
                    "CompletionVisualResult"=NULL WHERE "Id"=%s''', (row["Id"],))
        return row["Id"]

    def _completion_result(self, work_order_id, visual_result):
        return CompletionAssessmentResult(
            work_order_id=work_order_id, visual_result=visual_result,
            model_response=CompletionModelResponse(
                visible_issue_improved=True, visible_issue_still_present=False,
                insufficient_completion_image=False, additional_visible_concern=False,
                summary="Visible repair looks improved.", needs_maintenance_supervisor_review=True,
            ),
            provider="test", model_id="test-model", prompt_version="test", processing_time_ms=1,
        )

    def _wo(self, work_order_id):
        return self.sql('''SELECT "Status","CompletionAgentStatus","CompletionAgentAttemptCount","CompletionVisualResult"
                           FROM "dbo"."WorkOrders" WHERE "Id"=%s''', (work_order_id,)).fetchone()

    def _requeue_as_stale(self, work_order_id):
        # NOW() is frozen inside the test transaction, so age the claim instead of waiting.
        self.sql('''UPDATE "dbo"."WorkOrders" SET "CompletionAgentClaimedAt"=NOW()-INTERVAL '2 hours',
                    "CompletionAgentStartedAt"=NOW()-INTERVAL '2 hours' WHERE "Id"=%s''', (work_order_id,))
        self.assertEqual(self.work_orders.requeue_stale_completion(30), 1)

    def test_old_completion_attempt_cannot_overwrite_newer_attempt(self):
        wo = self._submitted_work_order()
        old = self.work_orders.claim_next_pending_completion()
        self._requeue_as_stale(wo)
        new = self.work_orders.claim_next_pending_completion()
        self.assertEqual(new.attempt_count, old.attempt_count + 1)

        self.assertFalse(self.work_orders.complete_completion_assessment(
            self._completion_result(wo, "OLD_ATTEMPT"), attempt_count=old.attempt_count))
        self.work_orders.fail_completion(work_order_id=wo, attempt_count=old.attempt_count, error="late")
        state = self._wo(wo)
        self.assertEqual(state["CompletionAgentStatus"], "PROCESSING")
        self.assertIsNone(state["CompletionVisualResult"])

        self.assertTrue(self.work_orders.complete_completion_assessment(
            self._completion_result(wo, "NEW_ATTEMPT"), attempt_count=new.attempt_count))
        self.work_orders.fail_completion(work_order_id=wo, attempt_count=old.attempt_count, error="late")
        state = self._wo(wo)
        self.assertEqual((state["CompletionAgentStatus"], state["CompletionVisualResult"]), ("COMPLETED", "NEW_ATTEMPT"))

    def test_late_completion_failure_does_not_kill_a_requeued_claim(self):
        wo = self._submitted_work_order()
        old = self.work_orders.claim_next_pending_completion()
        self._requeue_as_stale(wo)
        self.work_orders.fail_completion(work_order_id=wo, attempt_count=old.attempt_count, error="late")
        self.assertEqual(self._wo(wo)["CompletionAgentStatus"], "PENDING")

    def test_late_completion_result_cannot_land_on_supervisor_reviewed_work_order(self):
        wo = self._submitted_work_order()
        claimed = self.work_orders.claim_next_pending_completion()
        self.sql('''UPDATE "dbo"."WorkOrders" SET "Status"='COMPLETED' WHERE "Id"=%s''', (wo,))  # human decision

        self.assertFalse(self.work_orders.complete_completion_assessment(
            self._completion_result(wo, "LATE"), attempt_count=claimed.attempt_count))
        self.work_orders.fail_completion(work_order_id=wo, attempt_count=claimed.attempt_count, error="late")
        state = self._wo(wo)
        self.assertEqual(state["Status"], "COMPLETED")
        self.assertIsNone(state["CompletionVisualResult"])
        self.assertNotEqual(state["CompletionAgentStatus"], "FAILED")

    # ------------------------------------------------------------------ initial report

    def _submitted_report(self):
        row = self.sql(
            '''SELECT r."Id" FROM "dbo"."Reports" r
               WHERE r."Status"='RESOLVED' AND NOT EXISTS (
                 SELECT 1 FROM "dbo"."Reports" o
                 WHERE o."ReporterId"=r."ReporterId" AND o."Status" <> ALL(%s))
               LIMIT 1''', (list(TERMINAL),),
        ).fetchone()
        if row is None:
            self.skipTest("No reusable report row in the test copy.")
        self.sql('''UPDATE "dbo"."Reports" SET "Status"='SUBMITTED',"AgentStatus"='PENDING' WHERE "Id"=%s''', (row["Id"],))
        return row["Id"]

    def _report(self, report_id):
        return self.sql('''SELECT "Status","AgentStatus","AgentAttemptCount","AiSummary"
                           FROM "dbo"."Reports" WHERE "Id"=%s''', (report_id,)).fetchone()

    def _result(self):
        return assessed_result(FacilityCategory.PLUMBING_WATER, Urgency.MEDIUM)

    def test_cancelled_report_cannot_be_revived_by_old_attempt(self):
        report_id = self._submitted_report()
        claimed = self.reports.claim_specific(report_id)
        self.sql('''UPDATE "dbo"."Reports" SET "Status"='CANCELLED' WHERE "Id"=%s''', (report_id,))

        self.assertFalse(self.reports.complete_report(
            report_id=report_id, attempt_count=claimed.attempt_count, result=self._result(), context=None))
        self.reports.fail_report(report_id=report_id, attempt_count=claimed.attempt_count, error="late")
        self.assertIsNone(self.reports.claim_specific(report_id))
        self.assertEqual(self._report(report_id)["Status"], "CANCELLED")
        self.assertEqual(self._report(report_id)["AgentStatus"], "PROCESSING")

        # Even a direct write cannot reopen it (TR_Reports_GuardCancelledMutation).
        with self.assertRaises(psycopg.errors.CheckViolation), self.conn.transaction():
            self.sql('''UPDATE "dbo"."Reports" SET "AgentStatus"='PENDING' WHERE "Id"=%s''', (report_id,))

    def test_failed_retry_fences_out_the_previous_attempt(self):
        report_id = self._submitted_report()
        first = self.reports.claim_specific(report_id)
        self.reports.fail_report(report_id=report_id, attempt_count=first.attempt_count, error="model down")
        self.assertEqual(self._report(report_id)["AgentStatus"], "FAILED")

        retry = self.reports.claim_specific(report_id)
        self.assertEqual(retry.attempt_count, first.attempt_count + 1)
        self.assertFalse(self.reports.complete_report(
            report_id=report_id, attempt_count=first.attempt_count, result=self._result(), context=None))

        self.assertTrue(self.reports.complete_report(
            report_id=report_id, attempt_count=retry.attempt_count, result=self._result(), context=None))
        self.reports.fail_report(report_id=report_id, attempt_count=first.attempt_count, error="late")
        state = self._report(report_id)
        self.assertEqual((state["Status"], state["AgentStatus"]), ("PENDING_REVIEW", "COMPLETED"))
        history = self.sql('''SELECT "AgentAttemptCount" FROM "dbo"."ReportAssessmentHistory" WHERE "ReportId"=%s''',
                           (report_id,)).fetchall()
        self.assertIn(retry.attempt_count, [h["AgentAttemptCount"] for h in history])
        self.assertNotIn(first.attempt_count, [h["AgentAttemptCount"] for h in history])

    def test_reviewed_report_cannot_be_reclaimed_or_overwritten(self):
        report_id = self._submitted_report()
        claimed = self.reports.claim_specific(report_id)
        self.assertTrue(self.reports.complete_report(
            report_id=report_id, attempt_count=claimed.attempt_count, result=self._result(), context=None))
        summary = self._report(report_id)["AiSummary"]
        self.assertIsNone(self.reports.claim_specific(report_id))
        self.assertFalse(self.reports.complete_report(
            report_id=report_id, attempt_count=claimed.attempt_count, result=self._result(), context=None))
        self.assertEqual(self._report(report_id)["AiSummary"], summary)

    def test_unknown_ids_are_no_ops(self):
        self.assertIsNone(self.reports.claim_specific(uuid4()))
        self.assertFalse(self.work_orders.complete_completion_assessment(
            self._completion_result(uuid4(), "X"), attempt_count=1))


if __name__ == "__main__":
    unittest.main()
