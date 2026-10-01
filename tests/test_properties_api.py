import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.locations.models import Location
from apps.properties.models import Property, PropertyType

User = get_user_model()
PASSWORD = "Secure-Password-521!"


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def property_dependencies(db):
    property_type = PropertyType.objects.create(name="House", slug="house")
    location = Location.objects.create(name="Cap-Haïtien", slug="cap-haitien", type="CITY")
    owner = User.objects.create_user(
        email="owner@example.com", password=PASSWORD, role=User.Role.OWNER
    )
    agent = User.objects.create_user(
        email="agent@example.com", password=PASSWORD, role=User.Role.AGENT
    )
    other_owner = User.objects.create_user(
        email="other@example.com", password=PASSWORD, role=User.Role.OWNER
    )
    normal_user = User.objects.create_user(email="user@example.com", password=PASSWORD)
    admin = User.objects.create_user(
        email="admin@example.com", password=PASSWORD, role=User.Role.ADMIN
    )
    return property_type, location, owner, agent, other_owner, normal_user, admin


def property_payload(property_type, location, **overrides):
    data = {
        "title": "Bright family home",
        "slug": "bright-family-home",
        "description": "A comfortable home close to town.",
        "property_type": str(property_type.pk),
        "listing_type": "RENT",
        "price": "1250.00",
        "currency": "USD",
        "bedrooms": 3,
        "bathrooms": "2.5",
        "parking_spaces": 1,
        "area": "120.00",
        "area_unit": "SQM",
        "furnished": True,
        "location": str(location.pk),
        "address": "Rue 18",
    }
    data.update(overrides)
    return data


def create_property(owner, property_type, location, **overrides):
    data = {
        "owner": owner,
        "title": "Bright family home",
        "slug": "bright-family-home",
        "description": "A comfortable home close to town.",
        "property_type": property_type,
        "listing_type": Property.ListingType.RENT,
        "price": "1250.00",
        "currency": "USD",
        "bedrooms": 3,
        "bathrooms": "2.5",
        "parking_spaces": 1,
        "area": "120.00",
        "area_unit": Property.AreaUnit.SQM,
        "furnished": True,
        "location": location,
    }
    data.update(overrides)
    return Property.objects.create(**data)


def authenticate_as(client, user):
    token = AccessToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")


@pytest.mark.django_db
def test_only_published_properties_are_public(api_client, property_dependencies):
    property_type, location, owner, _, other_owner, normal_user, admin = property_dependencies
    published = create_property(
        owner, property_type, location, status=Property.Status.PUBLISHED
    )
    create_property(other_owner, property_type, location, slug="private-draft")
    pending = create_property(
        other_owner,
        property_type,
        location,
        slug="pending-listing",
        status=Property.Status.PENDING_REVIEW,
    )

    public_list = api_client.get("/api/properties/")
    public_detail = api_client.get(f"/api/properties/{published.pk}/")
    hidden_draft = api_client.get(f"/api/properties/{pending.pk}/")

    assert public_list.status_code == 200
    assert public_list.data["count"] == 1
    assert public_list.data["results"][0]["id"] == str(published.pk)
    assert public_detail.status_code == 200
    assert hidden_draft.status_code == 404

    authenticate_as(api_client, normal_user)
    normal_list = api_client.get("/api/properties/")
    assert normal_list.data["count"] == 1

    authenticate_as(api_client, admin)
    admin_list = api_client.get("/api/properties/")
    assert admin_list.data["count"] == 3


@pytest.mark.django_db
def test_owner_and_agent_can_see_all_their_own_statuses(property_dependencies, api_client):
    property_type, location, owner, agent, other_owner, *_ = property_dependencies
    draft = create_property(owner, property_type, location)
    pending = create_property(
        owner, property_type, location, slug="pending", status=Property.Status.PENDING_REVIEW
    )
    rejected = create_property(
        owner, property_type, location, slug="rejected", status=Property.Status.REJECTED
    )
    public = create_property(
        other_owner, property_type, location, slug="public", status=Property.Status.PUBLISHED
    )
    create_property(other_owner, property_type, location, slug="other-draft")

    authenticate_as(api_client, owner)
    owner_list = api_client.get("/api/properties/")
    assert owner_list.data["count"] == 4
    assert {item["id"] for item in owner_list.data["results"]} == {
        str(draft.pk), str(pending.pk), str(rejected.pk), str(public.pk)
    }

    authenticate_as(api_client, agent)
    agent_list = api_client.get("/api/properties/")
    assert agent_list.data["count"] == 1
    assert agent_list.data["results"][0]["id"] == str(public.pk)


