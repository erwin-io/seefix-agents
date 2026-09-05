from __future__ import annotations

from abc import ABC, abstractmethod

from ..schemas import ProviderAnalysis


class ProviderError(RuntimeError):
    pass


class ModelProvider(ABC):
    name: str
    model_id: str

    @abstractmethod
    def analyze(self, image_bytes: bytes) -> ProviderAnalysis:
        raise NotImplementedError
