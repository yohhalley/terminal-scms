import hmac

from django.conf import settings
from rest_framework.permissions import BasePermission


class IsMESService(BasePermission):
    message = 'A valid MES service token is required.'

    def has_permission(self, request, view):
        expected = getattr(settings, 'MES_API_TOKEN', '')
        provided = request.headers.get('X-MES-Token', '')
        return bool(expected and provided and hmac.compare_digest(provided, expected))
