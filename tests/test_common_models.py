import uuid
from unittest.mock import patch

from django.db import models
from django.test import SimpleTestCase
import pytest

from apps.common.models import SoftDeleteModel, TimestampedModel, UUIDModel, UUIDTimestampedModel


class SoftDeleteProbe(SoftDeleteModel):
    """Test-only concrete subclass; no production domain model is introduced."""

    class Meta:
        app_label = "common"


class CommonAbstractModelsTests(SimpleTestCase):
    def test_shared_bases_are_abstract_and_provide_expected_fields(self):
        self.assertTrue(UUIDModel._meta.abstract)
        self.assertTrue(TimestampedModel._meta.abstract)
        self.assertTrue(UUIDTimestampedModel._meta.abstract)
        self.assertTrue(SoftDeleteModel._meta.abstract)

        id_field = UUIDTimestampedModel._meta.get_field("id")
        self.assertIsInstance(id_field, models.UUIDField)
        self.assertTrue(id_field.primary_key)
        self.assertIs(id_field.default, uuid.uuid4)
        self.assertTrue(UUIDTimestampedModel._meta.get_field("created_at").auto_now_add)
        self.assertTrue(UUIDTimestampedModel._meta.get_field("updated_at").auto_now)

    def test_soft_delete_and_restore_update_state_and_persist_fields(self):
        instance = SoftDeleteProbe()

        with patch.object(instance, "save") as save:
            instance.soft_delete()

        self.assertTrue(instance.is_deleted)
        self.assertIsNotNone(instance.deleted_at)
        save.assert_called_once()
        self.assertEqual(
            save.call_args.kwargs["update_fields"],
            ["is_deleted", "deleted_at", "updated_at"],
        )

        with patch.object(instance, "save") as save:
            instance.restore()

        self.assertFalse(instance.is_deleted)
        self.assertIsNone(instance.deleted_at)
        save.assert_called_once()

@pytest.mark.django_db
def test_soft_delete_queryset_hides_restores_and_hard_deletes_rows():
    instance = SoftDeleteProbe.all_objects.create()

    assert SoftDeleteProbe.objects.filter(pk=instance.pk).exists()
    deleted_count, _ = SoftDeleteProbe.objects.filter(pk=instance.pk).delete()
    assert deleted_count == 1
    assert not SoftDeleteProbe.objects.filter(pk=instance.pk).exists()
    assert SoftDeleteProbe.all_objects.filter(pk=instance.pk, is_deleted=True).exists()

    assert SoftDeleteProbe.all_objects.filter(pk=instance.pk).restore() == 1
    assert SoftDeleteProbe.objects.filter(pk=instance.pk).exists()

    hard_deleted_count, _ = SoftDeleteProbe.all_objects.filter(pk=instance.pk).hard_delete()
    assert hard_deleted_count == 1
    assert not SoftDeleteProbe.all_objects.filter(pk=instance.pk).exists()