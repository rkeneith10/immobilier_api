from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.interactions.models import Inquiry, VisitRequest
from apps.locations.models import Location
from apps.notifications.models import Notification
from apps.properties.models import Property, PropertyType
from apps.users.models import OwnerProfile

User = get_user_model()
PASSWORD = "Secure-Password-521!"


@pytest.fixture
def notification_data(db):
    reporter = User.objects.create_user(email="notification-client@example.com", password=PASSWORD)
    other = User.objects.create_user(email="notification-other@example.com", password=PASSWORD)
    owner = User.objects.create_user(email="notification-owner@example.com", password=PASSWORD, role=User.Role.OWNER)
    admin = User.objects.create_user(email="notification-admin@example.com", password=PASSWORD, role=User.Role.ADMIN)
    property_type = PropertyType.objects.create(name="Notification house", slug="notification-house")
    location = Location.objects.create(name="Notification city", slug="notification-city", type="CITY")
    return {"reporter": reporter, "other": other, "owner": owner, "admin": admin,
            "property_type": property_type, "location": location}


def auth(client, user):
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")


def make_property(data, slug, status=Property.Status.PUBLISHED):
    return Property.objects.create(
        owner=data["owner"], title=slug.replace("-", " ").title(), slug=slug,
        property_type=data["property_type"], listing_type=Property.ListingType.RENT,
        price="900.00", currency="USD", location=data["location"], status=status,
    )


def visit_payload():
    return {
        "requested_date": (timezone.localdate() + timedelta(days=2)).isoformat(),
        "requested_time": "12:00:00", "message": "I would like a visit.",
    }


@pytest.mark.django_db
def test_notifications_are_private_and_can_be_marked_read(notification_data):
    user = notification_data["reporter"]
    other = notification_data["other"]
    mine = Notification.objects.create(
        user=user, type=Notification.Type.NEW_INQUIRY, title="Question",
        message="A new message.", data={"item_id": "123"},
    )
    Notification.objects.create(
        user=other, type=Notification.Type.NEW_INQUIRY, title="Private",
        message="Not yours.",
    )
    client = APIClient()
    assert client.get("/api/notifications/").status_code == 401
    auth(client, user)
    response = client.get("/api/notifications/")
    assert response.status_code == 200
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == str(mine.pk)
    assert response.data["results"][0]["data"] == {"item_id": "123"}

    read = client.patch(f"/api/notifications/{mine.pk}/read/")
    assert read.status_code == 200
    assert read.data["is_read"] is True
    assert client.patch(f"/api/notifications/{Notification.objects.filter(user=other).first().pk}/read/").status_code == 404

    Notification.objects.create(user=user, type=Notification.Type.NEW_INQUIRY, title="Unread", message="Again")
    Notification.objects.create(user=other, type=Notification.Type.NEW_INQUIRY, title="Other", message="Other")
    response = client.post(reverse("notifications:read-all"))
    assert response.status_code == 200
    assert response.data["updated"] == 1
    assert Notification.objects.filter(user=user, is_read=False).count() == 0
    assert Notification.objects.filter(user=other, is_read=False).count() == 2


@pytest.mark.django_db
def test_property_approval_and_rejection_notify_listing_owner(notification_data):
    data = notification_data
    to_publish = make_property(data, "notify-publish", Property.Status.PENDING_REVIEW)
    to_reject = make_property(data, "notify-reject", Property.Status.PENDING_REVIEW)
    client = APIClient()
    auth(client, data["admin"])
    assert client.post(f"/api/properties/{to_publish.pk}/publish/").status_code == 200
    assert client.post(f"/api/properties/{to_reject.pk}/reject/").status_code == 200
    notifications = list(Notification.objects.filter(user=data["owner"]).order_by("type"))
    assert {item.type for item in notifications} == {
        Notification.Type.PROPERTY_APPROVED, Notification.Type.PROPERTY_REJECTED,
    }
    assert all(item.data["property_id"] in {str(to_publish.pk), str(to_reject.pk)} for item in notifications)


@pytest.mark.django_db
def test_new_inquiry_and_visit_decisions_notify_the_right_users(notification_data):
    data = notification_data
    property_obj = make_property(data, "notify-inquiry-visits")
    client = APIClient()
    auth(client, data["reporter"])
    inquiry = client.post(f"/api/properties/{property_obj.pk}/inquiries/", {"message": "Is it available?"}, format="json")
    assert inquiry.status_code == 201
    visit_one = client.post(f"/api/properties/{property_obj.pk}/visits/", visit_payload(), format="json")
    visit_two = client.post(f"/api/properties/{property_obj.pk}/visits/", visit_payload(), format="json")
    assert visit_one.status_code == visit_two.status_code == 201
    owner_notifications = list(Notification.objects.filter(user=data["owner"]).values_list("type", flat=True))
    assert owner_notifications.count(Notification.Type.NEW_INQUIRY) == 1
    assert owner_notifications.count(Notification.Type.NEW_VISIT_REQUEST) == 2

    auth(client, data["owner"])
    assert client.patch(f"/api/visit-requests/{visit_one.data['id']}/", {"status": "ACCEPTED"}, format="json").status_code == 200
    assert client.patch(f"/api/visit-requests/{visit_two.data['id']}/", {"status": "REJECTED"}, format="json").status_code == 200
    client_notifications = set(Notification.objects.filter(user=data["reporter"]).values_list("type", flat=True))
    assert client_notifications == {Notification.Type.VISIT_ACCEPTED, Notification.Type.VISIT_REJECTED}
    assert Inquiry.objects.filter(pk=inquiry.data["id"]).exists()
    assert VisitRequest.objects.filter(pk=visit_one.data["id"]).exists()


@pytest.mark.django_db
def test_new_verification_notifies_active_admins(notification_data):
    data = notification_data
    OwnerProfile.objects.create(user=data["owner"], display_name="Owner profile")
    client = APIClient()
    auth(client, data["owner"])
    response = client.post("/api/auth/owner-profile/me/verification/")
    assert response.status_code == 201
    admin_notifications = Notification.objects.filter(user=data["admin"])
    assert admin_notifications.count() == 1
    event = admin_notifications.get()
    assert event.type == Notification.Type.NEW_VERIFICATION
    assert event.data["verification_id"] == response.data["id"]


@pytest.mark.django_db
def test_property_report_notifies_admins(notification_data):
    data = notification_data
    property_obj = make_property(data, "notify-report")
    client = APIClient()
    auth(client, data["reporter"])
    response = client.post(
        f"/api/properties/{property_obj.pk}/reports/", {"reason": "SCAM"}, format="json"
    )
    assert response.status_code == 201
    notification = Notification.objects.get(user=data["admin"])
    assert notification.type == Notification.Type.PROPERTY_REPORTED
    assert notification.data["property_id"] == str(property_obj.pk)
    assert notification.data["report_id"] == response.data["id"]
