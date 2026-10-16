from __future__ import annotations

import re
import time

from ..config import Settings
from ..providers import OllamaProvider, ProviderError
from ..providers.base import InvalidModelOutput
from ..providers.gate import InferenceBusy
from ..schemas import (
    AssistantModelAnswer,
    AssistantNavigation,
    AssistantRequest,
    AssistantResponse,
    AssistantStatus,
)
from .prompts import ASSISTANT_PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt

MAX_NAVIGATION = 5
MAX_FALLBACK_LINES = 10
_URL = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]*://|www\.)\S+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class AssistantService:
    """Phrases an answer from API-supplied facts only. Never raises for model problems: it falls back."""

    def __init__(self, *, provider: OllamaProvider, settings: Settings) -> None:
        self.provider = provider
        self.settings = settings

    def respond(self, request: AssistantRequest) -> AssistantResponse:
        started = time.perf_counter()
        if not request.facts:
            # Nothing the caller may see matched; no model call, so nothing can be invented.
            return self._response(
                started, AssistantStatus.NO_FACTS,
                "SEEFIX found no records you can access for this question.", [], model=None,
            )
        try:
            raw = self.provider.answer_assistant(
                output_model=AssistantModelAnswer,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_user_prompt(request),
                timeout_seconds=self.settings.assistant_timeout_seconds,
                wait_seconds=self.settings.assistant_queue_wait_seconds,
                max_output_tokens=self.settings.assistant_max_output_tokens,
            )
        except InferenceBusy:
            return self._fallback(started, request, "MODEL_BUSY")
        except InvalidModelOutput:
            return self._fallback(started, request, "INVALID_MODEL_OUTPUT")
        except ProviderError:
            return self._fallback(started, request, "MODEL_UNAVAILABLE")
        except Exception as exc:  # the UI must still get a safe answer
            print(f"[ASSISTANT] Unexpected error, falling back: {str(exc)[:300]}", flush=True)
            return self._fallback(started, request, "MODEL_ERROR")

        known = {fact.id for fact in request.facts}
        cited = list(dict.fromkeys(i for i in raw.cited_fact_ids if i in known))
        if not cited:
            return self._fallback(started, request, "UNGROUNDED_ANSWER")
        answer = _CONTROL.sub("", _URL.sub("[link removed]", raw.answer)).strip()
        if not answer:
            return self._fallback(started, request, "INVALID_MODEL_OUTPUT")
        return self._response(started, AssistantStatus.ANSWERED, answer, cited, model=self.provider.model_id,
                              request=request)

    def _fallback(self, started: float, request: AssistantRequest, reason: str) -> AssistantResponse:
        lines = [f"- {fact.label}" for fact in request.facts[:MAX_FALLBACK_LINES]]
        more = len(request.facts) - len(lines)
        if more > 0:
            lines.append(f"- and {more} more")
        answer = (
            "The assistant could not write a summary right now. These are the records SEEFIX found for you:\n"
            + "\n".join(lines)
        )
        return self._response(
            started, AssistantStatus.FALLBACK, answer, [fact.id for fact in request.facts],
            model=None, request=request, fallback_reason=reason,
        )

    def _response(self, started, status, answer, cited, *, model, request=None, fallback_reason=None):
        facts = {fact.id: fact for fact in request.facts} if request else {}
        # Paths always come from the API's facts, never from model text.
        navigation = [
            AssistantNavigation(fact_id=i, path=facts[i].navigation, label=facts[i].label)
            for i in cited if facts[i].navigation
        ][:MAX_NAVIGATION]
        return AssistantResponse(
            status=status, answer=answer, cited_fact_ids=cited, navigation=navigation,
            fallback_reason=fallback_reason, model=model, prompt_version=ASSISTANT_PROMPT_VERSION,
            processing_time_ms=int((time.perf_counter() - started) * 1000),
        )
