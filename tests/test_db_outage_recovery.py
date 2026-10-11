"""Issue #5: PostgreSQL outages must not kill or flood the worker loops, and claims resume on recovery.

Network independent: repositories are fakes that raise DatabaseError, then recover.
"""
from __future__ import annotations

import contextlib
import io
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

import psycopg

from app.database import Database, DatabaseError
from app.workers.backoff import LoopBackoff
from app.workers.completion_worker import CompletionWorker
from app.workers.report_worker import ReportWorker

TIMEOUT = DatabaseError("Unable to connect to PostgreSQL: connection timeout expired")
SETTINGS = SimpleNamespace(completion_poll_seconds=0.01, completion_stale_minutes=15, agent_poll_seconds=0.01, agent_stale_minutes=15)


class FlakyRepository:
    """Raises `failures` times, then hands out `claims` once each, then None."""

    def __init__(self, failures: int, claims: list) -> None:
        self.failures = failures
        self.claims = list(claims)
        self.calls = 0
        self.lock = threading.Lock()

    def claim(self):
        with self.lock:
            self.calls += 1
            if self.failures:
                self.failures -= 1
                raise TIMEOUT
            return self.claims.pop(0) if self.claims else None

    # Startup stale recovery also hits the DB; it must fail safely during an outage.
    def requeue_stale_completion(self, _minutes):
        raise TIMEOUT

    requeue_stale = requeue_stale_completion


def run_until(worker, done, timeout=5.0):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        worker.start()
        deadline = time.monotonic() + timeout
        while not done() and time.monotonic() < deadline:
            time.sleep(0.005)
        running_during = worker.is_running
        worker.stop()
    return out.getvalue(), running_during


def fast(worker, name):
    worker._backoff = LoopBackoff(name, 0.001, 0.004)
    return worker


class LoopBackoffTests(unittest.TestCase):
    def test_wait_doubles_and_is_capped(self):
        b = LoopBackoff("T", 3, 60)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            delays = []
            for _ in range(8):
                try:
                    raise TIMEOUT
                except DatabaseError as exc:
                    delays.append(b.failed(exc))
        self.assertEqual(delays, [3, 6, 12, 24, 48, 60, 60, 60])

    def test_bounded_logging_and_single_recovery_line(self):
        b = LoopBackoff("T", 0.001)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            for _ in range(100):
                try:
                    raise TIMEOUT
                except DatabaseError as exc:
                    b.failed(exc)
            b.succeeded()
            b.succeeded()
        log = out.getvalue()
        self.assertEqual(log.count("Traceback"), 1, "only the first failure prints a traceback")
        # failures 1 (Loop error) + 2,4,8,16,32,64 (Still failing) = 7 lines for 100 failures
        self.assertEqual(log.count("Loop error") + log.count("Still failing"), 7)
        self.assertEqual(log.count("Recovered after 100 failed attempt(s)."), 1)
        self.assertEqual(b.failures, 0)


class CompletionWorkerOutageTests(unittest.TestCase):
    def make(self, repo):
        worker = CompletionWorker(settings=SETTINGS, repository=mock.Mock(), service=mock.Mock())
        worker.repository.claim_next_pending_completion.side_effect = repo.claim
        worker.repository.requeue_stale_completion.side_effect = repo.requeue_stale_completion
        processed = []
        worker._process = lambda wo_id, wo_no: processed.append(wo_id)
        return fast(worker, "COMPLETION WORKER"), processed

    def test_recovers_after_outage_and_claims_exactly_once(self):
        claim = SimpleNamespace(id=uuid4(), work_order_no="WO-1")
        repo = FlakyRepository(failures=12, claims=[claim])
        worker, processed = self.make(repo)
        log, running = run_until(worker, lambda: processed and repo.calls > 14)
        self.assertTrue(running, "thread stays alive through the outage")
        self.assertEqual(processed, [claim.id], "one claim after recovery, no duplicate")
        self.assertIn("Stale recovery failed safely", log)
        self.assertEqual(log.count("Traceback"), 1)
        self.assertIn("Recovered after 12 failed attempt(s).", log)
        worker.repository.fail_completion.assert_not_called()

    def test_shutdown_during_outage_is_prompt(self):
        repo = FlakyRepository(failures=10**9, claims=[])
        worker, _ = self.make(repo)
        worker._backoff = LoopBackoff("COMPLETION WORKER", 30, 60)  # long real backoff
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            worker.start()
            time.sleep(0.05)
            started = time.monotonic()
            worker.stop()
        self.assertLess(time.monotonic() - started, 1.0, "stop() wakes the backoff wait")
        self.assertFalse(worker.is_running)


class ReportWorkerOutageTests(unittest.TestCase):
    def test_recovers_after_outage_and_claims_exactly_once(self):
        claim = SimpleNamespace(id=uuid4(), report_no="RPT-1", attempt_count=1)
        repo = FlakyRepository(failures=12, claims=[claim])
        worker = ReportWorker(settings=SETTINGS, repository=mock.Mock(), agent=mock.Mock(), maintenance_request_workflow=mock.Mock())
        worker.repository.claim_next_pending.side_effect = repo.claim
        worker.repository.requeue_stale.side_effect = repo.requeue_stale
        processed = []
        worker._process = lambda c: processed.append(c.id)
        fast(worker, "AGENT WORKER")
        log, running = run_until(worker, lambda: processed and repo.calls > 14)
        self.assertTrue(running)
        self.assertEqual(processed, [claim.id])
        self.assertIn("Startup stale recovery failed safely", log)
        self.assertEqual(log.count("Traceback"), 1)
        self.assertIn("Recovered after 12 failed attempt(s).", log)
        worker.repository.fail_report.assert_not_called()


class DatabaseHealthTests(unittest.TestCase):
    """/health uses database.health(): false while unreachable, true again after recovery."""

    def test_health_degrades_and_recovers(self):
        db = Database(database_url="postgresql://u@db.invalid:5432/x", sslmode="prefer", sslrootcert="", connect_timeout_seconds=1, application_name="t")
        with mock.patch("app.database.psycopg.connect", side_effect=psycopg.errors.ConnectionTimeout("connection timeout expired")):
            self.assertFalse(db.health())
        cur = mock.MagicMock()
        cur.__enter__.return_value.fetchone.return_value = (1,)
        conn = mock.MagicMock()
        conn.__enter__.return_value.cursor.return_value = cur
        with mock.patch("app.database.psycopg.connect", return_value=conn):
            self.assertTrue(db.health())


if __name__ == "__main__":
    unittest.main()
