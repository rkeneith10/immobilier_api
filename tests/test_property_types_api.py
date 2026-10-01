import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.properties.models import PropertyType

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


def authenticate_as(client, user):
    token = AccessToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")


def admin_user(email="admin@example.com"):
    return User.objects.create_user(
        email=email,
        password="Secure-Password-521!",
        role=User.Role.ADMIN,
    )


def property_type_data(**overrides):
    data = {"name": "House", "slug": "house", "description": "Standalone home"}
    data.update(overrides)
    return data


@pytest.mark.django_db
def test_property_types_are_public_and_use_uuid_details(api_client):
    property_type = PropertyType.objects.create(name="House", slug="house")

    listing = api_client.get("/api/properties/types/")
    detail = api_client.get(f"/api/properties/types/{property_type.pk}/")

    assert listing.status_code == 200
    assert listing.data["count"] == 1
    assert listing.data["results"][0]["name"] == "House"
    assert detail.status_code == 200
    assert str(detail.data["id"]) == str(property_type.pk)
    assert "created_at" in detail.data
    assert "updated_at" in detail.data


@pytest.mark.django_db
def test_property_types_can_be_searched_by_name_and_slug(api_client):
    PropertyType.objects.create(name="Commercial space", slug="commercial")
    PropertyType.objects.create(name="Apartment", slug="apartment")

    by_name = api_client.get("/api/properties/types/", {"search": "Commercial"})
    by_slug = api_client.get("/api/properties/types/", {"search": "apartment"})

    assert [item["slug"] for item in by_name.data["results"]] == ["commercial"]
    assert [item["slug"] for item in by_slug.data["results"]] == ["apartment"]


@pytest.mark.django_db
def test_property_type_pagination_and_active_filter(api_client):
    for index in range(7):
        PropertyType.objects.create(name=f"Type {index}", slug=f"type-{index}")
    PropertyType.objects.create(name="Inactive", slug="inactive", is_active=False)

    page = api_client.get("/api/properties/types/", {"page_size": 3, "page": 2})
    inactive_public = api_client.get("/api/properties/types/", {"is_active": "false"})

    assert page.status_code == 200
    assert page.data["count"] == 7
    assert len(page.data["results"]) == 3
    assert inactive_public.data["count"] == 0

    authenticate_as(api_client, admin_user())
    inactive_admin = api_client.get("/api/properties/types/", {"is_active": "false"})
    assert inactive_admin.data["count"] == 1


@pytest.mark.django_db
def test_only_admin_can_create_update_and_delete_property_types(api_client):
    anonymous = api_client.post("/api/properties/types/", property_type_data(), format="json")
    assert anonymous.status_code in (401, 403)

    user = User.objects.create_user(email="user@example.com", password="Secure-Password-521!")
    existing = PropertyType.objects.create(name="Apartment", slug="apartment")
    authenticate_as(api_client, user)
    denied_create = api_client.post(
        "/api/properties/types/", property_type_data(), format="json"
    )
    denied_update = api_client.patch(
        f"/api/properties/types/{existing.pk}/", {"name": "Flat"}, format="json"
    )
    denied_delete = api_client.delete(f"/api/properties/types/{existing.pk}/")
    assert denied_create.status_code == 403
    assert denied_update.status_code == 403
    assert denied_delete.status_code == 403

    authenticate_as(api_client, admin_user())
    created = api_client.post("/api/properties/types/", property_type_data(), format="json")
    assert created.status_code == 201
    property_type_id = created.data["id"]

    updated = api_client.patch(
        f"/api/properties/types/{property_type_id}/",
        {"description": "Updated description"},
        format="json",
    )
    assert updated.status_code == 200
    assert updated.data["description"] == "Updated description"

    deleted = api_client.delete(f"/api/properties/types/{property_type_id}/")
    assert deleted.status_code == 204
    assert not PropertyType.objects.filter(pk=property_type_id).exists()


@pytest.mark.django_db
def test_property_type_names_and_slugs_are_unique_case_insensitively(api_client):
    PropertyType.objects.create(name="House", slug="house")
    authenticate_as(api_client, admin_user())

    duplicate_name = api_client.post(
        "/api/properties/types/",
        property_type_data(name="HOUSE", slug="detached-house"),
        format="json",
    )
    duplicate_slug = api_client.post(
        "/api/properties/types/",
        property_type_data(name="Detached home", slug="HOUSE"),
        format="json",
    )

    assert duplicate_name.status_code == 400
    assert "name" in duplicate_name.data
    assert duplicate_slug.status_code == 400
    assert "slug" in duplicate_slug.data


@pytest.mark.django_db
def test_initial_category_examples_are_database_rows_not_property_choices():
    examples = (
        ("HOUSE", "house"),
        ("APARTMENT", "apartment"),
        ("STUDIO", "studio"),
        ("VILLA", "villa"),
        ("ROOM", "room"),
        ("COMMERCIAL", "commercial"),
        ("LAND", "land"),
    )
    for name, slug in examples:
        PropertyType.objects.create(name=name.title(), slug=slug)

    assert PropertyType.objects.count() == len(examples)
    assert PropertyType._meta.get_field("name").choices is None
    # PropertyType remains a database-backed relation now that Property exists;
    # its available categories must not be encoded as choices on Property.
    from apps.properties.models import Property

    property_type_field = Property._meta.get_field("property_type")
    assert property_type_field.remote_field.model is PropertyType
    assert property_type_field.choices is None
