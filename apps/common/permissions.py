from rest_framework.permissions import BasePermission, SAFE_METHODS


class AdminOnly(BasePermission):
    """Require an active account whose application role is ADMIN."""

    message = "Cette ressource est réservée aux administrateurs actifs."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated and user.is_active
            and getattr(user, "role", None) == "ADMIN"
        )


class IsAdminOrReadOnly(BasePermission):
    """Allow public reads and require an active ADMIN role for writes."""

    message = "Seuls les administrateurs actifs peuvent effectuer cette action."

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and getattr(user, "role", None) == "ADMIN"
        )
