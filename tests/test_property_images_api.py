from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.locations.models import Location
from apps.properties.models import Property, PropertyImage, PropertyType

User = get_user_model()
PASSWORD = "Secure-Password-521!"


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def property_dependencies(db):
    property_type = PropertyType.objects.create(name="Image House", slug="image-house")
    location = Location.objects.create(name="Image City", slug="image-city", type="CITY")
    owner = User.objects.create_user(email="image-owner@example.com", password=PASSWORD, role=User.Role.OWNER)
    agent = User.objects.create_user(email="image-agent@example.com", password=PASSWORD, role=User.Role.AGENT)
    other_owner = User.objects.create_user(email="image-other@example.com", password=PASSWORD, role=User.Role.OWNER)
    normal_user = User.objects.create_user(email="image-user@example.com", password=PASSWORD)
    admin = User.objects.create_user(email="image-admin@example.com", password=PASSWORD, role=User.Role.ADMIN)
    return property_type, location, owner, agent, other_owner, normal_user, admin


def create_property(owner, property_type, location):
    return Property.objects.create(
        owner=owner,
        title="Image test property",
        slug=f"image-test-{owner.pk}",
        property_type=property_type,
        listing_type=Property.ListingType.RENT,
        price="1000.00",
        currency="USD",
        location=location,
    )


def image_file(name="photo.png", image_format="PNG", size=(40, 30)):
    content = BytesIO()
    Image.new("RGB", size, color="blue").save(content, format=image_format)
    mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}[image_format]
    return SimpleUploadedFile(name, content.getvalue(), content_type=mime)


def login(client, user):
    token = AccessToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")


def mocked_upload(monkeypatch):
    counter = iter(range(1, 100))

    def upload(_file):
        value = next(counter)
        return {"url": f"https://res.cloudinary.com/demo/image/upload/{value}.png", "public_id": f"test/{value}"}

    monkeypatch.setattr("apps.properties.image_views.upload_property_image", upload)


@pytest.mark.django_db
def test_owner_can_upload_images_and_first_image_is_primary(
    api_client, property_dependencies, monkeypatch
):
    _, location, owner, *_ = property_dependencies
    property_obj = create_property(owner, property_dependencies[0], location)
    mocked_upload(monkeypatch)
    login(api_client, owner)

    first = api_client.post(
        f"/api/properties/{property_obj.pk}/images/",
        {"image": image_file(), "alt_text": "Front facade"},
        format="multipart",
    )
    second = api_client.post(
        f"/api/properties/{property_obj.pk}/images/",
        {"image": image_file("second.png"), "alt_text": "Living room"},
        format="multipart",
    )

    assert first.status_code == 201, first.data
    assert second.status_code == 201, second.data
    assert first.data["url"].startswith("https://res.cloudinary.com/")
    assert first.data["public_id"] == "test/1"
    assert first.data["sort_order"] == 0
    assert first.data["is_primary"] is True
    assert second.data["sort_order"] == 1
    assert second.data["is_primary"] is False
    assert PropertyImage.objects.filter(property=property_obj).count() == 2


@pytest.mark.django_db
def test_only_owner_or_admin_can_upload_images(api_client, property_dependencies, monkeypatch):
    _, location, owner, _, other_owner, normal_user, admin = property_dependencies
    property_obj = create_property(owner, property_dependencies[0], location)
    mocked_upload(monkeypatch)
    url = f"/api/properties/{property_obj.pk}/images/"

    assert api_client.post(url, {"image": image_file()}, format="multipart").status_code == 401
    login(api_client, normal_user)
    assert api_client.post(url, {"image": image_file()}, format="multipart").status_code == 403
    login(api_client, other_owner)
    assert api_client.post(url, {"image": image_file()}, format="multipart").status_code == 403
    login(api_client, admin)
    assert api_client.post(url, {"image": image_file()}, format="multipart").status_code == 201


