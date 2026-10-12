from django.conf import settings

from apps.ai.exceptions import AIServiceResponseError, AIServiceUnavailableError

from .base import AIService, AIServiceResponse

class OpenAIService(AIService):
    """Talks to OpenAI's chat completions endpoint (gpt-4o-mini by default).
    Used as the preferred AIService when OPENAI_API_KEY is set in settings —
    falls back to OllamaAIService when the key is absent or the call fails."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or getattr(settings, 'OPENAI_API_KEY', '')
        self.model = model or getattr(settings, 'OPENAI_TEXT_MODEL', 'gpt-4o-mini')

    def generate(self, prompt: str, *, system: str | None = None, timeout: float | None = None, num_predict: int | None = None, temperature: float | None = None) -> AIServiceResponse:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AIServiceUnavailableError('openai package is not installed.') from exc

        if not self.api_key:
            raise AIServiceUnavailableError('OPENAI_API_KEY is not set.')

        messages = []
        if system:
            messages.append({'role': 'system', 'content': system})
        messages.append({'role': 'user', 'content': prompt})

        kwargs = {
            'model': self.model,
            'messages': messages,
        }
        if num_predict:
            kwargs['max_tokens'] = num_predict
        if timeout:
            kwargs['timeout'] = timeout
        if temperature is not None:
            kwargs['temperature'] = temperature

        try:
            client = OpenAI(api_key=self.api_key)
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise AIServiceUnavailableError(f'OpenAI request failed: {exc}') from exc

        text = response.choices[0].message.content
        if not isinstance(text, str) or not text.strip():
            raise AIServiceResponseError('OpenAI returned an empty response.')

        return AIServiceResponse(text=text.strip(), raw=response.model_dump())

    def health_check(self) -> bool:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key)
            client.models.list()
            return True
        except Exception:
            return False
