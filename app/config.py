from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except ImportError:  # Core tests can run before optional web dependencies are installed.
    load_dotenv = None


if load_dotenv:
    load_dotenv()


@dataclass(frozen=True)
class Settings:
    runtime: str = os.getenv("SEEFIX_RUNTIME", "local").strip().lower()
    provider: str = os.getenv("SEEFIX_PROVIDER", "mock").strip().lower()
    ollama_base_url: str = os.getenv(
        "SEEFIX_OLLAMA_BASE_URL", "http://127.0.0.1:11434"
    ).rstrip("/")
    ollama_model: str = os.getenv(
        "SEEFIX_OLLAMA_MODEL",
        "qwen3-vl:2b-instruct-q4_K_M",
    )
    request_timeout_seconds: int = int(
        os.getenv("SEEFIX_REQUEST_TIMEOUT_SECONDS", "180")
    )
    max_upload_mb: int = int(os.getenv("SEEFIX_MAX_UPLOAD_MB", "10"))
    max_image_dimension: int = int(
        os.getenv("SEEFIX_MAX_IMAGE_DIMENSION", "512")
    )
    model_context: int = int(os.getenv("SEEFIX_MODEL_CONTEXT", "1536"))
    model_max_output_tokens: int = max(
        512, int(os.getenv("SEEFIX_MODEL_MAX_OUTPUT_TOKENS", "512"))
    )
    model_keep_alive: str = os.getenv("SEEFIX_MODEL_KEEP_ALIVE", "15m").strip()
    huggingface_model: str = os.getenv(
        "SEEFIX_HUGGINGFACE_MODEL",
        "Qwen/Qwen3-VL-2B-Instruct",
    ).strip()
    huggingface_max_output_tokens: int = max(
        512, int(os.getenv("SEEFIX_HUGGINGFACE_MAX_OUTPUT_TOKENS", "512"))
    )


settings = Settings()
