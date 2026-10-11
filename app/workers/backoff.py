from __future__ import annotations

import traceback


class LoopBackoff:
    """Transient-error handling for a polling worker loop (e.g. PostgreSQL unreachable).

    The thread keeps running; the wait doubles from base_seconds up to cap_seconds. Only the
    first failure prints a traceback, later ones print one line at 2, 4, 8... consecutive
    failures, and the first success after an outage prints one recovery line.
    """

    def __init__(self, name: str, base_seconds: float, cap_seconds: float = 60.0) -> None:
        self.name = name
        self.base_seconds = base_seconds
        self.cap_seconds = max(cap_seconds, base_seconds)
        self.failures = 0

    def failed(self, exc: BaseException) -> float:
        """Call from inside the except block; returns how long to wait before retrying."""
        self.failures += 1
        message = str(exc)[:500]
        if self.failures == 1:
            print(f"[{self.name}] Loop error: {message}", flush=True)
            traceback.print_exc()
        elif self.failures & (self.failures - 1) == 0:
            print(f"[{self.name}] Still failing ({self.failures} consecutive): {message}", flush=True)
        return min(self.base_seconds * 2 ** (self.failures - 1), self.cap_seconds)

    def succeeded(self) -> None:
        if self.failures:
            print(f"[{self.name}] Recovered after {self.failures} failed attempt(s).", flush=True)
            self.failures = 0
