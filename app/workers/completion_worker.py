from __future__ import annotations

import threading
import traceback

from ..config import Settings
from ..remote_image import download_remote_image
from ..repositories.work_orders import WorkOrderRepository
from ..work_order.completion import CompletionAssessmentService
from .backoff import LoopBackoff


class CompletionWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: WorkOrderRepository,
        service: CompletionAssessmentService,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.service = service
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._backoff = LoopBackoff("COMPLETION WORKER", settings.completion_poll_seconds)

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        try:
            requeued = self.repository.requeue_stale_completion(self.settings.completion_stale_minutes)
            if requeued:
                print(f"[COMPLETION WORKER] Requeued {requeued} stale work order(s).", flush=True)
        except Exception as exc:
            print(f"[COMPLETION WORKER] Stale recovery failed safely: {str(exc)[:500]}", flush=True)
        self._thread = threading.Thread(target=self._run, name="seefix-completion-worker", daemon=True)
        self._thread.start()
        print("[COMPLETION WORKER] Started.", flush=True)

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def wake(self) -> None:
        self._wake_event.set()

    def _download(self, url: str) -> bytes:
        return download_remote_image(
            url,
            timeout_seconds=self.settings.image_download_timeout_seconds,
            max_bytes=self.settings.max_upload_mb * 1024 * 1024,
            allowed_hosts=self.settings.allowed_image_hosts,
            max_redirects=self.settings.image_max_redirects,
        )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                claimed = self.repository.claim_next_pending_completion()
                self._backoff.succeeded()
                if claimed is None:
                    self._wake_event.wait(timeout=self.settings.completion_poll_seconds)
                    self._wake_event.clear()
                    continue
                self._process(claimed.id, claimed.work_order_no)
            except Exception as exc:
                # Thread stays alive; claims resume on their own once PostgreSQL is reachable (#5).
                self._wake_event.wait(timeout=self._backoff.failed(exc))
                self._wake_event.clear()

    def _process(self, work_order_id, work_order_no: str) -> None:
        try:
            bundle = self.repository.get_completion_bundle(work_order_id)
            original = self._download(bundle.original_image_url)
            completion_images = [self._download(url) for url in bundle.completion_image_urls[:4]]
            result = self.service.assess(bundle=bundle, images=[original, *completion_images])
            self.repository.complete_completion_assessment(result)
            print(f"[COMPLETION WORKER] Assessed {work_order_no}; Maintenance Supervisor review still required.", flush=True)
        except Exception as exc:
            print(f"[COMPLETION WORKER] FAILED {work_order_no}: {str(exc)[:700]}", flush=True)
            traceback.print_exc()
            try:
                self.repository.fail_completion(work_order_id=work_order_id, error=str(exc))
            except Exception as save_error:
                print(f"[COMPLETION WORKER] Unable to store FAILED state: {str(save_error)[:500]}", flush=True)
