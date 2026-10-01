"""Account-facing API and authorization helpers live in this module."""

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from apps.users.permissions import IsSelfOrAdmin

User = get_user_model()


@pytest.mark.django_db
def test_self_or_admin_permission_blocks_other_users():
    first = User.objects.create_user(email="first@example.com", password="Secure-Password-521!")
    second = User.objects.create_user(email="second@example.com", password="Secure-Password-521!")
    admin = User.objects.create_user(
        email="admin@example.com",
        password="Secure-Password-521!",
        role=User.Role.ADMIN,
    )
    permission = IsSelfOrAdmin()

    assert permission.has_object_permission(SimpleNamespace(user=first), None, first)
    assert not permission.has_object_permission(SimpleNamespace(user=first), None, second)
    assert permission.has_object_permission(SimpleNamespace(user=admin), None, second)
