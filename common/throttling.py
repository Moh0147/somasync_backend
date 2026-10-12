from rest_framework.throttling import ScopedRateThrottle

class LoginRateThrottle(ScopedRateThrottle):
    """Per-IP brute-force guard on the login endpoint. Set
    `throttle_scope = 'login'` on the view; DRF keys ScopedRateThrottle by
    IP for unauthenticated requests, which is what we want here since a
    login attempt has no authenticated user yet."""

class AIRequestThrottle(ScopedRateThrottle):
    """Per-user guard on AI-backed endpoints (Explain My Mistake). Set
    `throttle_scope = 'ai_explain'` on the view. Ollama runs as a single
    local instance shared by the whole school -- without this, one student
    hammering the endpoint could starve everyone else's requests or pin
    the CPU on the low-end hardware this project targets."""
