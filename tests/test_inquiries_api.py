import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.interactions.models import Inquiry
from apps.locations.models import Location
from apps.properties.models import Property, PropertyType

User = get_user_model()
PASSWORD = "Secure-Password-521!"


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def inquiry_dependencies(db):
    client_one = User.objects.create_user(email="inquiry-client1@example.com", password=PASSWORD)
    client_two = User.objects.create_user(email="inquiry-client2@example.com", password=PASSWORD)
    owner_one = User.objects.create_user(
        email="inquiry-owner1@example.com", password=PASSWORD, role=User.Role.OWNER
    )
    owner_two = User.objects.create_user(
        email="inquiry-owner2@example.com", password=PASSWORD, role=User.Role.AGENT
    )
    admin = User.objects.create_user(
        email="inquiry-admin@example.com", password=PASSWORD, role=User.Role.ADMIN
    )
    property_type = PropertyType.objects.create(name="Inquiry house", slug="inquiry-house")
    location = Location.objects.create(name="Inquiry city", slug="inquiry-city", type="CITY")
    return client_one, client_two, owner_one, owner_two, admin, property_type, location


def authenticate(client, user):
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")


def make_property(owner, property_type, location, slug, status=Property.Status.PUBLISHED):
    return Property.objects.create(
        owner=owner,
        title=slug.replace("-", " ").title(),
        slug=slug,
        property_type=property_type,
        listing_type=Property.ListingType.RENT,
        price="1250.00",
        currency="USD",
        location=location,
        status=status,
    )


def send_inquiry(client, property_obj, message="I would like to schedule a visit.", **extra):
    payload = {"message": message, **extra}
    return client.post(f"/api/properties/{property_obj.pk}/inquiries/", payload, format="json")


@pytest.mark.django_db
def test_inquiry_endpoints_require_authentication(api_client, inquiry_dependencies):
    _, _, owner, _, _, property_type, location = inquiry_dependencies
    property_obj = make_property(owner, property_type, location, "auth-required-inquiry")

    assert api_client.get("/api/inquiries/").status_code == 401
    assert api_client.post("/api/inquiries/00000000-0000-0000-0000-000000000001/").status_code == 401
    assert send_inquiry(api_client, property_obj).status_code == 401


@pytest.mark.django_db
def test_authenticated_user_can_send_inquiry_owner_is_derived_from_property(
    api_client, inquiry_dependencies
):
    client, _, owner, _, _, property_type, location = inquiry_dependencies
    property_obj = make_property(owner, property_type, location, "valid-inquiry")
    authenticate(api_client, client)

    response = send_inquiry(api_client, property_obj, "Is the property available?")

    assert response.status_code == 201, response.data
    inquiry = Inquiry.objects.get(pk=response.data["id"])
    assert inquiry.user_id == client.pk
    assert inquiry.owner_id == property_obj.owner_id == owner.pk
    assert inquiry.property_id == property_obj.pk
    assert inquiry.message == "Is the property available?"
    assert inquiry.status == Inquiry.Status.NEW
    assert response.data["owner"] == owner.pk
    assert "created_at" in response.data and "updated_at" in response.data


@pytest.mark.django_db
def test_client_cannot_choose_owner_user_or_property_ids(api_client, inquiry_dependencies):
    client, _, owner, other_owner, _, property_type, location = inquiry_dependencies
    property_obj = make_property(owner, property_type, location, "owner-spoof-inquiry")
    authenticate(api_client, client)

    spoof_owner = send_inquiry(api_client, property_obj, owner_id=str(other_owner.pk))
    spoof_owner_alias = send_inquiry(api_client, property_obj, owner=str(other_owner.pk))
    spoof_user = send_inquiry(api_client, property_obj, user_id=str(other_owner.pk))
    spoof_property = send_inquiry(
        api_client, property_obj, property_id="00000000-0000-0000-0000-000000000001"
    )

    assert spoof_owner.status_code == 400
    assert "owner_id" in spoof_owner.data
    assert spoof_owner_alias.status_code == 400
    assert "owner" in spoof_owner_alias.data
    assert spoof_user.status_code == 400
    assert "user_id" in spoof_user.data
    assert spoof_property.status_code == 400
    assert "property_id" in spoof_property.data
    assert Inquiry.objects.count() == 0


@pytest.mark.django_db
def test_only_published_properties_can_receive_inquiries(api_client, inquiry_dependencies):
    client, _, owner, _, _, property_type, location = inquiry_dependencies
    draft = make_property(owner, property_type, location, "draft-inquiry", Property.Status.DRAFT)
    authenticate(api_client, client)

    response = send_inquiry(api_client, draft)

    assert response.status_code == 404
    assert Inquiry.objects.count() == 0


