import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.users.models import OwnerProfile, OwnerVerification

User = get_user_model()
PASSWORD = "Secure-Password-521!"


@pytest.fixture
def owner_users(db):
    return {
        "owner": User.objects.create_user(email="owner-profile@example.com", password=PASSWORD, role=User.Role.OWNER),
        "agent": User.objects.create_user(email="agent-profile@example.com", password=PASSWORD, role=User.Role.AGENT),
        "user": User.objects.create_user(email="normal-profile@example.com", password=PASSWORD),
        "admin": User.objects.create_user(email="admin-profile@example.com", password=PASSWORD, role=User.Role.ADMIN),
    }


def auth(client, user):
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")


@pytest.mark.django_db
def test_owner_and_agent_can_create_and_edit_profile_but_cannot_set_verified(owner_users):
    client = APIClient()
    for key in ("owner", "agent"):
        user = owner_users[key]
        auth(client, user)
        response = client.post(reverse("users:owner-profile-me"), {"display_name": key.title()}, format="json")
        assert response.status_code == 201
        assert response.data["verification_status"] == OwnerProfile.VerificationStatus.PENDING
        assert "password" not in response.data
        profile = user.owner_profile
        response = client.patch(
            reverse("users:owner-profile-me"),
            {"display_name": "Updated", "verification_status": "VERIFIED"}, format="json",
        )
        assert response.status_code == 400
        profile.refresh_from_db()
        assert profile.verification_status != OwnerProfile.VerificationStatus.VERIFIED


@pytest.mark.django_db
def test_only_owner_or_agent_can_create_profile(owner_users):
    client = APIClient()
    assert client.post(reverse("users:owner-profile-me"), {"display_name": "No auth"}, format="json").status_code == 401
    auth(client, owner_users["user"])
    assert client.post(reverse("users:owner-profile-me"), {"display_name": "No role"}, format="json").status_code == 403
    auth(client, owner_users["owner"])
    assert client.post(reverse("users:owner-profile-me"), {"display_name": "Owner"}, format="json").status_code == 201
    assert client.post(reverse("users:owner-profile-me"), {"display_name": "Duplicate"}, format="json").status_code == 400


@pytest.mark.django_db
def test_owner_submits_verification_and_admin_approves(owner_users):
    client = APIClient()
    owner = owner_users["owner"]
    auth(client, owner)
    assert client.post(reverse("users:owner-verification-submit")).status_code == 400
    client.post(reverse("users:owner-profile-me"), {"display_name": "Owner display"}, format="json")
    submitted = client.post(reverse("users:owner-verification-submit"))
    assert submitted.status_code == 201
    verification_id = submitted.data["id"]
    assert submitted.data["status"] == OwnerVerification.Status.PENDING
    assert client.post(reverse("users:owner-verification-submit")).status_code == 400

    auth(client, owner_users["user"])
    assert client.get(reverse("users:owner-verification-list")).status_code == 403
    auth(client, owner_users["admin"])
    assert client.get(reverse("users:owner-verification-list")).status_code == 200
    assert client.get(reverse("users:owner-verification-review", kwargs={"pk": verification_id})).status_code == 200
    response = client.patch(
        reverse("users:owner-verification-review", kwargs={"pk": verification_id}),
        {"status": "VERIFIED"}, format="json",
    )
    assert response.status_code == 200
    assert response.data["reviewed_by"] == owner_users["admin"].pk
    owner.owner_profile.refresh_from_db()
    assert owner.owner_profile.verification_status == OwnerProfile.VerificationStatus.VERIFIED
    assert owner.owner_profile.verified_at is not None
    assert client.patch(
        reverse("users:owner-verification-review", kwargs={"pk": verification_id}),
        {"status": "REJECTED", "rejection_reason": "Changed"}, format="json",
    ).status_code == 400


@pytest.mark.django_db
def test_admin_rejects_with_reason_and_owner_can_resubmit(owner_users):
    client = APIClient()
    owner = owner_users["agent"]
    auth(client, owner)
    client.post(reverse("users:owner-profile-me"), {"display_name": "Agency"}, format="json")
    verification = client.post(reverse("users:owner-verification-submit")).data
    auth(client, owner_users["admin"])
    endpoint = reverse("users:owner-verification-review", kwargs={"pk": verification["id"]})
    assert client.patch(endpoint, {"status": "REJECTED"}, format="json").status_code == 400
    response = client.patch(endpoint, {"status": "REJECTED", "rejection_reason": "Document illisible"}, format="json")
    assert response.status_code == 200
    owner.owner_profile.refresh_from_db()
    assert owner.owner_profile.verification_status == OwnerProfile.VerificationStatus.REJECTED
    assert owner.owner_profile.verified_at is None
    auth(client, owner)
    resubmitted = client.post(reverse("users:owner-verification-submit"))
    assert resubmitted.status_code == 201
    assert resubmitted.data["id"] != verification["id"]
    assert OwnerVerification.objects.filter(owner_profile=owner.owner_profile).count() == 2
