from rest_framework.throttling import ScopedRateThrottle

# Both just alias ScopedRateThrottle under a name that reads clearly at the
# call site (`throttle_classes = [LoginRateThrottle]`). The actual scope
# used for the rate lookup comes from `throttle_scope` on the *view*
# (ScopedRateThrottle.allow_request reads `getattr(view, 'throttle_scope',
# None)`, not anything on the throttle class itself) -- so each view that
# uses one of these must also set the matching `throttle_scope`.


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
