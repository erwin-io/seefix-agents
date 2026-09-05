from __future__ import annotations

import json
from io import BytesIO

from PIL import Image

from ..json_utils import extract_json_object
from ..prompts import SYSTEM_PROMPT, build_user_prompt
from ..schemas import ModelInspectionResponse, ProviderAnalysis
from .base import ModelProvider, ProviderError


class HuggingFaceProvider(ModelProvider):
    """Runs the production vision-language model with Transformers."""

    name = "huggingface"

    def __init__(
        self,
        *,
        model: str,
        max_output_tokens: int,
        device: str = "cuda",
    ) -> None:
        self.model_id = model
        self.max_output_tokens = max_output_tokens

        try:
            import torch
            from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

            self._torch = torch
            self._processor = AutoProcessor.from_pretrained(self.model_id)
            self._model = Qwen3VLForConditionalGeneration.from_pretrained(
                self.model_id,
                dtype=torch.bfloat16,
                low_cpu_mem_usage=True,
                attn_implementation="sdpa",
            )
            self._model.to(device)
            self._model.eval()
        except Exception as exc:
            raise ProviderError(
                f"Unable to load Hugging Face model '{self.model_id}': {exc}"
            ) from exc

    def analyze(self, image_bytes: bytes) -> ProviderAnalysis:
        try:
            with Image.open(BytesIO(image_bytes)) as source:
                image = source.convert("RGB")

            schema = json.dumps(
                ModelInspectionResponse.model_json_schema(),
                separators=(",", ":"),
            )
            user_prompt = (
                f"{build_user_prompt()}\n"
                "Return only valid JSON matching this schema:\n"
                f"{schema}"
            )
            messages = [
                {
                    "role": "system",
                    "content": [{"type": "text", "text": SYSTEM_PROMPT}],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": image},
                        {"type": "text", "text": user_prompt},
                    ],
                },
            ]

            inputs = self._processor.apply_chat_template(
                messages,
                tokenize=True,
                add_generation_prompt=True,
                return_dict=True,
                return_tensors="pt",
            )
            inputs = inputs.to(self._model.device)
            inputs.pop("token_type_ids", None)

            with self._torch.inference_mode():
                generated_ids = self._model.generate(
                    **inputs,
                    max_new_tokens=self.max_output_tokens,
                    do_sample=False,
                    use_cache=True,
                )

            response_ids = [
                output_ids[len(input_ids) :]
                for input_ids, output_ids in zip(inputs.input_ids, generated_ids)
            ]
            response_text = self._processor.batch_decode(
                response_ids,
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False,
            )[0]

            data = extract_json_object(response_text)
            model_result = ModelInspectionResponse.model_validate(data)
            return model_result.to_provider_result()
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                f"Hugging Face inference returned an invalid assessment: {exc}"
            ) from exc
