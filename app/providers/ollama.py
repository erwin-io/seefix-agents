from __future__ import annotations

import base64
import copy
import json
import re
from typing import TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ValidationError

from ..inspection.prompts import SYSTEM_PROMPT as INSPECTION_SYSTEM_PROMPT, build_user_prompt
from ..json_utils import extract_json_object
from ..maintenance_request.prompts import SYSTEM_PROMPT as MAINTENANCE_SYSTEM_PROMPT, build_prompt as build_maintenance_prompt
from ..schemas import (
    CompletionModelResponse,
    MaintenanceRequestModelDraft,
    ModelInspectionResponse,
    ProcurementClarificationModelDraft,
    ProviderAnalysis,
)
from ..work_order.prompts import COMPLETION_SYSTEM_PROMPT, build_completion_prompt
from .base import ProviderError

T = TypeVar("T", bound=BaseModel)


# Structured JSON plus Qwen/VLM image tokens can easily consume a 2K context
# before the model has enough room to emit a complete response. Keep a safe
# floor for every typed SEEFIX call even when an older .env still says 2048.
MIN_STRUCTURED_CONTEXT = 4096
CONTEXT_RETRY_CEILING = 16384
CONTEXT_OUTPUT_RESERVE = 256

REPAIR_SYSTEM_PROMPT = """You repair one SEEFIX JSON response.
Preserve the meaning of the previous response and do not invent new facts.
Return exactly one complete JSON object matching the supplied schema.
JSON only; no Markdown or commentary."""


