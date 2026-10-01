from django.db import models
from django.utils import timezone


class SoftDeleteQuerySet(models.QuerySet):
    """QuerySet that soft-deletes rows by default."""

    def delete(self):
        now = timezone.now()
        count = self.update(is_deleted=True, deleted_at=now, updated_at=now)
        return count, {self.model._meta.label: count}

    def hard_delete(self):
        """Permanently delete matching rows. Use only for explicit cleanup."""
        return super().delete()

    def restore(self):
        now = timezone.now()
        count = self.update(is_deleted=False, deleted_at=None, updated_at=now)
        return count


class SoftDeleteManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    """Default manager; deleted rows are excluded from normal queries."""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)
