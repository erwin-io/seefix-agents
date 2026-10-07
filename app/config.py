from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


if load_dotenv:
    load_dotenv()


VALID_SSL_MODES = {
    "disable",
    "allow",
    "prefer",
    "require",
    "verify-ca",
    "verify-full",
}


def _env_bool(
    name: str,
    default: bool,
) -> bool:
    raw = os.getenv(name)

    if raw is None:
        return default

    return raw.strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }


def _env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
) -> int:
    raw = os.getenv(name)

    try:
        value = (
            int(raw)
            if raw is not None
            else default
        )
    except ValueError as exc:
        raise ValueError(
            f"{name} must be an integer."
        ) from exc

    if (
        minimum is not None
        and value < minimum
    ):
        raise ValueError(
            f"{name} must be at least {minimum}."
        )

    return value


def _env_csv(
    name: str,
    default: str,
) -> tuple[str, ...]:
    raw = os.getenv(name, default)

    values = tuple(
        item.strip().lower()
        for item in raw.split(",")
        if item.strip()
    )

    if not values:
        raise ValueError(
            f"{name} must contain at least one value."
        )

    return values


@dataclass(frozen=True)
class Settings:
    # ---------------------------------------------------------
    # Ollama
    # ---------------------------------------------------------

    ollama_base_url: str = os.getenv(
        "SEEFIX_OLLAMA_BASE_URL",
        "http://127.0.0.1:11434",
    ).rstrip("/")

    ollama_model: str = os.getenv(
        "SEEFIX_OLLAMA_MODEL",
        "qwen3-vl:2b-instruct-q4_K_M",
    ).strip()

    request_timeout_seconds: int = _env_int(
        "SEEFIX_REQUEST_TIMEOUT_SECONDS",
        180,
        minimum=1,
    )

    # Structured Qwen/VLM requests require at least 4096.
    # OllamaProvider can automatically expand further when
    # prompt + schema + image tokens require more context.
    model_context: int = _env_int(
        "SEEFIX_MODEL_CONTEXT",
        4096,
        minimum=4096,
    )

    model_max_output_tokens: int = _env_int(
        "SEEFIX_MODEL_MAX_OUTPUT_TOKENS",
        768,
        minimum=256,
    )

    model_keep_alive: str = os.getenv(
        "SEEFIX_MODEL_KEEP_ALIVE",
        "15m",
    ).strip()

    # ---------------------------------------------------------
    # Images
    # ---------------------------------------------------------

    max_upload_mb: int = _env_int(
        "SEEFIX_MAX_UPLOAD_MB",
        10,
        minimum=1,
    )

    max_image_dimension: int = _env_int(
        "SEEFIX_MAX_IMAGE_DIMENSION",
        512,
        minimum=128,
    )

    image_download_timeout_seconds: int = _env_int(
        "SEEFIX_IMAGE_DOWNLOAD_TIMEOUT_SECONDS",
        30,
        minimum=1,
    )

    allowed_image_hosts: tuple[str, ...] = _env_csv(
        "SEEFIX_ALLOWED_IMAGE_HOSTS",
        "res.cloudinary.com",
    )

    image_max_redirects: int = _env_int(
        "SEEFIX_IMAGE_MAX_REDIRECTS",
        3,
        minimum=0,
    )

    # ---------------------------------------------------------
    # PostgreSQL
    # ---------------------------------------------------------

    database_url: str = os.getenv(
        "DATABASE_URL",
        "",
    ).strip()

    database_sslmode: str = os.getenv(
        "SEEFIX_DATABASE_SSLMODE",
        "prefer",
    ).strip().lower()

    database_sslrootcert: str = os.getenv(
        "SEEFIX_DATABASE_SSLROOTCERT",
        "",
    ).strip()

    database_connect_timeout_seconds: int = _env_int(
        "SEEFIX_DATABASE_CONNECT_TIMEOUT_SECONDS",
        10,
        minimum=1,
    )

    database_application_name: str = os.getenv(
        "SEEFIX_DATABASE_APPLICATION_NAME",
        "seefix-agents",
    ).strip()

    # ---------------------------------------------------------
    # Initial Report worker
    # ---------------------------------------------------------

    agent_worker_name: str = os.getenv(
        "SEEFIX_AGENT_WORKER_NAME",
        "seefix-agent-01",
    ).strip()

    agent_poll_seconds: int = _env_int(
        "SEEFIX_AGENT_POLL_SECONDS",
        3,
        minimum=1,
    )

    agent_stale_minutes: int = _env_int(
        "SEEFIX_AGENT_STALE_MINUTES",
        30,
        minimum=1,
    )

    # ---------------------------------------------------------
    # Completion worker
    # ---------------------------------------------------------

    completion_worker_name: str = os.getenv(
        "SEEFIX_COMPLETION_WORKER_NAME",
        "seefix-completion-agent-01",
    ).strip()

    completion_poll_seconds: int = _env_int(
        "SEEFIX_COMPLETION_POLL_SECONDS",
        3,
        minimum=1,
    )

    completion_stale_minutes: int = _env_int(
        "SEEFIX_COMPLETION_STALE_MINUTES",
        30,
        minimum=1,
    )

    # ---------------------------------------------------------
    # Knowledge / deterministic defaults
    #
    # dbo.SystemSettings takes precedence when available.
    # ---------------------------------------------------------

    recurrence_threshold: int = _env_int(
        "SEEFIX_RECURRENCE_THRESHOLD",
        2,
        minimum=1,
    )

    recurrence_lookback_days: int = _env_int(
        "SEEFIX_RECURRENCE_LOOKBACK_DAYS",
        365,
        minimum=1,
    )

    verification_threshold: int = _env_int(
        "SEEFIX_VERIFICATION_THRESHOLD",
        2,
        minimum=1,
    )

    duplicate_lookback_days: int = _env_int(
        "SEEFIX_DUPLICATE_LOOKBACK_DAYS",
        30,
        minimum=1,
    )

    # ---------------------------------------------------------
    # Procurement / monitoring
    #
    # Operational follow-up targets only.
    # Not legal Procurement deadlines.
    # ---------------------------------------------------------

    workflow_monitor_enabled: bool = _env_bool(
        "SEEFIX_WORKFLOW_MONITOR_ENABLED",
        True,
    )

    workflow_monitor_seconds: int = _env_int(
        "SEEFIX_WORKFLOW_MONITOR_SECONDS",
        60,
        minimum=1,
    )

    procurement_expected_days: int = _env_int(
        "SEEFIX_PROCUREMENT_EXPECTED_DAYS",
        3,
        minimum=1,
    )

    procurement_followup_hours: int = _env_int(
        "SEEFIX_PROCUREMENT_FOLLOWUP_HOURS",
        24,
        minimum=1,
    )

    # ---------------------------------------------------------
    # FastAPI authentication
    # ---------------------------------------------------------

    require_api_key: bool = _env_bool(
        "SEEFIX_REQUIRE_API_KEY",
        True,
    )

    agent_api_key: str = os.getenv(
        "SEEFIX_AGENT_API_KEY",
        "",
    ).strip()

    def validate(self) -> None:
        if not self.database_url:
            raise ValueError(
                "DATABASE_URL is required."
            )

        if (
            self.database_sslmode
            not in VALID_SSL_MODES
        ):
            raise ValueError(
                "SEEFIX_DATABASE_SSLMODE is invalid."
            )

        if not self.ollama_model:
            raise ValueError(
                "SEEFIX_OLLAMA_MODEL must not be empty."
            )

        if (
            not self.agent_worker_name
            or not self.completion_worker_name
        ):
            raise ValueError(
                "Agent worker names must not be empty."
            )

        if (
            self.require_api_key
            and not self.agent_api_key
        ):
            raise ValueError(
                "SEEFIX_AGENT_API_KEY is required "
                "when SEEFIX_REQUIRE_API_KEY=true."
            )


settings = Settings()
settings.validate()