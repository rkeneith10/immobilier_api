from rest_framework.permissions import BasePermission


class IsOwnerOrAgent(BasePermission):
    message = "Seuls les propriétaires et agents peuvent gérer un profil propriétaire."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated and user.is_active
            and user.role in ("OWNER", "AGENT")
        )


class IsAdminUserRole(BasePermission):
    message = "Cette action est réservée aux administrateurs."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active and user.role == "ADMIN")
