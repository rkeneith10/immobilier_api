from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.interactions.models import VisitRequest
from apps.locations.models import Location
from apps.properties.models import Property, PropertyType

User = get_user_model()
PASSWORD = "Secure-Password-521!"


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def visit_dependencies(db):
    client_one = User.objects.create_user(email="visit-client1@example.com", password=PASSWORD)
    client_two = User.objects.create_user(email="visit-client2@example.com", password=PASSWORD)
    owner_one = User.objects.create_user(
        email="visit-owner1@example.com", password=PASSWORD, role=User.Role.OWNER
    )
    owner_two = User.objects.create_user(
        email="visit-owner2@example.com", password=PASSWORD, role=User.Role.AGENT
    )
    admin = User.objects.create_user(email="visit-admin@example.com", password=PASSWORD, role=User.Role.ADMIN)
    property_type = PropertyType.objects.create(name="Visit house", slug="visit-house")
    location = Location.objects.create(name="Visit city", slug="visit-city", type="CITY")
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
        price="1400.00",
        currency="USD",
        location=location,
        status=status,
    )


def future_schedule(days=2):
    return {
        "requested_date": (timezone.localdate() + timedelta(days=days)).isoformat(),
        "requested_time": "12:00:00",
    }


def create_visit(client, property_obj, **payload):
    data = {**future_schedule(), "message": "I would like to see the property.", **payload}
    return client.post(f"/api/properties/{property_obj.pk}/visits/", data, format="json")


@pytest.mark.django_db
def test_visit_request_requires_authentication(api_client, visit_dependencies):
    _, _, owner, _, _, property_type, location = visit_dependencies
    property_obj = make_property(owner, property_type, location, "visit-auth-property")

    response = create_visit(api_client, property_obj)

    assert response.status_code == 401
    assert VisitRequest.objects.count() == 0


@pytest.mark.django_db
def test_authenticated_user_can_request_visit_and_owner_is_derived(api_client, visit_dependencies):
    client, _, owner, _, _, property_type, location = visit_dependencies
    property_obj = make_property(owner, property_type, location, "visit-create-property")
    authenticate(api_client, client)

    response = create_visit(api_client, property_obj)

    assert response.status_code == 201, response.data
    visit = VisitRequest.objects.get(pk=response.data["id"])
    assert visit.property_id == property_obj.pk
    assert visit.user_id == client.pk
    assert visit.owner_id == property_obj.owner_id == owner.pk
    assert visit.status == VisitRequest.Status.PENDING
    assert visit.requested_date.isoformat() == response.data["requested_date"]
    assert "created_at" in response.data and "updated_at" in response.data


@pytest.mark.django_db
def test_client_cannot_supply_owner_user_property_or_status(api_client, visit_dependencies):
    client, _, owner, other_owner, _, property_type, location = visit_dependencies
    property_obj = make_property(owner, property_type, location, "visit-spoof-property")
    authenticate(api_client, client)
    base = future_schedule()

    for field, value in (
        ("owner_id", str(other_owner.pk)),
        ("owner", str(other_owner.pk)),
        ("user_id", str(other_owner.pk)),
        ("property_id", "00000000-0000-0000-0000-000000000001"),
        ("status", VisitRequest.Status.ACCEPTED),
    ):
        response = create_visit(api_client, property_obj, **{**base, field: value})
        assert response.status_code == 400, (field, response.data)
        assert field in response.data

    assert VisitRequest.objects.count() == 0


@pytest.mark.django_db
def test_visit_requests_only_for_published_properties(api_client, visit_dependencies):
    client, _, owner, _, _, property_type, location = visit_dependencies
    draft = make_property(owner, property_type, location, "visit-draft", Property.Status.DRAFT)
    authenticate(api_client, client)

    hidden = create_visit(api_client, draft)
    assert hidden.status_code == 404

    authenticate(api_client, owner)
    own_draft = create_visit(api_client, draft)
    assert own_draft.status_code == 400
    assert "property" in own_draft.data
    assert VisitRequest.objects.count() == 0


