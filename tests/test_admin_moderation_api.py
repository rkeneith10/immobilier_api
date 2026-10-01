import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.interactions.models import Report
from apps.locations.models import Location
from apps.properties.models import Amenity, Property, PropertyType
from apps.users.models import OwnerProfile, OwnerVerification

User = get_user_model()
PASSWORD = "Secure-Password-521!"


@pytest.fixture
def moderation_data(db):
    owner = User.objects.create_user(email="mod-owner@example.com", password=PASSWORD, role=User.Role.OWNER)
    agent = User.objects.create_user(email="mod-agent@example.com", password=PASSWORD, role=User.Role.AGENT)
    regular = User.objects.create_user(email="mod-user@example.com", password=PASSWORD)
    admin = User.objects.create_user(email="mod-admin@example.com", password=PASSWORD, role=User.Role.ADMIN)
    property_type = PropertyType.objects.create(name="Moderation home", slug="moderation-home")
    location = Location.objects.create(name="Moderation city", slug="moderation-city", type="CITY")
    return {"owner": owner, "agent": agent, "user": regular, "admin": admin,
            "property_type": property_type, "location": location}


def auth(client, user):
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")


def make_property(data, slug, state):
    return Property.objects.create(
        owner=data["owner"], title=slug.title(), slug=slug, property_type=data["property_type"],
        listing_type=Property.ListingType.RENT, price="750.00", currency="USD",
        location=data["location"], status=state,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("path", [
    "/api/admin/dashboard/", "/api/admin/users/", "/api/admin/users/00000000-0000-0000-0000-000000000000/",
    "/api/admin/properties/", "/api/admin/locations/", "/api/admin/property-types/",
    "/api/admin/amenities/", "/api/admin/owner-verifications/", "/api/admin/reports/",
])
def test_admin_namespace_rejects_anonymous_and_regular_users(path, moderation_data):
    client = APIClient()
    assert client.get(path).status_code == 401
    for role_user in (moderation_data["user"], moderation_data["owner"], moderation_data["agent"]):
        auth(client, role_user)
        assert client.get(path).status_code == 403


@pytest.mark.django_db
def test_dashboard_counts_and_report_submission(moderation_data):
    data = moderation_data
    make_property(data, "mod-published", Property.Status.PUBLISHED)
    make_property(data, "mod-pending", Property.Status.PENDING_REVIEW)
    make_property(data, "mod-rented", Property.Status.RENTED)
    profile = OwnerProfile.objects.create(user=data["agent"], display_name="Agent profile")
    OwnerVerification.objects.create(owner_profile=profile)
    client = APIClient()
    auth(client, data["user"])
    reported = client.post("/api/reports/", {
        "reported_user": str(data["owner"].pk), "reason": "FRAUD", "description": "Suspected fraud",
    }, format="json")
    assert reported.status_code == 201

    auth(client, data["admin"])
    response = client.get(reverse("admin_api:dashboard"))
    assert response.status_code == 200
    assert response.data == {
        "total_users": 4, "active_users": 4, "total_owners": 1,
        "total_properties": 3, "published_properties": 1,
        "pending_properties": 1, "rented_properties": 1,
        "pending_verifications": 1, "reports_count": 1,
    }


@pytest.mark.django_db
def test_admin_can_moderate_properties_and_manage_catalogues(moderation_data):
    data = moderation_data
    pending = make_property(data, "admin-approval", Property.Status.PENDING_REVIEW)
    client = APIClient()
    auth(client, data["user"])
    assert client.post(f"/api/admin/properties/{pending.pk}/publish/").status_code == 403

    auth(client, data["admin"])
    response = client.post(f"/api/admin/properties/{pending.pk}/publish/")
    assert response.status_code == 200
    pending.refresh_from_db()
    assert pending.status == Property.Status.PUBLISHED

    response = client.post("/api/admin/amenities/", {"name": "Admin pool", "slug": "admin-pool"}, format="json")
    assert response.status_code == 201
    assert Amenity.objects.filter(slug="admin-pool").exists()


@pytest.mark.django_db
def test_report_target_constraints_and_admin_review(moderation_data):
    data = moderation_data
    client = APIClient()
    auth(client, data["user"])
    assert client.post("/api/reports/", {"reason": "OTHER"}, format="json").status_code == 400
    assert client.post("/api/reports/", {
        "reported_user": str(data["user"].pk), "reason": "OTHER",
    }, format="json").status_code == 400
    created = client.post("/api/reports/", {
        "reported_user": str(data["owner"].pk), "reason": "OTHER",
    }, format="json")
    assert created.status_code == 201
    report_id = created.data["id"]
    assert created.data["status"] == Report.Status.OPEN
    assert "reviewed_by" not in created.data

    auth(client, data["admin"])
    detail_url = reverse("admin_api:report-detail", kwargs={"pk": report_id})
    response = client.patch(detail_url, {"status": "REVIEWING", "resolution_note": "Checking"}, format="json")
    assert response.status_code == 200
    report = Report.objects.get(pk=report_id)
    assert report.reviewed_by == data["admin"]
    assert report.reviewed_at is not None
    assert report.status == Report.Status.REVIEWING
