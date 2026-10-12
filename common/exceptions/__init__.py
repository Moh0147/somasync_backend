from rest_framework.views import exception_handler as drf_exception_handler

def custom_exception_handler(exc, context):
    """Wrap DRF's default exception handler so every error response shares
    the same envelope: {"success": false, "errors": ...}."""
    response = drf_exception_handler(exc, context)

    if response is None:
        return response

    response.data = {
        'success': False,
        'errors': response.data,
    }
    return response
