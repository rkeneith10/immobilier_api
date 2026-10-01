import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.locations.models import Location

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def location_tree(db):
    country = Location.objects.create(name="Haïti", slug="haiti", type=Location.Type.COUNTRY)
    department = Location.objects.create(
        name="Nord", slug="nord", type=Location.Type.DEPARTMENT, parent=country
    )
    city = Location.objects.create(
        name="Cap-Haïtien", slug="cap-haitien", type=Location.Type.CITY, parent=department
    )
    neighborhood = Location.objects.create(
        name="Vertières", slug="vertieres", type=Location.Type.NEIGHBORHOOD, parent=city
    )
    return country, department, city, neighborhood


def authenticate_as(client, user):
    token = AccessToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")


def location_data(**overrides):
    data = {"name": "Vaudreuil", "slug": "vaudreuil", "type": "NEIGHBORHOOD"}
    data.update(overrides)
    return data


@pytest.mark.django_db
def test_locations_are_public_and_return_parent_ids(api_client, location_tree):
    country, department, city, neighborhood = location_tree

    response = api_client.get("/api/locations/")
    detail = api_client.get(f"/api/locations/{neighborhood.pk}/")

    assert response.status_code == 200
    assert response.data["count"] == 4
    assert detail.status_code == 200
    assert detail.data["name"] == "Vertières"
    assert str(detail.data["parent"]) == str(city.pk)
    assert "Haïti" in [item["name"] for item in response.data["results"]]


@pytest.mark.django_db
def test_locations_can_be_searched_by_name_or_slug(api_client, location_tree):
    by_name = api_client.get("/api/locations/", {"search": "Cap-Haïtien"})
    by_slug = api_client.get("/api/locations/", {"search": "vertieres"})

    assert [item["slug"] for item in by_name.data["results"]] == ["cap-haitien"]
    assert [item["slug"] for item in by_slug.data["results"]] == ["vertieres"]


@pytest.mark.django_db
def test_location_type_and_parent_filters(api_client, location_tree):
    _, department, _, _ = location_tree

    response = api_client.get(
        "/api/locations/",
        {"type": "CITY", "parent": str(department.pk)},
    )

    assert response.status_code == 200
    assert response.data["count"] == 1
    assert response.data["results"][0]["slug"] == "cap-haitien"


@pytest.mark.django_db
def test_location_pagination_supports_page_and_page_size(api_client):
    for index in range(23):
        Location.objects.create(
            name=f"Area {index}", slug=f"area-{index}", type=Location.Type.AREA
        )

    first_page = api_client.get("/api/locations/", {"page_size": 5})
    second_page = api_client.get("/api/locations/", {"page": 2, "page_size": 5})

    assert first_page.status_code == 200
    assert first_page.data["count"] == 23
    assert len(first_page.data["results"]) == 5
    assert len(second_page.data["results"]) == 5


@pytest.mark.django_db
def test_anonymous_and_regular_users_cannot_write_locations(api_client, location_tree):
    _, _, city, neighborhood = location_tree
    anonymous_create = api_client.post("/api/locations/", location_data(), format="json")
    assert anonymous_create.status_code in (401, 403)

    user = User.objects.create_user(email="normal@example.com", password="Secure-Password-521!")
    authenticate_as(api_client, user)
    create = api_client.post("/api/locations/", location_data(), format="json")
    update = api_client.patch(
        f"/api/locations/{neighborhood.pk}/", {"name": "Changed"}, format="json"
    )
    delete = api_client.delete(f"/api/locations/{neighborhood.pk}/")

    assert create.status_code == 403
    assert update.status_code == 403
    assert delete.status_code == 403
    assert Location.objects.filter(pk=neighborhood.pk).exists()
    assert Location.objects.filter(pk=city.pk).exists()


@pytest.mark.django_db
def test_admin_can_create_update_and_delete_location(api_client):
    admin = User.objects.create_user(
        email="admin@example.com", password="Secure-Password-521!", role=User.Role.ADMIN
    )
    authenticate_as(api_client, admin)

    created = api_client.post("/api/locations/", location_data(), format="json")
    assert created.status_code == 201
    location_id = created.data["id"]

    updated = api_client.patch(
        f"/api/locations/{location_id}/", {"description": "North side"}, format="json"
    )
    assert updated.status_code == 200
    assert updated.data["description"] == "North side"

    deleted = api_client.delete(f"/api/locations/{location_id}/")
    assert deleted.status_code == 204
    assert not Location.objects.filter(pk=location_id).exists()


@pytest.mark.django_db
def test_public_cannot_see_inactive_locations_but_admin_can(api_client):
    inactive = Location.objects.create(
        name="Closed area", slug="closed-area", type=Location.Type.AREA, is_active=False
    )

    public_list = api_client.get("/api/locations/")
    public_detail = api_client.get(f"/api/locations/{inactive.pk}/")
    assert public_list.data["count"] == 0
    assert public_detail.status_code == 404

    admin = User.objects.create_user(
        email="admin@example.com", password="Secure-Password-521!", role=User.Role.ADMIN
    )
    authenticate_as(api_client, admin)
    admin_detail = api_client.get(f"/api/locations/{inactive.pk}/")
    assert admin_detail.status_code == 200


@pytest.mark.django_db
def test_location_rejects_duplicate_sibling_slug_case_insensitively(api_client, location_tree):
    _, _, city, _ = location_tree
    admin = User.objects.create_user(
        email="admin@example.com", password="Secure-Password-521!", role=User.Role.ADMIN
    )
    authenticate_as(api_client, admin)

    response = api_client.post(
        "/api/locations/",
        location_data(slug="VERTIERES", parent=str(city.pk)),
        format="json",
    )

    assert response.status_code == 400
    assert "slug" in response.data


@pytest.mark.django_db
def test_location_validates_coordinates_and_prevents_parent_cycles(api_client, location_tree):
    _, _, city, neighborhood = location_tree
    admin = User.objects.create_user(
        email="admin@example.com", password="Secure-Password-521!", role=User.Role.ADMIN
    )
    authenticate_as(api_client, admin)

    invalid_coordinates = api_client.post(
        "/api/locations/",
        location_data(latitude=91, longitude=72),
        format="json",
    )
    cycle = api_client.patch(
        f"/api/locations/{city.pk}/", {"parent": str(neighborhood.pk)}, format="json"
    )

    assert invalid_coordinates.status_code == 400
    assert "latitude" in invalid_coordinates.data
    assert cycle.status_code == 400
    assert "parent" in cycle.data


@pytest.mark.django_db
def test_location_with_children_cannot_be_deleted(api_client, location_tree):
    country, _, _, _ = location_tree
    admin = User.objects.create_user(
        email="admin@example.com", password="Secure-Password-521!", role=User.Role.ADMIN
    )
    authenticate_as(api_client, admin)

    response = api_client.delete(f"/api/locations/{country.pk}/")

    assert response.status_code == 400
    assert Location.objects.filter(pk=country.pk).exists()