class OllamaProvider:
    """Local-only Qwen provider through Ollama with typed structured output."""

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: int,
        model_context: int,
        max_output_tokens: int,
        keep_alive: str,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model_id = model
        self.timeout_seconds = timeout_seconds
        self.model_context = model_context
        self.max_output_tokens = max_output_tokens
        self.keep_alive = keep_alive

    def analyze(self, image_bytes: bytes, *, report_context: dict | None = None) -> ProviderAnalysis:
        result = self._structured(
            output_model=ModelInspectionResponse,
            system_prompt=INSPECTION_SYSTEM_PROMPT,
            user_prompt=build_user_prompt(report_context),
            images=[image_bytes],
            repair_context=report_context or {},
            label="OLLAMA INSPECTION",
        )
        return result.to_provider_result()

    def draft_maintenance_request(self, context: dict) -> MaintenanceRequestModelDraft:
        return self._structured(
            output_model=MaintenanceRequestModelDraft,
            system_prompt=MAINTENANCE_SYSTEM_PROMPT,
            user_prompt=build_maintenance_prompt(context),
            images=[],
            repair_context=context,
            label="OLLAMA MAINTENANCE REQUEST",
        )

    def draft_procurement_clarification(self, context: dict) -> ProcurementClarificationModelDraft:
        system_prompt = """You draft a PPO-reviewable response to a Procurement clarification using only recorded SEEFIX facts.
Do not invent field observations, silently change scope, approve Procurement, select providers, or make authoritative engineering certifications.
Identify uncertainty and set the structured field field_inspection_required=true when recorded facts cannot answer safely.
Do not write field_inspection_required=true/false or any other schema field assignment inside response_draft.
If the prose says a field inspection is required, the structured field must also be true. If the prose explicitly says no field inspection is required, the structured field must be false.
The response is a draft only; PPO Head reviews and sends the final response.
Return JSON only matching the supplied schema."""
        return self._structured(
            output_model=ProcurementClarificationModelDraft,
            system_prompt=system_prompt,
            user_prompt=json.dumps(context, ensure_ascii=False, indent=2, default=str),
            images=[],
            repair_context=context,
            label="OLLAMA PROCUREMENT CLARIFICATION",
        )

    def compare_completion(self, images: list[bytes], context: dict) -> CompletionModelResponse:
        if len(images) < 2:
            raise ProviderError("Completion comparison requires original and completion images.")
        return self._structured(
            output_model=CompletionModelResponse,
            system_prompt=COMPLETION_SYSTEM_PROMPT,
            user_prompt=build_completion_prompt(context),
            images=images,
            repair_context=context,
            label="OLLAMA COMPLETION",
        )

    def ping(self) -> dict:
        request = Request(f"{self.base_url}/api/tags", method="GET")
        try:
            with urlopen(request, timeout=min(self.timeout_seconds, 5)) as response:
                body = json.loads(response.read().decode("utf-8"))
            models = body.get("models", []) if isinstance(body, dict) else []
            names = {str(item.get("name", "")) for item in models if isinstance(item, dict)}
            return {
                "reachable": True,
                "model_available": self.model_id in names,
                "model": self.model_id,
            }
        except Exception as exc:  # health endpoint must remain bounded
            return {
                "reachable": False,
                "model_available": False,
                "model": self.model_id,
                "error": str(exc)[:300],
            }

    def _structured(
        self,
        *,
        output_model: type[T],
        system_prompt: str,
        user_prompt: str,
        images: list[bytes],
        repair_context: dict,
        label: str,
    ) -> T:
        schema = self._compact_schema(output_model.model_json_schema())
        encoded_images = [base64.b64encode(value).decode("ascii") for value in images]
        payload = self._build_payload(
            schema=schema,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            encoded_images=encoded_images,
        )
        body = self._chat(payload)
        self._print_timing(body, label=label)
        content = self._extract_content(body)

        # Keep the first validation exception in a variable that survives the
        # ``except`` block. Python deliberately clears the name bound by
        # ``except ... as exc`` when the block exits, so referring to that
        # exception variable later raises UnboundLocalError.
        first_validation_error: Exception | None = None
        try:
            return self._validate_content(content, output_model)
        except (TypeError, ValueError, ValidationError) as exc:
            first_validation_error = exc
            print(
                f"[{label}] Initial structured response failed validation; running one corrective attempt. "
                f"Reason: {str(exc)[:500]}",
                flush=True,
            )

        # This is guaranteed by the except branch above, but keeping the
        # explicit guard makes the invariant clear and keeps type checkers
        # happy.
        if first_validation_error is None:  # pragma: no cover - defensive
            raise ProviderError("Structured-output validation failed without a captured validation error.")

        # The repair pass deliberately does NOT resend the image, original
        # task, or database context. The schema and previous JSON are enough
        # to repair syntax/shape, while omitting duplicated material prevents
        # the corrective request from overflowing the context window.
        repair_prompt = self._build_repair_prompt(
            invalid_content=content,
            validation_error=first_validation_error,
        )
        repair_payload = self._build_payload(
            schema=schema,
            system_prompt=REPAIR_SYSTEM_PROMPT,
            user_prompt=repair_prompt,
            encoded_images=[],
        )
        repaired_body = self._chat(repair_payload)
        self._print_timing(repaired_body, label=f"{label} REPAIR")
        repaired_content = self._extract_content(repaired_body)
        try:
            return self._validate_content(repaired_content, output_model)
        except (TypeError, ValueError, ValidationError) as repair_error:
            raise ProviderError(
                "Ollama returned structured output that failed SEEFIX validation after one corrective attempt. "
                f"Initial: {str(first_validation_error)[:500]}. Repair: {str(repair_error)[:500]}"
            ) from repair_error

    def _build_payload(
        self,
        *,
        schema: dict,
        system_prompt: str,
        user_prompt: str,
        encoded_images: list[str],
    ) -> dict:
        user_message: dict = {"role": "user", "content": user_prompt}
        if encoded_images:
            user_message["images"] = encoded_images
        return {
            "model": self.model_id,
            "stream": False,
            "think": False,
            "format": schema,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": 0,
                # Older SEEFIX .env files used 2048. That is too small for
                # Qwen3-VL structured image analysis because schema + image
                # tokens must share the same context with generated JSON.
                "num_ctx": max(self.model_context, MIN_STRUCTURED_CONTEXT),
                "num_predict": self.max_output_tokens,
            },
            "messages": [
                {"role": "system", "content": system_prompt},
                user_message,
            ],
        }

    @staticmethod
    def _build_repair_prompt(
        *,
        invalid_content: str,
        validation_error: Exception,
    ) -> str:
        # Keep corrective input intentionally small. The Pydantic schema is
        # already supplied through Ollama's ``format`` field. Repeating the
        # full original prompt, report context, and image caused the old
        # 2048-token repair request to overflow.
        error_text = str(validation_error)[:700]
        invalid_text = invalid_content[:2600]
        return f"""Correct the previous JSON so it validates against the supplied schema.
Do not add facts that were not present. Return one complete JSON object only.

VALIDATION ERROR:
{error_text}

PREVIOUS JSON:
{invalid_text}
"""

    def _chat(self, payload: dict) -> dict:
        # Work on a copy because context auto-expansion must not mutate the
        # caller's payload or leak into later requests.
        request_payload = copy.deepcopy(payload)

        for context_retry in range(3):
            request = Request(
                f"{self.base_url}/api/chat",
                data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = response.read().decode("utf-8")
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise ProviderError("Ollama returned a non-object API response.")

                # Ollama can finish with ``length`` when prompt + generated
                # tokens consume the current context. If that happened near
                # the context boundary, automatically retry with more room.
                if self._response_hit_context_limit(body, request_payload):
                    if self._increase_context(
                        request_payload,
                        prompt_tokens=int(body.get("prompt_eval_count", 0) or 0),
                    ):
                        print(
                            "[OLLAMA] Response reached context boundary; "
                            f"retrying with num_ctx={request_payload['options']['num_ctx']}.",
                            flush=True,
                        )
                        continue
                return body

            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:4000]
                overflow = self._parse_context_overflow(detail)
                if overflow is not None and self._increase_context(
                    request_payload,
                    prompt_tokens=overflow.get("n_prompt_tokens", 0),
                ):
                    print(
                        "[OLLAMA] Context request exceeded current window; "
                        f"retrying with num_ctx={request_payload['options']['num_ctx']}.",
                        flush=True,
                    )
                    continue
                raise ProviderError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
            except URLError as exc:
                raise ProviderError(
                    f"Cannot connect to Ollama at {self.base_url}. Make sure the local Ollama service is running."
                ) from exc
            except TimeoutError as exc:
                raise ProviderError(f"Ollama exceeded the {self.timeout_seconds}-second timeout.") from exc
            except json.JSONDecodeError as exc:
                raise ProviderError("Ollama returned an invalid JSON API envelope.") from exc

        raise ProviderError(
            "Ollama could not complete the request within the bounded context retry policy."
        )

    def _increase_context(self, payload: dict, *, prompt_tokens: int) -> bool:
        options = payload.setdefault("options", {})
        current = int(options.get("num_ctx", self.model_context) or self.model_context)
        output_tokens = int(options.get("num_predict", self.max_output_tokens) or self.max_output_tokens)
        required = max(
            current * 2,
            int(prompt_tokens or 0) + output_tokens + CONTEXT_OUTPUT_RESERVE,
            MIN_STRUCTURED_CONTEXT,
        )
        next_context = min(self._round_context(required), CONTEXT_RETRY_CEILING)
        if next_context <= current:
            return False
        options["num_ctx"] = next_context
        return True

    @staticmethod
    def _round_context(value: int) -> int:
        # Round to a predictable 1024-token boundary rather than growing by
        # arbitrary small amounts on repeated retries.
        step = 1024
        return ((max(value, step) + step - 1) // step) * step

    @staticmethod
    def _parse_context_overflow(detail: str) -> dict | None:
        if "exceed_context_size_error" not in detail and "exceeds the available context size" not in detail:
            return None

        # Ollama may return the actual error object as a JSON-encoded string
        # inside an outer ``error`` field. Regex fallback keeps this tolerant
        # across Ollama versions.
        prompt_match = re.search(r'\"?n_prompt_tokens\"?\s*:\s*(\d+)', detail)
        ctx_match = re.search(r'\"?n_ctx\"?\s*:\s*(\d+)', detail)
        return {
            "n_prompt_tokens": int(prompt_match.group(1)) if prompt_match else 0,
            "n_ctx": int(ctx_match.group(1)) if ctx_match else 0,
        }

    @staticmethod
    def _response_hit_context_limit(body: dict, payload: dict) -> bool:
        if str(body.get("done_reason", "")).lower() != "length":
            return False
        prompt_tokens = int(body.get("prompt_eval_count", 0) or 0)
        output_tokens = int(body.get("eval_count", 0) or 0)
        context = int(payload.get("options", {}).get("num_ctx", 0) or 0)
        # Only treat this as a context problem when the response ended close
        # to the context boundary. A pure num_predict limit remains bounded by
        # the configured output-token cap and is handled by schema validation.
        return bool(context and prompt_tokens + output_tokens >= context - 64)

    @staticmethod
    def _extract_content(body: dict) -> str:
        try:
            content = body["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise ProviderError("Ollama returned an invalid API response envelope.") from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("Ollama returned empty message content.")
        return content

    @staticmethod
    def _validate_content(content: str, output_model: type[T]) -> T:
        data = extract_json_object(content)
        return output_model.model_validate(data)

    @staticmethod
    def _compact_schema(value):
        metadata_fields = {"title", "description", "default", "examples"}
        if isinstance(value, dict):
            return {
                key: OllamaProvider._compact_schema(item)
                for key, item in value.items()
                if key not in metadata_fields
            }
        if isinstance(value, list):
            return [OllamaProvider._compact_schema(item) for item in value]
        return value

    @staticmethod
    def _print_timing(body: dict, label: str = "OLLAMA TIMING") -> None:
        def seconds(field: str) -> float:
            return round(float(body.get(field, 0) or 0) / 1_000_000_000, 2)

        eval_count = int(body.get("eval_count", 0) or 0)
        eval_seconds = seconds("eval_duration")
        tokens_per_second = round(eval_count / eval_seconds, 2) if eval_seconds else 0
        print(
            f"[{label}] total={seconds('total_duration')}s "
            f"load={seconds('load_duration')}s "
            f"prompt/image={seconds('prompt_eval_duration')}s "
            f"generation={eval_seconds}s output_tokens={eval_count} "
            f"speed={tokens_per_second} tokens/s",
            flush=True,
        )
