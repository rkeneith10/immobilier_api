from rest_framework.permissions import BasePermission, SAFE_METHODS


class CanManagePropertyImages(BasePermission):
    message = "Seul le propriétaire de la propriété ou un administrateur peut gérer ses images."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_active)

    def has_object_permission(self, request, view, property_obj):
        user = request.user
        role = getattr(user, "role", None)
        return role == "ADMIN" or (
            role in {"OWNER", "AGENT"} and property_obj.owner_id == user.pk
        )


class PropertyPermission(BasePermission):
    """Enforce listing ownership and privileged workflow actions."""

    message = "Vous n’êtes pas autorisé à effectuer cette action sur cette propriété."
    contributor_roles = {"OWNER", "AGENT"}

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True

        user = request.user
        if not user or not user.is_authenticated or not user.is_active:
            return False

        role = getattr(user, "role", None)
        action = getattr(view, "action", None)
        if action == "create":
            return role in self.contributor_roles
        if action in {"publish", "reject", "suspend"}:
            return role == "ADMIN"
        if action in {"submit_for_review", "archive", "mark_rented"}:
            return role in self.contributor_roles | {"ADMIN"}
        if action in {"update", "partial_update", "destroy"}:
            return role in self.contributor_roles | {"ADMIN"}
        return role == "ADMIN"

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        user = request.user
        if not user or not user.is_authenticated or not user.is_active:
            return False
        if getattr(view, "action", None) in {"publish", "reject", "suspend"}:
            return getattr(user, "role", None) == "ADMIN"
        return getattr(user, "role", None) == "ADMIN" or obj.owner_id == user.pk
