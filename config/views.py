from django.db import connection
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

from common.utils import api_response

@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    try:
        connection.ensure_connection()
        db_status = 'connected'
    except Exception:
        db_status = 'unavailable'

    return api_response(
        data={'status': 'ok', 'database': db_status},
        message='SomaSync API is running',
    )
