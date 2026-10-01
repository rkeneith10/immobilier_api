import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.locations.models import Location
from apps.properties.models import Amenity, Property, PropertyAmenity, PropertyType

User = get_user_model()
PASSWORD = "Secure-Password-521!"


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def amenity_dependencies(db):
    owner = User.objects.create_user(email="amenity-owner@example.com", password=PASSWORD, role=User.Role.OWNER)
    normal_user = User.objects.create_user(email="amenity-user@example.com", password=PASSWORD)
    admin = User.objects.create_user(email="amenity-admin@example.com", password=PASSWORD, role=User.Role.ADMIN)
    property_type = PropertyType.objects.create(name="Amenity house", slug="amenity-house")
    location = Location.objects.create(name="Amenity city", slug="amenity-city", type="CITY")
    return owner, normal_user, admin, property_type, location


def authenticate_as(client, user):
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")


def amenity_payload(**overrides):
    payload = {"name": "Swimming pool", "slug": "swimming-pool", "icon": "pool"}
    payload.update(overrides)
    return payload


def property_payload(property_type, location, slug="amenities-property", **overrides):
    payload = {
        "title": "Home with amenities",
        "slug": slug,
        "description": "A property listing.",
        "property_type": str(property_type.pk),
        "listing_type": "RENT",
        "price": "1200.00",
        "currency": "USD",
        "location": str(location.pk),
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
def test_amenities_are_public_searchable_and_inactive_amenities_hidden(api_client):
    active = Amenity.objects.create(name="Swimming pool", slug="pool")
    Amenity.objects.create(name="Elevator", slug="lift")
    Amenity.objects.create(name="Inactive", slug="inactive", is_active=False)

    listing = api_client.get("/api/properties/amenities/")
    detail = api_client.get(f"/api/properties/amenities/{active.pk}/")
    search = api_client.get("/api/properties/amenities/", {"search": "pool"})
    inactive = api_client.get("/api/properties/amenities/", {"is_active": "false"})

    assert listing.status_code == 200
    assert listing.data["count"] == 2
    assert detail.status_code == 200
    assert detail.data["name"] == "Swimming pool"
    assert search.data["count"] == 1
    assert search.data["results"][0]["slug"] == "pool"
    assert inactive.data["count"] == 0


@pytest.mark.django_db
def test_only_admin_can_create_update_and_delete_amenities(api_client, amenity_dependencies):
    owner, normal_user, admin, *_ = amenity_dependencies
    create_anonymous = api_client.post("/api/properties/amenities/", amenity_payload(), format="json")
    assert create_anonymous.status_code in (401, 403)

    authenticate_as(api_client, owner)
    denied_owner = api_client.post("/api/properties/amenities/", amenity_payload(), format="json")
    assert denied_owner.status_code == 403

    existing = Amenity.objects.create(name="Balcony", slug="balcony")
    authenticate_as(api_client, normal_user)
    denied_update = api_client.patch(
        f"/api/properties/amenities/{existing.pk}/", {"name": "Terrace"}, format="json"
    )
    denied_delete = api_client.delete(f"/api/properties/amenities/{existing.pk}/")
    assert denied_update.status_code == 403
    assert denied_delete.status_code == 403

    authenticate_as(api_client, admin)
    created = api_client.post("/api/properties/amenities/", amenity_payload(), format="json")
    updated = api_client.patch(
        f"/api/properties/amenities/{existing.pk}/", {"name": "Terrace"}, format="json"
    )
    deleted = api_client.delete(f"/api/properties/amenities/{existing.pk}/")
    assert created.status_code == 201, created.data
    assert created.data["icon"] == "pool"
    assert updated.status_code == 200
    assert updated.data["name"] == "Terrace"
    assert deleted.status_code == 204


@pytest.mark.django_db
def test_owner_can_associate_existing_amenities_on_create_and_update(api_client, amenity_dependencies):
    owner, _, _, property_type, location = amenity_dependencies
    pool = Amenity.objects.create(name="Swimming pool", slug="pool")
    parking = Amenity.objects.create(name="Parking", slug="parking")
    authenticate_as(api_client, owner)

    created = api_client.post(
        "/api/properties/",
        property_payload(property_type, location, amenities=[str(pool.pk)]),
        format="json",
    )

    assert created.status_code == 201, created.data
    assert [str(pk) for pk in created.data["amenities"]] == [str(pool.pk)]
    property_obj = Property.objects.get(pk=created.data["id"])
    assert PropertyAmenity.objects.filter(property=property_obj, amenity=pool).exists()

    updated = api_client.patch(
        f"/api/properties/{property_obj.pk}/",
        {"amenities": [str(pool.pk), str(parking.pk)]},
        format="json",
    )
    assert updated.status_code == 200, updated.data
    assert {str(pk) for pk in updated.data["amenities"]} == {str(pool.pk), str(parking.pk)}
    assert property_obj.amenities.count() == 2


@pytest.mark.django_db
def test_owner_cannot_create_or_attach_arbitrary_or_inactive_amenities(api_client, amenity_dependencies):
    owner, _, _, property_type, location = amenity_dependencies
    inactive = Amenity.objects.create(name="Inactive amenity", slug="inactive-amenity", is_active=False)
    authenticate_as(api_client, owner)

    forged = api_client.post(
        "/api/properties/amenities/", amenity_payload(name="Forged", slug="forged"), format="json"
    )
    arbitrary = api_client.post(
        "/api/properties/",
        property_payload(property_type, location, slug="arbitrary-amenity", amenities=["00000000-0000-0000-0000-000000000001"]),
        format="json",
    )
    inactive_association = api_client.post(
        "/api/properties/",
        property_payload(property_type, location, slug="inactive-association", amenities=[str(inactive.pk)]),
        format="json",
    )

    assert forged.status_code == 403
    assert arbitrary.status_code == 400
    assert "amenities" in arbitrary.data
    assert inactive_association.status_code == 400
    assert "amenities" in inactive_association.data
    assert Amenity.objects.filter(slug="forged").count() == 0
