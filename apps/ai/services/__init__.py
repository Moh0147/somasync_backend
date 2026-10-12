from .base import AIService, AIServiceResponse
from .ollama import OllamaAIService
from .openai_service import OpenAIService

__all__ = ['AIService', 'AIServiceResponse', 'OllamaAIService', 'OpenAIService', 'get_ai_service']


def get_ai_service() -> AIService:
    """Returns the best available AIService.
    Prefers OpenAI when OPENAI_API_KEY is set; falls back to Ollama."""
    from django.conf import settings
    key = getattr(settings, 'OPENAI_API_KEY', '').strip()
    if key:
        return OpenAIService(api_key=key)
    return OllamaAIService()
