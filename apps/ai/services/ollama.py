import requests
from django.conf import settings

from apps.ai.exceptions import AIServiceResponseError, AIServiceUnavailableError

from .base import AIService, AIServiceResponse


class OllamaAIService(AIService):
    """Talks to a local Ollama server's /api/generate endpoint. Configured
    entirely via settings (OLLAMA_BASE_URL, OLLAMA_MODEL,
    OLLAMA_TIMEOUT_SECONDS), overridable per-instance for tests."""

    def __init__(self, base_url=None, model=None, timeout=None):
        self.base_url = (base_url or settings.OLLAMA_BASE_URL).rstrip('/')
        self.model = model or settings.OLLAMA_MODEL
        self.timeout = timeout if timeout is not None else settings.OLLAMA_TIMEOUT_SECONDS

    def generate(self, prompt, *, system=None, timeout=None, num_predict=None):
        # Default is kept short for the phone-sized explanation card. Callers
        # that need longer output (e.g. answer extraction) pass num_predict
        # explicitly.
        payload = {
            'model': self.model,
            'prompt': prompt,
            'stream': False,
            'keep_alive': '10m',
            'options': {'temperature': 0.2, 'num_predict': num_predict if num_predict is not None else 220},
        }
        if system:
            payload['system'] = system

        try:
            response = requests.post(
                f'{self.base_url}/api/generate', json=payload,
                timeout=timeout if timeout is not None else self.timeout,
            )
        except requests.exceptions.Timeout as exc:
            raise AIServiceUnavailableError('Ollama request timed out.') from exc
        except requests.exceptions.ConnectionError as exc:
            raise AIServiceUnavailableError('Could not connect to Ollama.') from exc
        except requests.exceptions.RequestException as exc:
            raise AIServiceUnavailableError(f'Ollama request failed: {exc}') from exc

        if response.status_code != 200:
            raise AIServiceUnavailableError(f'Ollama returned HTTP {response.status_code}.')

        try:
            data = response.json()
        except ValueError as exc:
            raise AIServiceResponseError('Ollama response was not valid JSON.') from exc

        if not isinstance(data, dict):
            raise AIServiceResponseError('Ollama response was not a JSON object.')
        if 'error' in data:
            raise AIServiceResponseError(f'Ollama returned an error: {data["error"]}')

        text = data.get('response')
        if not isinstance(text, str) or not text.strip():
            raise AIServiceResponseError('Ollama response did not include usable text.')

        return AIServiceResponse(text=text.strip(), raw=data)

    def health_check(self):
        try:
            response = requests.get(f'{self.base_url}/api/tags', timeout=self.timeout)
        except requests.exceptions.RequestException:
            return False
        return response.status_code == 200