@pytest.mark.django_db
def test_visit_date_and_time_must_be_valid_and_in_the_future(api_client, visit_dependencies):
    client, _, owner, _, _, property_type, location = visit_dependencies
    property_obj = make_property(owner, property_type, location, "visit-date-validation")
    authenticate(api_client, client)

    past_date = timezone.localdate() - timedelta(days=1)
    past = create_visit(
        api_client,
        property_obj,
        requested_date=past_date.isoformat(),
        requested_time="12:00:00",
    )
    past_time_local = timezone.localtime() - timedelta(hours=2)
    same_day_past_time = create_visit(
        api_client,
        property_obj,
        requested_date=past_time_local.date().isoformat(),
        requested_time=past_time_local.time().replace(second=0, microsecond=0).isoformat(),
    )
    invalid_date = create_visit(api_client, property_obj, requested_date="not-a-date")
    invalid_time = create_visit(api_client, property_obj, requested_time="25:61")

    assert past.status_code == 400
    assert "requested_date" in past.data
    assert same_day_past_time.status_code == 400
    assert "requested_date" in same_day_past_time.data
    assert invalid_date.status_code == 400
    assert "requested_date" in invalid_date.data
    assert invalid_time.status_code == 400
    assert "requested_time" in invalid_time.data
    assert VisitRequest.objects.count() == 0


@pytest.mark.django_db
def test_requester_owner_and_admin_visibility(api_client, visit_dependencies):
    client_one, client_two, owner_one, owner_two, admin, property_type, location = visit_dependencies
    property_one = make_property(owner_one, property_type, location, "visit-list-one")
    property_two = make_property(owner_two, property_type, location, "visit-list-two")
    first = VisitRequest.objects.create(
        property=property_one,
        user=client_one,
        owner=owner_one,
        requested_date=timezone.localdate() + timedelta(days=1),
        requested_time="12:00",
    )
    second = VisitRequest.objects.create(
        property=property_one,
        user=client_two,
        owner=owner_one,
        requested_date=timezone.localdate() + timedelta(days=1),
        requested_time="13:00",
    )
    third = VisitRequest.objects.create(
        property=property_two,
        user=client_two,
        owner=owner_two,
        requested_date=timezone.localdate() + timedelta(days=1),
        requested_time="14:00",
    )

    authenticate(api_client, client_one)
    client_list = api_client.get("/api/visit-requests/")
    assert client_list.data["count"] == 1
    assert str(client_list.data["results"][0]["id"]) == str(first.pk)

    authenticate(api_client, owner_one)
    owner_list = api_client.get("/api/visit-requests/")
    assert owner_list.data["count"] == 2
    assert {str(item["id"]) for item in owner_list.data["results"]} == {str(first.pk), str(second.pk)}

    authenticate(api_client, admin)
    admin_list = api_client.get("/api/visit-requests/")
    assert admin_list.data["count"] == 3
    assert {str(item["id"]) for item in admin_list.data["results"]} == {
        str(first.pk), str(second.pk), str(third.pk)
    }


@pytest.mark.django_db
def test_visit_request_detail_is_private(api_client, visit_dependencies):
    client_one, client_two, owner_one, _, admin, property_type, location = visit_dependencies
    property_obj = make_property(owner_one, property_type, location, "visit-private-detail")
    visit = VisitRequest.objects.create(
        property=property_obj,
        user=client_two,
        owner=owner_one,
        requested_date=timezone.localdate() + timedelta(days=1),
        requested_time="12:00",
    )
    url = f"/api/visit-requests/{visit.pk}/"

    authenticate(api_client, client_one)
    assert api_client.get(url).status_code == 404
    authenticate(api_client, client_two)
    assert api_client.get(url).status_code == 200
    authenticate(api_client, owner_one)
    assert api_client.get(url).status_code == 200
    authenticate(api_client, admin)
    assert api_client.get(url).status_code == 200