@pytest.mark.django_db
def test_only_owner_or_agent_can_create_and_creation_is_always_draft(api_client, property_dependencies):
    property_type, location, owner, agent, _, normal_user, admin = property_dependencies
    payload = property_payload(
        property_type,
        location,
        status="PUBLISHED",
        is_featured=True,
        owner=str(admin.pk),
    )

    authenticate_as(api_client, normal_user)
    denied = api_client.post("/api/properties/", property_payload(property_type, location), format="json")
    assert denied.status_code == 403

    authenticate_as(api_client, admin)
    admin_create = api_client.post(
        "/api/properties/", property_payload(property_type, location), format="json"
    )
    assert admin_create.status_code == 403

    authenticate_as(api_client, owner)
    forged = api_client.post("/api/properties/", payload, format="json")
    assert forged.status_code == 400
    assert {"status", "is_featured", "owner"}.issubset(forged.data.keys())

    created = api_client.post(
        "/api/properties/", property_payload(property_type, location), format="json"
    )
    assert created.status_code == 201
    assert created.data["status"] == Property.Status.DRAFT
    assert str(created.data["owner"]) == str(owner.pk)
    assert created.data["is_featured"] is False
    assert created.data["published_at"] is None
    assert "password" not in created.data

    authenticate_as(api_client, agent)
    agent_created = api_client.post(
        "/api/properties/",
        property_payload(property_type, location, slug="agent-listing"),
        format="json",
    )
    assert agent_created.status_code == 201
    assert str(agent_created.data["owner"]) == str(agent.pk)


@pytest.mark.django_db
def test_owner_can_update_only_own_property_and_cannot_set_status_or_featured(api_client, property_dependencies):
    property_type, location, owner, _, other_owner, _, _ = property_dependencies
    own = create_property(owner, property_type, location)
    public_other = create_property(
        other_owner, property_type, location, slug="other-public", status=Property.Status.PUBLISHED
    )
    authenticate_as(api_client, owner)

    updated = api_client.patch(
        f"/api/properties/{own.pk}/", {"title": "Updated title"}, format="json"
    )
    forbidden_other = api_client.patch(
        f"/api/properties/{public_other.pk}/", {"title": "Intrusion", "price": "1.00"}, format="json"
    )
    change_owner = api_client.patch(
        f"/api/properties/{own.pk}/", {"owner_id": str(other_owner.pk)}, format="json"
    )
    status_change = api_client.patch(
        f"/api/properties/{own.pk}/", {"status": "PUBLISHED"}, format="json"
    )
    featured_change = api_client.patch(
        f"/api/properties/{own.pk}/", {"is_featured": True}, format="json"
    )

    assert updated.status_code == 200
    assert updated.data["title"] == "Updated title"
    assert forbidden_other.status_code == 403
    assert change_owner.status_code == 400
    assert status_change.status_code == 400
    assert featured_change.status_code == 400
    own.refresh_from_db()
    assert own.status == Property.Status.DRAFT
    assert own.is_featured is False
    public_other.refresh_from_db()
    assert str(public_other.price) == "1250.00"


@pytest.mark.django_db
def test_agent_can_update_and_submit_only_the_agents_own_listing(api_client, property_dependencies):
    property_type, location, _, agent, other_owner, _, _ = property_dependencies
    own = create_property(agent, property_type, location, slug="agent-owned-listing")
    another = create_property(other_owner, property_type, location, slug="agent-not-owned-listing")
    another.status = Property.Status.PUBLISHED
    another.save(update_fields=("status", "updated_at"))
    authenticate_as(api_client, agent)

    updated = api_client.patch(f"/api/properties/{own.pk}/", {"price": "1400.00"}, format="json")
    denied = api_client.patch(f"/api/properties/{another.pk}/", {"price": "1.00"}, format="json")
    submitted = api_client.post(f"/api/properties/{own.pk}/submit-for-review/")

    assert updated.status_code == 200
    assert denied.status_code == 403
    assert submitted.status_code == 200
    own.refresh_from_db()
    another.refresh_from_db()
    assert own.price == 1400
    assert another.price == 1250
    assert own.status == Property.Status.PENDING_REVIEW


