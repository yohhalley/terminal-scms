import hmac

from django.conf import settings
from rest_framework.permissions import BasePermission


class HasOESServiceToken(BasePermission):
    message = "A valid OES service token is required."

    def has_permission(self, request, view):
        configured = settings.OES_API_TOKEN
        if not configured:
            return settings.DEBUG
        supplied = request.headers.get("X-OES-Token", "")
        return bool(supplied) and hmac.compare_digest(supplied, configured)

