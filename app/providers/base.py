from __future__ import annotations


class ProviderError(RuntimeError):
    """Raised when the local Ollama inference provider fails."""


class InvalidModelOutput(ProviderError):
    """The model answered, but not in the allowed structured shape."""



