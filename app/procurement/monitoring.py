from __future__ import annotations

import threading
import traceback

from ..repositories.procurement import ProcurementRepository


class WorkflowMonitor:
    def __init__(self, *, repository: ProcurementRepository, interval_seconds: int, enabled: bool = True) -> None:
        self.repository = repository
        self.interval_seconds = interval_seconds
        self.enabled = enabled
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive()) if self.enabled else False

    def start(self) -> None:
        if not self.enabled or self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="seefix-workflow-monitor", daemon=True)
        self._thread.start()
        print("[WORKFLOW MONITOR] Started.", flush=True)

    def stop(self) -> None:
        if not self.enabled:
            return
        self._stop_event.set()
        self._wake_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def run_once(self) -> dict[str, int]:
        return self.repository.run_monitor()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                counts = self.run_once()
                if any(counts.values()):
                    print(f"[WORKFLOW MONITOR] Created action items: {counts}", flush=True)
            except Exception as exc:
                print(f"[WORKFLOW MONITOR] Run failed safely: {str(exc)[:500]}", flush=True)
                traceback.print_exc()
            self._wake_event.wait(timeout=self.interval_seconds)
            self._wake_event.clear()
