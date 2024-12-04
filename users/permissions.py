from rest_framework.permissions import BasePermission


class HasEmailConfirmed(BasePermission):
    message = 'Email not confirmed.'

    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated:
            return False
        return user.email_confirmed