@pytest.mark.django_db
def test_all_visit_request_status_transitions(api_client, visit_dependencies):
    client_one, client_two, owner, _, _, property_type, location = visit_dependencies
    first_property = make_property(owner, property_type, location, "visit-transition-accept")
    second_property = make_property(owner, property_type, location, "visit-transition-reject")
    third_property = make_property(owner, property_type, location, "visit-transition-cancel")

    authenticate(api_client, client_one)
    accepted_visit = create_visit(api_client, first_property).data
    rejected_visit = create_visit(api_client, second_property).data
    cancelled_visit = create_visit(api_client, third_property).data
    accept_url = f"/api/visit-requests/{accepted_visit['id']}/"
    reject_url = f"/api/visit-requests/{rejected_visit['id']}/"
    cancel_url = f"/api/visit-requests/{cancelled_visit['id']}/"

    requester_accept = api_client.patch(accept_url, {"status": "ACCEPTED"}, format="json")
    assert requester_accept.status_code == 403

    authenticate(api_client, owner)
    accepted = api_client.patch(accept_url, {"status": "ACCEPTED"}, format="json")
    assert accepted.status_code == 200
    assert accepted.data["status"] == VisitRequest.Status.ACCEPTED

    authenticate(api_client, client_one)
    accepted_cancel = api_client.patch(cancel_url, {"status": "CANCELLED"}, format="json")
    assert accepted_cancel.status_code == 200
    assert accepted_cancel.data["status"] == VisitRequest.Status.CANCELLED
    cannot_cancel_accepted = api_client.patch(accept_url, {"status": "CANCELLED"}, format="json")
    assert cannot_cancel_accepted.status_code == 400

    authenticate(api_client, owner)
    completed = api_client.patch(accept_url, {"status": "COMPLETED"}, format="json")
    rejected = api_client.patch(reject_url, {"status": "REJECTED"}, format="json")
    assert completed.status_code == 200
    assert completed.data["status"] == VisitRequest.Status.COMPLETED
    assert rejected.status_code == 200
    assert rejected.data["status"] == VisitRequest.Status.REJECTED
    cannot_complete_twice = api_client.patch(accept_url, {"status": "COMPLETED"}, format="json")
    cannot_accept_rejected = api_client.patch(reject_url, {"status": "ACCEPTED"}, format="json")
    assert cannot_complete_twice.status_code == 400
    assert cannot_accept_rejected.status_code == 400


@pytest.mark.django_db
def test_other_users_cannot_change_visit_status_or_mutate_fields(api_client, visit_dependencies):
    client, another_client, owner, another_owner, _, property_type, location = visit_dependencies
    property_obj = make_property(owner, property_type, location, "visit-permission-property")
    other_property = make_property(another_owner, property_type, location, "visit-other-property")
    visit = VisitRequest.objects.create(
        property=property_obj,
        user=client,
        owner=owner,
        requested_date=timezone.localdate() + timedelta(days=1),
        requested_time="12:00",
    )
    url = f"/api/visit-requests/{visit.pk}/"

    authenticate(api_client, another_client)
    hidden = api_client.get(url)
    forbidden = api_client.patch(url, {"status": "CANCELLED"}, format="json")
    assert hidden.status_code == 404
    assert forbidden.status_code == 404

    authenticate(api_client, another_owner)
    wrong_owner = api_client.patch(url, {"status": "ACCEPTED"}, format="json")
    assert wrong_owner.status_code == 404

    authenticate(api_client, owner)
    owner_cancel = api_client.patch(url, {"status": "CANCELLED"}, format="json")
    spoof_owner = api_client.patch(url, {"status": "ACCEPTED", "owner_id": str(another_owner.pk)}, format="json")
    spoof_property = api_client.patch(url, {"status": "ACCEPTED", "property_id": str(other_property.pk)}, format="json")
    assert owner_cancel.status_code == 403
    assert spoof_owner.status_code == 400
    assert "owner_id" in spoof_owner.data
    assert spoof_property.status_code == 400
    assert "property_id" in spoof_property.data
