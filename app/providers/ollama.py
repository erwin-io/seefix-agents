from __future__ import annotations

import base64
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..json_utils import extract_json_object
from ..prompts import SYSTEM_PROMPT, build_user_prompt
from ..schemas import ModelInspectionResponse, ProviderAnalysis
from .base import ModelProvider, ProviderError


class OllamaProvider(ModelProvider):
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

    def analyze(self, image_bytes: bytes) -> ProviderAnalysis:
        schema = self._compact_schema(ModelInspectionResponse.model_json_schema())
        payload = {
            "model": self.model_id,
            "stream": False,
            "think": False,
            "format": schema,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": 0,
                "num_ctx": self.model_context,
                "num_predict": self.max_output_tokens,
            },
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_user_prompt(),
                    "images": [base64.b64encode(image_bytes).decode("ascii")],
                },
            ],
        }
        body = self._chat(payload)
        self._print_timing(body)

        try:
            content = body["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("message.content was empty")
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                f"Ollama returned an invalid API response envelope: {exc}"
            ) from exc

        try:
            return self._validate_content(content)
        except (TypeError, ValueError) as exc:
            print(
                f"[OLLAMA JSON] Initial response was invalid ({exc}); "
                "running one text-only repair attempt.",
                flush=True,
            )

        repair_payload = {
            "model": self.model_id,
            "stream": False,
            "think": False,
            "format": schema,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": 0,
                "num_ctx": self.model_context,
                "num_predict": self.max_output_tokens,
            },
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Repair the JSON below. Preserve its assessment values, fill only "
                        "required missing fields conservatively, and return only one valid "
                        "JSON object matching the required response format.\n\n"
                        f"{content}"
                    ),
                }
            ],
        }
        repaired_body = self._chat(repair_payload)
        try:
            self._print_timing(repaired_body, label="OLLAMA REPAIR TIMING")
            repaired_content = repaired_body["message"]["content"]
            return self._validate_content(repaired_content)
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                "Ollama returned malformed structured JSON after one automatic repair "
                f"attempt: {exc}"
            ) from exc

    def _chat(self, payload: dict) -> dict:
        request = Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise ProviderError(
                f"Cannot connect to Ollama at {self.base_url}. Is Ollama running?"
            ) from exc
        except TimeoutError as exc:
            raise ProviderError(
                f"Ollama exceeded the {self.timeout_seconds}-second timeout. "
                "Run 'ollama ps' in another terminal while analyzing. If PROCESSOR "
                "shows 100% CPU, use the 2B model or enable supported GPU acceleration."
            ) from exc

    @staticmethod
    def _validate_content(content: str) -> ProviderAnalysis:
        data = extract_json_object(content)
        model_result = ModelInspectionResponse.model_validate(data)
        return model_result.to_provider_result()

    @staticmethod
    def _compact_schema(value):
        if isinstance(value, dict):
            return {
                key: OllamaProvider._compact_schema(item)
                for key, item in value.items()
                if key not in {"title", "description", "default"}
            }
        if isinstance(value, list):
            return [OllamaProvider._compact_schema(item) for item in value]
        return value

    @staticmethod
    def _print_timing(body: dict, label: str = "OLLAMA TIMING") -> None:
        def seconds(field: str) -> float:
            return round(float(body.get(field, 0)) / 1_000_000_000, 2)

        eval_count = int(body.get("eval_count", 0) or 0)
        eval_seconds = seconds("eval_duration")
        tokens_per_second = round(eval_count / eval_seconds, 2) if eval_seconds else 0
        print(
            f"[{label}] "
            f"total={seconds('total_duration')}s "
            f"load={seconds('load_duration')}s "
            f"prompt/image={seconds('prompt_eval_duration')}s "
            f"generation={eval_seconds}s "
            f"output_tokens={eval_count} "
            f"speed={tokens_per_second} tokens/s",
            flush=True,
        )
