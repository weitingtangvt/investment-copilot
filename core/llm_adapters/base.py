from __future__ import annotations

from abc import ABC, abstractmethod

from .models import LLMCapabilitySet, LLMRequest, LLMResponse


class BaseLLMAdapter(ABC):
    provider_name = "base"

    @abstractmethod
    def capabilities(self) -> LLMCapabilitySet:
        raise NotImplementedError

    @abstractmethod
    def execute(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError
