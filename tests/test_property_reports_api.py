import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.interactions.models import PropertyReport
from apps.locations.models import Location
from apps.properties.models import Property, PropertyType

User = get_user_model()
PASSWORD = "Secure-Password-521!"


@pytest.fixture
def report_data(db):
    reporter = User.objects.create_user(email="pr-reporter@example.com", password=PASSWORD)
    another_reporter = User.objects.create_user(email="pr-other@example.com", password=PASSWORD)
    owner = User.objects.create_user(email="pr-owner@example.com", password=PASSWORD, role=User.Role.OWNER)
    admin = User.objects.create_user(email="pr-admin@example.com", password=PASSWORD, role=User.Role.ADMIN)
    property_type = PropertyType.objects.create(name="Reported house", slug="reported-house")
    location = Location.objects.create(name="Reported city", slug="reported-city", type="CITY")
    published = Property.objects.create(
        owner=owner, title="Published listing", slug="reported-published", property_type=property_type,
        listing_type=Property.ListingType.RENT, price="800", currency="USD", location=location,
        status=Property.Status.PUBLISHED,
    )
    draft = Property.objects.create(
        owner=owner, title="Draft listing", slug="reported-draft", property_type=property_type,
        listing_type=Property.ListingType.RENT, price="800", currency="USD", location=location,
        status=Property.Status.DRAFT,
    )
    return {"reporter": reporter, "another": another_reporter, "owner": owner, "admin": admin,
            "published": published, "draft": draft}


def auth(client, user):
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")


def submit(client, property_id, reason="SCAM", description="Concern"):
    return client.post(
        f"/api/properties/{property_id}/reports/",
        {"reason": reason, "description": description},
        format="json",
    )


@pytest.mark.django_db
def test_authenticated_users_can_only_report_published_properties(report_data):
    client = APIClient()
    assert submit(client, report_data["published"].pk).status_code == 401
    auth(client, report_data["reporter"])
    response = submit(client, report_data["published"].pk)
    assert response.status_code == 201
    assert response.data["property"] == report_data["published"].pk
    assert response.data["reported_by"] == report_data["reporter"].pk
    assert response.data["reason"] == PropertyReport.Reason.SCAM
    assert response.data["status"] == PropertyReport.Status.PENDING
    assert response.data["reviewed_by"] is None
    assert submit(client, report_data["draft"].pk).status_code == 400
    assert submit(client, "00000000-0000-0000-0000-000000000000").status_code == 404


@pytest.mark.django_db
def test_duplicate_active_report_is_rejected_but_a_closed_report_can_be_resubmitted(report_data):
    client = APIClient()
    auth(client, report_data["reporter"])
    first = submit(client, report_data["published"].pk, "WRONG_PRICE", "Price mismatch")
    assert first.status_code == 201
    assert submit(client, report_data["published"].pk, "WRONG_PRICE", "Another description").status_code == 400
    assert submit(client, report_data["published"].pk, "SCAM").status_code == 201
    report = PropertyReport.objects.get(pk=first.data["id"])
    report.status = PropertyReport.Status.DISMISSED
    report.save(update_fields=("status", "updated_at"))
    assert submit(client, report_data["published"].pk, "WRONG_PRICE").status_code == 201


@pytest.mark.django_db
@pytest.mark.parametrize("reason", [
    "FAKE_LISTING", "WRONG_PRICE", "PROPERTY_RENTED", "MISLEADING_PHOTOS", "SCAM", "OTHER",
])
def test_all_property_report_reasons_are_accepted(report_data, reason):
    client = APIClient()
    auth(client, report_data["reporter"])
    assert submit(client, report_data["published"].pk, reason).status_code == 201


@pytest.mark.django_db
def test_admin_can_list_and_process_property_reports_and_regular_user_cannot(report_data):
    client = APIClient()
    auth(client, report_data["reporter"])
    created = submit(client, report_data["published"].pk)
    detail = reverse("admin_api:property-report-detail", kwargs={"pk": created.data["id"]})
    assert client.get("/api/admin/property-reports/").status_code == 403
    assert client.patch(detail, {"status": "ACTION_TAKEN"}, format="json").status_code == 403

    auth(client, report_data["admin"])
    assert client.get("/api/admin/property-reports/").status_code == 200
    assert client.get(detail).status_code == 200
    reviewed = client.patch(detail, {"status": "REVIEWED"}, format="json")
    assert reviewed.status_code == 200
    report = PropertyReport.objects.get(pk=created.data["id"])
    assert report.reviewed_by == report_data["admin"]
    assert report.reviewed_at is not None
    acted = client.patch(detail, {"status": "ACTION_TAKEN"}, format="json")
    assert acted.status_code == 200
    report.refresh_from_db()
    assert report.status == PropertyReport.Status.ACTION_TAKEN
    assert report.reviewed_at is not None
    assert client.patch(detail, {"status": "DISMISSED"}, format="json").status_code == 400


@pytest.mark.django_db
def test_pending_report_can_be_dismissed_by_admin_and_updates_dashboard(report_data):
    client = APIClient()
    auth(client, report_data["reporter"])
    created = submit(client, report_data["published"].pk)
    detail = reverse("admin_api:property-report-detail", kwargs={"pk": created.data["id"]})
    auth(client, report_data["admin"])
    response = client.patch(detail, {"status": "DISMISSED", "reviewed_by": str(report_data["reporter"].pk)}, format="json")
    assert response.status_code == 400
    response = client.patch(detail, {"status": "DISMISSED"}, format="json")
    assert response.status_code == 200
    assert response.data["reviewed_by"] == report_data["admin"].pk
    assert response.data["reviewed_at"] is not None

    dashboard = client.get(reverse("admin_api:dashboard"))
    assert dashboard.data["reports_count"] == 1
