from __future__ import annotations

import queue
import threading
import traceback
from uuid import UUID

from ..agent import FacilityInspectionAgent
from ..config import Settings
from ..maintenance_request.workflow import MaintenanceRequestWorkflow
from ..remote_image import download_remote_image
from ..repositories.reports import ClaimedReport, ReportRepository
from ..schemas import AssessmentDatabaseContext
from ..scoring import PriorityContext
from .backoff import LoopBackoff


class ReportWorker:
    """Durable PostgreSQL-backed initial Report Agent worker."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: ReportRepository,
        agent: FacilityInspectionAgent,
        maintenance_request_workflow: MaintenanceRequestWorkflow,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.agent = agent
        self.maintenance_request_workflow = maintenance_request_workflow
        self._requested: queue.Queue[ClaimedReport] = queue.Queue()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._backoff = LoopBackoff("AGENT WORKER", settings.agent_poll_seconds)

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        try:
            requeued = self.repository.requeue_stale(self.settings.agent_stale_minutes)
            if requeued:
                print(f"[AGENT WORKER] Requeued {requeued} stale report(s).", flush=True)
        except Exception as exc:
            print(f"[AGENT WORKER] Startup stale recovery failed safely: {str(exc)[:500]}", flush=True)
        self._thread = threading.Thread(target=self._run, name="seefix-report-worker", daemon=True)
        self._thread.start()
        print("[AGENT WORKER] Started.", flush=True)

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def submit(self, report_id: UUID) -> ClaimedReport | None:
        """Claim immediately, then queue the already-claimed Report.

        Claiming synchronously makes the durable PostgreSQL state PROCESSING
        before the HTTP retry endpoint responds, so callers never receive the
        stale FAILED/PENDING state after an accepted retry.
        """
        claimed = self.repository.claim_specific(report_id)
        if claimed is None:
            return None
        self._requested.put(claimed)
        self._wake_event.set()
        return claimed

    def wake(self) -> None:
        self._wake_event.set()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                claimed = self._next_claim()
                self._backoff.succeeded()
                if claimed is None:
                    self._wake_event.wait(timeout=self.settings.agent_poll_seconds)
                    self._wake_event.clear()
                    continue
                self._process(claimed)
            except Exception as exc:
                # Thread stays alive; claims resume on their own once PostgreSQL is reachable (#5).
                self._wake_event.wait(timeout=self._backoff.failed(exc))
                self._wake_event.clear()

    def _next_claim(self) -> ClaimedReport | None:
        try:
            claimed = self._requested.get_nowait()
        except queue.Empty:
            claimed = None
        if claimed is not None:
            return claimed
        return self.repository.claim_next_pending()

    def _download(self, url: str) -> bytes:
        return download_remote_image(
            url,
            timeout_seconds=self.settings.image_download_timeout_seconds,
            max_bytes=self.settings.max_upload_mb * 1024 * 1024,
            allowed_hosts=self.settings.allowed_image_hosts,
            max_redirects=self.settings.image_max_redirects,
        )

    def _process(self, claimed: ClaimedReport) -> None:
        print(
            f"[AGENT WORKER] Processing {claimed.report_no} ({claimed.id}), attempt {claimed.attempt_count}.",
            flush=True,
        )
        try:
            # A report may have been cancelled after the DB claim but before
            # this worker picked the in-memory queue item.
            if not self._still_eligible(claimed):
                print(f"[AGENT WORKER] Skipping closed/cancelled {claimed.report_no}.", flush=True)
                return
            bundle = self.repository.get_bundle(claimed.id)
            image_bytes = self._download(bundle.images[0].secure_url)
            if not self._still_eligible(claimed):
                return
            result = self.agent.analyze(image_bytes, report_context=bundle.prompt_context())
            context: AssessmentDatabaseContext | None = None

            if result.assessment is not None:
                context = self.repository.get_assessment_context(
                    bundle=bundle,
                    category=result.assessment.category,
                )
                priority_context = PriorityContext(
                    recurrence_count=context.recurrence_count,
                    verification_count=context.verification_count,
                    age_days=context.age_days,
                    recurrence_threshold=context.recurrence_threshold,
                    verification_threshold=context.verification_threshold,
                )
                duration_reference = self._duration_reference(context)
                result = self.agent.apply_database_context(
                    result,
                    priority_context=priority_context,
                    category_reference=context.category_reference,
                    duration_reference=duration_reference,
                )

            saved_assessment = self.repository.complete_report(
                report_id=claimed.id, attempt_count=claimed.attempt_count,
                result=result, context=context,
            )
            if not saved_assessment:
                print(f"[AGENT WORKER] Stale/cancelled attempt discarded: {claimed.report_no}.", flush=True)
                return

            # A valid visual assessment always gets a DRAFT. Qwen second-pass failure
            # is handled internally by a deterministic fallback.
            if result.assessment is not None and context is not None:
                # After completing assessment, status becomes PENDING_REVIEW.
                # Avoid launching the expensive second-pass draft when a
                # cancellation/review has already changed that state.
                latest = self.repository.get_state(claimed.id)
                if not latest or latest["Status"] != "PENDING_REVIEW":
                    print(f"[AGENT WORKER] Draft skipped after report state change: {claimed.report_no}.", flush=True)
                    return
                try:
                    saved = self.maintenance_request_workflow.generate_for_report(
                        report_id=claimed.id,
                        result=result,
                        context=context,
                    )
                    print(
                        f"[AGENT WORKER] Maintenance Request {saved['RequestNo']} status={saved['Status']}.",
                        flush=True,
                    )
                except Exception as request_exc:
                    # The visual assessment is authoritative and must not be lost because
                    # a later request-drafting/persistence operation failed.
                    print(
                        f"[AGENT WORKER] Assessment completed, but Maintenance Request DRAFT failed: {str(request_exc)[:700]}",
                        flush=True,
                    )
                    traceback.print_exc()

            print(f"[AGENT WORKER] Completed {claimed.report_no}.", flush=True)
        except Exception as exc:
            print(f"[AGENT WORKER] FAILED {claimed.report_no}: {str(exc)[:700]}", flush=True)
            traceback.print_exc()
            try:
                self.repository.fail_report(
                    report_id=claimed.id, attempt_count=claimed.attempt_count,
                    error=str(exc),
                )
            except Exception as save_error:
                print(f"[AGENT WORKER] Unable to store FAILED state: {str(save_error)[:500]}", flush=True)

    def _still_eligible(self, claimed: ClaimedReport) -> bool:
        state = self.repository.get_state(claimed.id)
        return bool(
            state and state["Status"] == "SUBMITTED"
            and state["AgentStatus"] == "PROCESSING"
            and int(state["AgentAttemptCount"]) == claimed.attempt_count
        )

    @staticmethod
    def _duration_reference(context: AssessmentDatabaseContext) -> str | None:
        historical = context.historical_duration
        if historical is None:
            return None
        return (
            "Historical completed-work-order reference: "
            f"{historical.sample_count} matching record(s), "
            f"median {historical.median_hours:.1f} hours, average {historical.average_hours:.1f} hours."
        )
