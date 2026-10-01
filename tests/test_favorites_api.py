import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.interactions.models import Favorite
from apps.locations.models import Location
from apps.properties.models import Amenity, Property, PropertyAmenity, PropertyType

User = get_user_model()
PASSWORD = "Secure-Password-521!"


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def favorite_dependencies(db):
    user = User.objects.create_user(email="favorite-user@example.com", password=PASSWORD)
    another_user = User.objects.create_user(email="favorite-another@example.com", password=PASSWORD)
    owner = User.objects.create_user(email="favorite-owner@example.com", password=PASSWORD, role=User.Role.OWNER)
    property_type = PropertyType.objects.create(name="Favorite house", slug="favorite-house")
    location = Location.objects.create(name="Favorite city", slug="favorite-city", type="CITY")
    return user, another_user, owner, property_type, location


def make_property(owner, property_type, location, slug, status=Property.Status.PUBLISHED):
    return Property.objects.create(
        owner=owner,
        title=slug.replace("-", " ").title(),
        slug=slug,
        property_type=property_type,
        listing_type=Property.ListingType.RENT,
        price="1000.00",
        currency="USD",
        location=location,
        status=status,
    )


def authenticate(client, user):
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")


@pytest.mark.django_db
def test_favorites_require_authentication(api_client):
    assert api_client.get("/api/favorites/").status_code == 401
    assert api_client.post("/api/properties/00000000-0000-0000-0000-000000000001/favorite/").status_code == 401
    assert api_client.delete("/api/properties/00000000-0000-0000-0000-000000000001/favorite/").status_code == 401


@pytest.mark.django_db
def test_user_can_add_and_list_only_their_own_favorites(api_client, favorite_dependencies):
    user, another_user, owner, property_type, location = favorite_dependencies
    first = make_property(owner, property_type, location, "favorite-first")
    second = make_property(owner, property_type, location, "favorite-second")
    Favorite.objects.create(user=another_user, property=second)
    authenticate(api_client, user)

    added = api_client.post(f"/api/properties/{first.pk}/favorite/")
    duplicate = api_client.post(f"/api/properties/{first.pk}/favorite/")
    listing = api_client.get("/api/favorites/")

    assert added.status_code == 201, added.data
    assert str(added.data["property"]["id"]) == str(first.pk)
    assert duplicate.status_code == 200
    assert Favorite.objects.filter(user=user, property=first).count() == 1
    assert listing.status_code == 200
    assert listing.data["count"] == 1
    assert str(listing.data["results"][0]["property"]["id"]) == str(first.pk)


@pytest.mark.django_db
def test_only_published_properties_can_be_favorited(api_client, favorite_dependencies):
    user, _, owner, property_type, location = favorite_dependencies
    draft = make_property(owner, property_type, location, "favorite-draft", Property.Status.DRAFT)
    pending = make_property(owner, property_type, location, "favorite-pending", Property.Status.PENDING_REVIEW)
    authenticate(api_client, user)

    hidden_draft = api_client.post(f"/api/properties/{draft.pk}/favorite/")
    hidden_pending = api_client.post(f"/api/properties/{pending.pk}/favorite/")

    assert hidden_draft.status_code == 404
    assert hidden_pending.status_code == 404
    assert Favorite.objects.count() == 0


@pytest.mark.django_db
def test_owner_cannot_favorite_own_unpublished_property(api_client, favorite_dependencies):
    _, _, owner, property_type, location = favorite_dependencies
    draft = make_property(owner, property_type, location, "owner-draft", Property.Status.DRAFT)
    authenticate(api_client, owner)

    response = api_client.post(f"/api/properties/{draft.pk}/favorite/")

    assert response.status_code == 400
    assert "property" in response.data
    assert Favorite.objects.count() == 0


@pytest.mark.django_db
def test_user_can_only_remove_their_own_favorite(api_client, favorite_dependencies):
    user, another_user, owner, property_type, location = favorite_dependencies
    property_obj = make_property(owner, property_type, location, "favorite-delete")
    own_favorite = Favorite.objects.create(user=user, property=property_obj)
    other_favorite = Favorite.objects.create(user=another_user, property=property_obj)
    authenticate(api_client, user)

    removed_other = api_client.delete(f"/api/properties/{property_obj.pk}/favorite/")
    assert removed_other.status_code == 204
    assert not Favorite.objects.filter(pk=own_favorite.pk).exists()
    assert Favorite.objects.filter(pk=other_favorite.pk).exists()

    no_longer_owned = api_client.delete(f"/api/properties/{property_obj.pk}/favorite/")
    assert no_longer_owned.status_code == 404
    assert Favorite.objects.filter(pk=other_favorite.pk).exists()


@pytest.mark.django_db
def test_favorite_database_constraint_prevents_duplicate_pairs(favorite_dependencies):
    user, _, owner, property_type, location = favorite_dependencies
    property_obj = make_property(owner, property_type, location, "favorite-unique")
    Favorite.objects.create(user=user, property=property_obj)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Favorite.objects.create(user=user, property=property_obj)


@pytest.mark.django_db
def test_favorites_list_prefetches_property_relations(api_client, favorite_dependencies, django_assert_num_queries):
    user, _, owner, property_type, location = favorite_dependencies
    amenity = Amenity.objects.create(name="Favorite pool", slug="favorite-pool")
    properties = [
        make_property(owner, property_type, location, "favorite-query-one"),
        make_property(owner, property_type, location, "favorite-query-two"),
    ]
    for property_obj in properties:
        PropertyAmenity.objects.create(property=property_obj, amenity=amenity)
        Favorite.objects.create(user=user, property=property_obj)
    authenticate(api_client, user)

    with django_assert_num_queries(4):
        response = api_client.get("/api/favorites/")

    assert response.status_code == 200
    assert response.data["count"] == 2
