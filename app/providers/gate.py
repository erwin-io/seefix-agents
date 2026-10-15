from __future__ import annotations

import threading
from contextlib import contextmanager

from .base import ProviderError


class InferenceBusy(ProviderError):
    """The assistant could not get a model slot in time; callers answer with a safe fallback."""


class InferenceGate:
    """Admission control for the single local Ollama host (seefix-agents#7).

    Queue jobs (report and completion assessments, drafts) always get the next free slot and never wait
    behind a chat. An interactive assistant call is admitted only when a slot is free and no job is
    waiting; it gives up after `wait_seconds`, and at most `max_waiting_interactive` chats may wait.
    """

    def __init__(self, *, slots: int = 1, max_waiting_interactive: int = 4) -> None:
        self._cond = threading.Condition()
        self._free = max(1, slots)
        self._jobs_waiting = 0
        self._interactive_waiting = 0
        self.max_waiting_interactive = max_waiting_interactive

    @contextmanager
    def job(self):
        with self._cond:
            self._jobs_waiting += 1
            try:
                self._cond.wait_for(lambda: self._free > 0)
            finally:
                self._jobs_waiting -= 1
            self._free -= 1
        try:
            yield
        finally:
            self._release()

    @contextmanager
    def interactive(self, wait_seconds: float):
        with self._cond:
            if self._interactive_waiting >= self.max_waiting_interactive:
                raise InferenceBusy("Assistant queue is full.")
            self._interactive_waiting += 1
            try:
                admitted = self._cond.wait_for(
                    lambda: self._free > 0 and self._jobs_waiting == 0, timeout=wait_seconds
                )
            finally:
                self._interactive_waiting -= 1
            if not admitted:
                raise InferenceBusy("Model is busy with queued assessments.")
            self._free -= 1
        try:
            yield
        finally:
            self._release()

    def _release(self) -> None:
        with self._cond:
            self._free += 1
            self._cond.notify_all()
