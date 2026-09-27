from .base import AIService, AIServiceResponse
from .ollama import OllamaAIService

__all__ = ['AIService', 'AIServiceResponse', 'OllamaAIService', 'get_ai_service']


def get_ai_service() -> AIService:
    """Returns the configured AIService implementation. Callers (Phase 13+
    features) should use this factory rather than importing
    OllamaAIService directly, so the backend can change later (settings
    only) without touching every call site."""
    return OllamaAIService()