@pytest.mark.django_db
def test_submit_review_requires_owner_and_admin_approval_publishes(property_dependencies, api_client):
    property_type, location, owner, _, other_owner, _, admin = property_dependencies
    listing = create_property(owner, property_type, location)

    authenticate_as(api_client, other_owner)
    wrong_owner = api_client.post(f"/api/properties/{listing.pk}/submit-for-review/")
    assert wrong_owner.status_code == 404

    authenticate_as(api_client, owner)
    submitted = api_client.post(f"/api/properties/{listing.pk}/submit-for-review/")
    assert submitted.status_code == 200
    assert submitted.data["status"] == Property.Status.PENDING_REVIEW

    owner_publish = api_client.post(f"/api/properties/{listing.pk}/publish/")
    assert owner_publish.status_code == 403

    authenticate_as(api_client, admin)
    published = api_client.post(f"/api/properties/{listing.pk}/publish/")
    assert published.status_code == 200
    assert published.data["status"] == Property.Status.PUBLISHED
    assert published.data["published_at"] is not None

    invalid_resubmit = api_client.post(f"/api/properties/{listing.pk}/submit-for-review/")
    assert invalid_resubmit.status_code == 400


@pytest.mark.django_db
def test_admin_can_reject_and_owner_can_resubmit(property_dependencies, api_client):
    property_type, location, owner, _, _, _, admin = property_dependencies
    listing = create_property(
        owner,
        property_type,
        location,
        status=Property.Status.PENDING_REVIEW,
    )

    authenticate_as(api_client, admin)
    rejected = api_client.post(f"/api/properties/{listing.pk}/reject/")
    assert rejected.status_code == 200
    assert rejected.data["status"] == Property.Status.REJECTED

    authenticate_as(api_client, owner)
    resubmitted = api_client.post(f"/api/properties/{listing.pk}/submit-for-review/")
    assert resubmitted.status_code == 200
    assert resubmitted.data["status"] == Property.Status.PENDING_REVIEW


@pytest.mark.django_db
def test_only_admin_can_suspend_a_property(property_dependencies, api_client):
    property_type, location, owner, _, _, _, admin = property_dependencies
    listing = create_property(owner, property_type, location, status=Property.Status.PUBLISHED)

    authenticate_as(api_client, owner)
    owner_suspend = api_client.post(f"/api/properties/{listing.pk}/suspend/")
    assert owner_suspend.status_code == 403

    authenticate_as(api_client, admin)
    suspended = api_client.post(f"/api/properties/{listing.pk}/suspend/")
    assert suspended.status_code == 200
    assert suspended.data["status"] == Property.Status.SUSPENDED


@pytest.mark.django_db
def test_owner_or_admin_can_mark_published_property_rented(property_dependencies, api_client):
    property_type, location, owner, _, other_owner, _, admin = property_dependencies
    listing = create_property(
        owner, property_type, location, status=Property.Status.PUBLISHED
    )

    authenticate_as(api_client, other_owner)
    forbidden = api_client.post(f"/api/properties/{listing.pk}/mark-rented/")
    assert forbidden.status_code == 403

    authenticate_as(api_client, owner)
    rented = api_client.post(f"/api/properties/{listing.pk}/mark-rented/")
    assert rented.status_code == 200
    assert rented.data["status"] == Property.Status.RENTED

    invalid_again = api_client.post(f"/api/properties/{listing.pk}/mark-rented/")
    assert invalid_again.status_code == 400

    listing.status = Property.Status.PUBLISHED
    listing.save(update_fields=("status", "updated_at"))
    authenticate_as(api_client, admin)
    admin_rented = api_client.post(f"/api/properties/{listing.pk}/mark-rented/")
    assert admin_rented.status_code == 200
    assert admin_rented.data["status"] == Property.Status.RENTED


@pytest.mark.django_db
def test_owner_and_admin_can_archive_but_other_users_cannot(property_dependencies, api_client):
    property_type, location, owner, _, other_owner, _, admin = property_dependencies
    listing = create_property(owner, property_type, location)

    authenticate_as(api_client, other_owner)
    forbidden = api_client.post(f"/api/properties/{listing.pk}/archive/")
    assert forbidden.status_code == 404

    authenticate_as(api_client, owner)
    archived = api_client.post(f"/api/properties/{listing.pk}/archive/")
    assert archived.status_code == 200
    assert archived.data["status"] == Property.Status.ARCHIVED
    assert archived.data["deleted_at"] is None

    invalid_archive = api_client.post(f"/api/properties/{listing.pk}/archive/")
    assert invalid_archive.status_code == 400

    other_listing = create_property(
        other_owner, property_type, location, slug="admin-archive"
    )
    authenticate_as(api_client, admin)
    admin_archive = api_client.post(f"/api/properties/{other_listing.pk}/archive/")
    assert admin_archive.status_code == 200


