from rest_framework.permissions import BasePermission


class HasActiveAccount(BasePermission):
    """Allow only authenticated accounts that are active and not soft-deleted."""

    message = "Ce compte n’est pas actif."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active)


class IsSelfOrAdmin(BasePermission):
    """Object permission for resources owned by a user."""

    def has_object_permission(self, request, view, obj):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and (obj.pk == user.pk or getattr(user, "role", None) == "ADMIN")
        )


class IsStandardUser(BasePermission):
    """Allow only authenticated, active accounts with the standard USER role."""

    message = "Seuls les utilisateurs avec le rôle USER peuvent effectuer cette action."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and getattr(user, "role", None) == "USER"
        )

