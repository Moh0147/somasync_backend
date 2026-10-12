import hashlib

from rest_framework.response import Response

def api_response(data=None, message=None, status=200):
    """Standard success envelope: {"success": true, "message": ..., "data": ...}."""
    payload = {'success': True}
    if message is not None:
        payload['message'] = message
    if data is not None:
        payload['data'] = data
    return Response(payload, status=status)

def compute_file_checksum(uploaded_file):
    """SHA-256 of an uploaded file, streamed via .chunks() rather than
    .read() -- uploads (exam scripts, lesson attachments) can be tens of
    MB, and this must never load the whole file into memory just to hash
    it, especially on the low-end hardware this project targets."""
    hasher = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        hasher.update(chunk)
    uploaded_file.seek(0)
    return hasher.hexdigest()
