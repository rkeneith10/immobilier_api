import uuid

from django.db import models
from django.utils import timezone

from .managers import SoftDeleteManager, SoftDeleteQuerySet


class UUIDModel(models.Model):
    """Abstract UUID primary key for domain models."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimestampedModel(models.Model):
    """Abstract creation and last-update timestamps."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UUIDTimestampedModel(UUIDModel, TimestampedModel):
    """Common abstract base with UUID identity and timestamps."""

    class Meta:
        abstract = True


class SoftDeleteModel(UUIDTimestampedModel):
    """Abstract soft deletion; normal queries hide deleted rows."""

    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = SoftDeleteManager()
    all_objects = SoftDeleteQuerySet.as_manager()

    class Meta:
        abstract = True

    def soft_delete(self, using=None):
        if self.is_deleted:
            return
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(using=using, update_fields=["is_deleted", "deleted_at", "updated_at"])

    def restore(self, using=None):
        if not self.is_deleted:
            return
        self.is_deleted = False
        self.deleted_at = None
        self.save(using=using, update_fields=["is_deleted", "deleted_at", "updated_at"])

    def delete(self, using=None, keep_parents=False):
        self.soft_delete(using=using)
        return 1, {self._meta.label: 1}

    def hard_delete(self, using=None, keep_parents=False):
        return super().delete(using=using, keep_parents=keep_parents)