@pytest.mark.django_db
def test_owner_cannot_receive_inquiry_on_own_unpublished_property(api_client, inquiry_dependencies):
    _, _, owner, _, _, property_type, location = inquiry_dependencies
    draft = make_property(owner, property_type, location, "own-draft-inquiry", Property.Status.DRAFT)
    authenticate(api_client, owner)

    response = send_inquiry(api_client, draft)

    assert response.status_code == 400
    assert "property" in response.data
    assert Inquiry.objects.count() == 0


@pytest.mark.django_db
def test_inquiry_list_is_limited_to_sender_owner_or_admin(api_client, inquiry_dependencies):
    client_one, client_two, owner_one, owner_two, admin, property_type, location = inquiry_dependencies
    first_property = make_property(owner_one, property_type, location, "list-inquiry-one")
    second_property = make_property(owner_two, property_type, location, "list-inquiry-two")
    first = Inquiry.objects.create(
        property=first_property, user=client_one, owner=owner_one, message="First question"
    )
    second = Inquiry.objects.create(
        property=first_property, user=client_two, owner=owner_one, message="Second question"
    )
    third = Inquiry.objects.create(
        property=second_property, user=client_two, owner=owner_two, message="Third question"
    )

    authenticate(api_client, client_one)
    client_one_list = api_client.get("/api/inquiries/")
    assert client_one_list.data["count"] == 1
    assert str(client_one_list.data["results"][0]["id"]) == str(first.pk)

    authenticate(api_client, owner_one)
    owner_list = api_client.get("/api/inquiries/")
    assert owner_list.data["count"] == 2
    assert {str(row["id"]) for row in owner_list.data["results"]} == {str(first.pk), str(second.pk)}

    authenticate(api_client, admin)
    admin_list = api_client.get("/api/inquiries/")
    assert admin_list.data["count"] == 3
    assert {str(row["id"]) for row in admin_list.data["results"]} == {
        str(first.pk), str(second.pk), str(third.pk)
    }


@pytest.mark.django_db
def test_detail_access_is_limited_to_sender_owner_or_admin(api_client, inquiry_dependencies):
    client_one, client_two, owner_one, owner_two, admin, property_type, location = inquiry_dependencies
    property_obj = make_property(owner_one, property_type, location, "detail-inquiry")
    inquiry = Inquiry.objects.create(
        property=property_obj, user=client_two, owner=owner_one, message="Please call me."
    )
    url = f"/api/inquiries/{inquiry.pk}/"

    authenticate(api_client, client_one)
    assert api_client.get(url).status_code == 404
    assert api_client.patch(url, {"status": "READ"}, format="json").status_code == 404

    authenticate(api_client, client_two)
    assert api_client.get(url).status_code == 200
    assert api_client.patch(url, {"status": "RESPONDED"}, format="json").status_code == 400
    closed = api_client.patch(url, {"status": "CLOSED"}, format="json")
    assert closed.status_code == 200
    assert closed.data["status"] == Inquiry.Status.CLOSED

    authenticate(api_client, owner_one)
    owner_detail = api_client.get(url)
    assert owner_detail.status_code == 200
    updated = api_client.patch(url, {"status": "RESPONDED"}, format="json")
    assert updated.status_code == 200
    assert updated.data["status"] == Inquiry.Status.RESPONDED

    authenticate(api_client, admin)
    admin_updated = api_client.patch(url, {"status": "READ"}, format="json")
    assert admin_updated.status_code == 200
    assert admin_updated.data["status"] == Inquiry.Status.READ


@pytest.mark.django_db
def test_inquiry_patch_cannot_modify_owner_or_message(api_client, inquiry_dependencies):
    client, _, owner, other_owner, _, property_type, location = inquiry_dependencies
    property_obj = make_property(owner, property_type, location, "patch-inquiry")
    inquiry = Inquiry.objects.create(
        property=property_obj, user=client, owner=owner, message="Original message"
    )
    authenticate(api_client, owner)
    url = f"/api/inquiries/{inquiry.pk}/"

    spoofed = api_client.patch(url, {"owner_id": str(other_owner.pk)}, format="json")
    changed_message = api_client.patch(url, {"message": "Changed"}, format="json")

    assert spoofed.status_code == 400
    assert "owner_id" in spoofed.data
    assert changed_message.status_code == 400
    assert "message" in changed_message.data
    inquiry.refresh_from_db()
    assert inquiry.owner_id == owner.pk
    assert inquiry.message == "Original message"
