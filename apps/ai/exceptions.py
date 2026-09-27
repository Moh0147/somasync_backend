class AIServiceError(Exception):
    """Base class for all AI service errors. AI is never required for core
    learning or quiz completion — callers must catch this and degrade
    gracefully (e.g. fall back to manual marking, skip an explanation),
    never let it break the calling feature."""


class AIServiceUnavailableError(AIServiceError):
    """The AI backend could not be reached at all: connection refused,
    timed out, or returned a non-200 response."""


class AIServiceResponseError(AIServiceError):
    """The AI backend responded, but not in a usable/expected shape
    (invalid JSON, an explicit error field, empty/missing text)."""
