from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

@dataclass
class AIServiceResponse:
    text: str
    raw: dict[str, Any] = field(default_factory=dict)

class AIService(ABC):
    """Abstraction over an LLM backend. A concrete implementation (e.g.
    OllamaAIService) is the ONLY thing allowed to talk to the underlying
    model server — the frontend never gets a direct line to it, and every
    AI feature (Explain My Mistake, AI-assisted exam marking — Phase 13+)
    goes through this interface, never around it.

    Implementations must raise AIServiceUnavailableError /
    AIServiceResponseError (apps.ai.exceptions) rather than letting a raw
    connection/parsing exception escape, so callers have one consistent
    set of failure modes to handle regardless of backend.
    """

    @abstractmethod
    def generate(self, prompt: str, *, system: str | None = None, timeout: float | None = None, num_predict: int | None = None, temperature: float | None = None) -> AIServiceResponse:
        """Returns an AIServiceResponse, or raises an AIServiceError subclass."""
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> bool:
        """True if the backing service is reachable and healthy. Never
        raises — a failed health check is a normal, expected result."""
        raise NotImplementedError
