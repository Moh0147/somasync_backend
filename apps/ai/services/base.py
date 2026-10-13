from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

@dataclass
class AIServiceResponse:
    text: str
    raw: dict[str, Any] = field(default_factory=dict)

class AIService(ABC):

    @abstractmethod
    def generate(self, prompt: str, *, system: str | None = None, timeout: float | None = None, num_predict: int | None = None, temperature: float | None = None) -> AIServiceResponse:
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> bool:
        raise NotImplementedError
