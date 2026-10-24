import hmac

from django.conf import settings
from rest_framework.permissions import BasePermission


class IsMESService(BasePermission):
    message = 'A valid MES service token is required.'

    def has_permission(self, request, view):
        expected = getattr(settings, 'MES_API_TOKEN', '')
        provided = request.headers.get('X-MES-Token', '')
        return bool(expected and provided and hmac.compare_digest(provided, expected))


class IsOESServiceOrAuthenticated(BasePermission):
    message = 'Authentication or a valid OES service token is required.'

    def has_permission(self, request, view):
        if request.user and request.user.is_authenticated:
            return True
        expected = getattr(settings, 'OES_API_TOKEN', '')
        provided = request.headers.get('X-OES-Token', '')
        if not provided:
            authorization = request.headers.get('Authorization', '')
            if authorization.startswith('Bearer '):
                provided = authorization[7:]
        return bool(expected and provided and hmac.compare_digest(provided, expected))
