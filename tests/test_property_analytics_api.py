from datetime import date, time

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.interactions.models import Favorite, Inquiry, VisitRequest
from apps.locations.models import Location
from apps.properties.models import Property, PropertyType, PropertyView

User = get_user_model()


@pytest.fixture
def analytics_data(db):
    owner = User.objects.create_user(email="analytics-owner@example.com", password="Secure-Password-521!", role="OWNER")
    other_owner = User.objects.create_user(email="analytics-other@example.com", password="Secure-Password-521!", role="AGENT")
    viewer = User.objects.create_user(email="analytics-viewer@example.com", password="Secure-Password-521!")
    admin = User.objects.create_user(email="analytics-admin@example.com", password="Secure-Password-521!", role="ADMIN")
    kind = PropertyType.objects.create(name="Analytics house", slug="analytics-house")
    location = Location.objects.create(name="Analytics city", slug="analytics-city", type="CITY")
    property_obj = Property.objects.create(
        owner=owner, title="Analytics property", slug="analytics-property", property_type=kind,
        listing_type=Property.ListingType.RENT, price="1000", currency="USD", location=location,
        status=Property.Status.PUBLISHED,
    )
    other_property = Property.objects.create(
        owner=other_owner, title="Other property", slug="analytics-other-property", property_type=kind,
        listing_type=Property.ListingType.RENT, price="1000", currency="USD", location=location,
        status=Property.Status.PUBLISHED,
    )
    return {"owner": owner, "other_owner": other_owner, "viewer": viewer, "admin": admin,
            "property": property_obj, "other_property": other_property}


@pytest.mark.django_db
def test_owner_analytics_counts_views_favorites_inquiries_and_visits(analytics_data):
    data = analytics_data
    property_obj = data["property"]
    client = APIClient()
    # Anonymous and other-user detail visits count; owner self-views do not.
    assert client.get(f"/api/properties/{property_obj.pk}/").status_code == 200
    client.force_authenticate(data["viewer"])
    assert client.get(f"/api/properties/{property_obj.pk}/").status_code == 200
    client.force_authenticate(data["owner"])
    assert client.get(f"/api/properties/{property_obj.pk}/").status_code == 200
    assert PropertyView.objects.filter(property=property_obj).count() == 2

    Favorite.objects.create(user=data["viewer"], property=property_obj)
    Inquiry.objects.create(property=property_obj, user=data["viewer"], owner=data["owner"], message="Interested")
    VisitRequest.objects.create(
        property=property_obj, user=data["viewer"], owner=data["owner"],
        requested_date=date(2030, 1, 1), requested_time=time(10, 30), message="Visit please",
    )
    response = client.get(f"/api/owner/properties/{property_obj.pk}/analytics/")
    assert response.status_code == 200
    assert response.data == {"views": 2, "favorites": 1, "inquiries": 1, "visit_requests": 1}


@pytest.mark.django_db
def test_owner_analytics_are_private_and_admin_gets_global_totals(analytics_data):
    data = analytics_data
    client = APIClient()
    assert client.get(f"/api/owner/properties/{data['property'].pk}/analytics/").status_code == 401

    client.force_authenticate(data["other_owner"])
    forbidden_property = client.get(f"/api/owner/properties/{data['property'].pk}/analytics/")
    assert forbidden_property.status_code == 404
    client.force_authenticate(data["viewer"])
    forbidden_role = client.get(f"/api/owner/properties/{data['other_property'].pk}/analytics/")
    assert forbidden_role.status_code == 403

    PropertyView.objects.create(property=data["property"])
    PropertyView.objects.create(property=data["other_property"])
    Favorite.objects.create(user=data["viewer"], property=data["property"])
    client.force_authenticate(data["admin"])
    response = client.get("/api/admin/analytics/")
    assert response.status_code == 200
    assert response.data == {"views": 2, "favorites": 1, "inquiries": 0, "visit_requests": 0}


@pytest.mark.django_db
def test_admin_analytics_endpoint_is_admin_only(analytics_data):
    client = APIClient()
    assert client.get("/api/admin/analytics/").status_code == 401
    client.force_authenticate(analytics_data["viewer"])
    assert client.get("/api/admin/analytics/").status_code == 403
