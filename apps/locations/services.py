from django.core.exceptions import ValidationError


def validate_parent_assignment(location, parent):
    """Prevent assigning a location beneath itself or one of its descendants."""
    if parent is None or location is None or location.pk is None:
        return

    ancestor = parent
    seen = set()
    while ancestor is not None:
        if ancestor.pk == location.pk:
            raise ValidationError("Une localisation ne peut pas être son propre ancêtre.")
        if ancestor.pk in seen:
            raise ValidationError("La hiérarchie de localisation contient déjà un cycle.")
        seen.add(ancestor.pk)
        ancestor = ancestor.parent