@pytest.mark.django_db
def test_delete_soft_deletes_owned_property(property_dependencies, api_client):
    property_type, location, owner, _, _, _, _ = property_dependencies
    listing = create_property(owner, property_type, location)
    authenticate_as(api_client, owner)

    response = api_client.delete(f"/api/properties/{listing.pk}/")

    assert response.status_code == 204
    listing.refresh_from_db()
    assert listing.is_deleted is True
    assert listing.deleted_at is not None
    assert not Property.objects.filter(pk=listing.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("price", "-0.01"),
        ("bedrooms", -1),
        ("bathrooms", "-0.5"),
        ("parking_spaces", -1),
        ("area", "-1"),
    ],
)
def test_negative_measurements_are_rejected(property_dependencies, api_client, field, value):
    property_type, location, owner, *_ = property_dependencies
    authenticate_as(api_client, owner)
    payload = property_payload(property_type, location, **{field: value, "slug": f"bad-{field}"})

    response = api_client.post("/api/properties/", payload, format="json")

    assert response.status_code == 400
    assert field in response.data


@pytest.mark.django_db
def test_slug_is_unique_and_coordinates_must_be_a_valid_pair(property_dependencies, api_client):
    property_type, location, owner, *_ = property_dependencies
    create_property(owner, property_type, location)
    authenticate_as(api_client, owner)

    duplicate = api_client.post(
        "/api/properties/",
        property_payload(property_type, location, slug="BRIGHT-FAMILY-HOME"),
        format="json",
    )
    bad_pair = api_client.post(
        "/api/properties/",
        property_payload(property_type, location, slug="bad-pair", latitude="19.75"),
        format="json",
    )
    out_of_range = api_client.post(
        "/api/properties/",
        property_payload(
            property_type,
            location,
            slug="bad-coordinate",
            latitude="91",
            longitude="-72",
        ),
        format="json",
    )

    assert duplicate.status_code == 400
    assert "slug" in duplicate.data
    assert bad_pair.status_code == 400
    assert "latitude" in bad_pair.data
    assert out_of_range.status_code == 400
    assert "latitude" in out_of_range.data


@pytest.mark.django_db
def test_database_constraints_reject_negative_values_and_duplicate_slugs(property_dependencies):
    property_type, location, owner, *_ = property_dependencies
    listing = create_property(owner, property_type, location)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Property.objects.filter(pk=listing.pk).update(price="-1.00")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Property.objects.filter(pk=listing.pk).update(bedrooms=-1)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            create_property(
                owner,
                property_type,
                location,
                slug="BRIGHT-FAMILY-HOME",
            )


@pytest.mark.django_db
def test_search_filters_and_ordering(property_dependencies, api_client):
    property_type, location, owner, _, _, _, _ = property_dependencies
    expensive = create_property(
        owner,
        property_type,
        location,
        slug="expensive-home",
        title="Expensive home",
        description="A large villa.",
        status=Property.Status.PUBLISHED,
        price="2000.00",
        bedrooms=4,
    )
    affordable = create_property(
        owner,
        property_type,
        location,
        slug="affordable-home",
        title="Affordable home",
        description="A comfortable family apartment.",
        status=Property.Status.PUBLISHED,
        price="1000.00",
        bedrooms=2,
    )

    by_title = api_client.get("/api/properties/", {"search": "Expensive"})
    by_description = api_client.get("/api/properties/", {"search": "comfortable family"})
    filtered = api_client.get(
        "/api/properties/",
        {"listing_type": "RENT", "min_price": "1500", "property_type": str(property_type.pk)},
    )
    ordered = api_client.get("/api/properties/", {"ordering": "price"})

    assert [row["id"] for row in by_title.data["results"]] == [str(expensive.pk)]
    assert [row["id"] for row in by_description.data["results"]] == [str(affordable.pk)]
    assert filtered.data["count"] == 1
    assert [row["id"] for row in ordered.data["results"]] == [
        str(affordable.pk), str(expensive.pk)
    ]


@pytest.mark.django_db
def test_property_pagination(property_dependencies, api_client):
    property_type, location, owner, *_ = property_dependencies
    for index in range(23):
        create_property(
            owner,
            property_type,
            location,
            slug=f"listing-{index}",
            title=f"Listing {index}",
            status=Property.Status.PUBLISHED,
        )

    page = api_client.get("/api/properties/", {"page": 2, "page_size": 5})

    assert page.status_code == 200
    assert page.data["count"] == 23
    assert len(page.data["results"]) == 5
    assert page.data["next"] is not None