@pytest.mark.django_db
def test_agent_can_manage_images_only_on_the_agents_own_property(api_client, property_dependencies, monkeypatch):
    property_type, location, _, agent, other_owner, *_ = property_dependencies
    agent_property = create_property(agent, property_type, location)
    other_property = create_property(other_owner, property_type, location)
    mocked_upload(monkeypatch)
    login(api_client, agent)

    allowed = api_client.post(
        f"/api/properties/{agent_property.pk}/images/", {"image": image_file()}, format="multipart"
    )
    denied = api_client.post(
        f"/api/properties/{other_property.pk}/images/", {"image": image_file("other.png")}, format="multipart"
    )

    assert allowed.status_code == 201
    assert denied.status_code == 403


@pytest.mark.django_db
def test_upload_rejects_unsupported_or_oversized_files(api_client, property_dependencies, monkeypatch):
    _, location, owner, *_ = property_dependencies
    property_obj = create_property(owner, property_dependencies[0], location)
    mocked_upload(monkeypatch)
    login(api_client, owner)
    url = f"/api/properties/{property_obj.pk}/images/"

    unsupported = api_client.post(
        url, {"image": image_file("animated.gif", "GIF")}, format="multipart"
    )
    assert unsupported.status_code == 400
    assert "image" in unsupported.data

    with override_settings(PROPERTY_IMAGE_MAX_UPLOAD_SIZE=10):
        oversized = api_client.post(url, {"image": image_file()}, format="multipart")
    assert oversized.status_code == 400
    assert "image" in oversized.data
    assert PropertyImage.objects.filter(property=property_obj).count() == 0


@pytest.mark.django_db
def test_patch_primary_and_sort_order_preserves_uniqueness(api_client, property_dependencies, monkeypatch):
    _, location, owner, *_ = property_dependencies
    property_obj = create_property(owner, property_dependencies[0], location)
    mocked_upload(monkeypatch)
    login(api_client, owner)
    collection_url = f"/api/properties/{property_obj.pk}/images/"
    images = [
        api_client.post(collection_url, {"image": image_file(f"{number}.png")}, format="multipart").data
        for number in range(3)
    ]

    result = api_client.patch(
        f"{collection_url}{images[2]['id']}/",
        {"is_primary": True, "sort_order": 0, "alt_text": "Best view"},
        format="json",
    )

    assert result.status_code == 200, result.data
    assert result.data["sort_order"] == 0
    assert result.data["is_primary"] is True
    assert result.data["alt_text"] == "Best view"
    stored = list(PropertyImage.objects.filter(property=property_obj).order_by("sort_order"))
    assert [item.sort_order for item in stored] == [0, 1, 2]
    assert sum(item.is_primary for item in stored) == 1
    assert str(stored[0].pk) == images[2]["id"]


@pytest.mark.django_db
def test_delete_removes_cloud_asset_and_compacts_order(api_client, property_dependencies, monkeypatch):
    _, location, owner, *_ = property_dependencies
    property_obj = create_property(owner, property_dependencies[0], location)
    mocked_upload(monkeypatch)
    deleted_assets = []
    monkeypatch.setattr(
        "apps.properties.image_views.delete_cloudinary_image",
        lambda public_id: deleted_assets.append(public_id) or {"result": "ok"},
    )
    login(api_client, owner)
    collection_url = f"/api/properties/{property_obj.pk}/images/"
    images = [
        api_client.post(collection_url, {"image": image_file(f"{number}.png")}, format="multipart").data
        for number in range(2)
    ]

    response = api_client.delete(f"{collection_url}{images[0]['id']}/")

    remaining = PropertyImage.objects.get(property=property_obj)
    assert response.status_code == 204
    assert deleted_assets == ["test/1"]
    assert remaining.sort_order == 0
    assert remaining.is_primary is True


@pytest.mark.django_db
def test_another_owner_cannot_patch_or_delete_property_image(api_client, property_dependencies, monkeypatch):
    _, location, owner, _, other_owner, *_ = property_dependencies
    property_obj = create_property(owner, property_dependencies[0], location)
    mocked_upload(monkeypatch)
    login(api_client, owner)
    url = f"/api/properties/{property_obj.pk}/images/"
    uploaded = api_client.post(url, {"image": image_file()}, format="multipart").data

    login(api_client, other_owner)
    detail_url = f"{url}{uploaded['id']}/"
    assert api_client.patch(detail_url, {"alt_text": "changed"}, format="json").status_code == 403
    assert api_client.delete(detail_url).status_code == 403
