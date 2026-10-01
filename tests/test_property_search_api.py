import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
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
def search_properties(db):
    owner = User.objects.create_user(email="search-owner@example.com", password=PASSWORD, role=User.Role.OWNER)
    other_owner = User.objects.create_user(email="search-other@example.com", password=PASSWORD, role=User.Role.OWNER)
    admin = User.objects.create_user(email="search-admin@example.com", password=PASSWORD, role=User.Role.ADMIN)
    normal_user = User.objects.create_user(email="search-user@example.com", password=PASSWORD)
    house = PropertyType.objects.create(name="Search house", slug="search-house")
    apartment = PropertyType.objects.create(name="Search apartment", slug="search-apartment")
    cap = Location.objects.create(name="Search Cap", slug="search-cap", type="CITY")
    limbe = Location.objects.create(name="Search Limbe", slug="search-limbe", type="CITY")

    target = Property.objects.create(
        owner=owner,
        title="Villa near beach",
        slug="target-slug-needle",
        description="Quiet sea-view home",
        property_type=house,
        listing_type=Property.ListingType.RENT,
        price="1500.00",
        currency="USD",
        bedrooms=3,
        bathrooms="2.0",
        area="120.00",
        furnished=True,
        location=cap,
        address="address-needle street",
        is_featured=True,
        status=Property.Status.PUBLISHED,
    )
    comparison = Property.objects.create(
        owner=other_owner,
        title="Small apartment",
        slug="small-apartment",
        description="Central urban flat",
        property_type=apartment,
        listing_type=Property.ListingType.SALE,
        price="900.00",
        currency="USD",
        bedrooms=1,
        bathrooms="1.0",
        area="60.00",
        furnished=False,
        location=limbe,
        address="Market road",
        is_featured=False,
        status=Property.Status.PUBLISHED,
    )
    Property.objects.filter(pk=target.pk).update(created_at=timezone.now() - timedelta(days=1))
    Property.objects.filter(pk=comparison.pk).update(created_at=timezone.now())
    return {
        "owner": owner,
        "other_owner": other_owner,
        "admin": admin,
        "normal_user": normal_user,
        "house": house,
        "apartment": apartment,
        "cap": cap,
        "limbe": limbe,
        "target": target,
        "comparison": comparison,
    }


def login(client, user):
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("parameter", "value", "expected_key"),
    [
        ("location", "__cap__", "target"),
        ("property_type", "__house__", "target"),
        ("listing_type", "RENT", "target"),
        ("min_price", "1400", "target"),
        ("max_price", "1000", "comparison"),
        ("min_bedrooms", "3", "target"),
        ("max_bedrooms", "1", "comparison"),
        ("min_bathrooms", "2", "target"),
        ("max_bathrooms", "1", "comparison"),
        ("furnished", "true", "target"),
        ("is_featured", "true", "target"),
    ],
)
def test_each_property_filter(search_properties, api_client, parameter, value, expected_key):
    if value == "__cap__":
        value = search_properties["cap"].pk
    elif value == "__house__":
        value = search_properties["house"].pk

    response = api_client.get("/api/properties/", {parameter: str(value)})

    expected = search_properties[expected_key]
    assert response.status_code == 200
    assert response.data["count"] == 1
    assert str(response.data["results"][0]["id"]) == str(expected.pk)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Villa", "target"),
        ("sea-view", "target"),
        ("target-slug-needle", None),
        ("address-needle", None),
    ],
)
def test_search_only_matches_title_and_description(search_properties, api_client, query, expected):
    response = api_client.get("/api/properties/", {"search": query})

    assert response.status_code == 200
    assert response.data["count"] == (1 if expected else 0)
    if expected:
        assert str(response.data["results"][0]["id"]) == str(search_properties[expected].pk)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("ordering", "expected_order"),
    [
        ("price", ("comparison", "target")),
        ("-price", ("target", "comparison")),
        ("bedrooms", ("comparison", "target")),
        ("area", ("comparison", "target")),
        ("-area", ("target", "comparison")),
        ("created_at", ("target", "comparison")),
        ("-created_at", ("comparison", "target")),
    ],
)
def test_property_ordering_fields(search_properties, api_client, ordering, expected_order):
    response = api_client.get("/api/properties/", {"ordering": ordering})
    ids = [str(item["id"]) for item in response.data["results"]]

    assert response.status_code == 200
    assert ids == [str(search_properties[key].pk) for key in expected_order]


@pytest.mark.django_db
def test_property_filters_can_be_combined(search_properties, api_client):
    target = search_properties["target"]
    response = api_client.get(
        "/api/properties/",
        {
            "location": str(target.location_id),
            "property_type": str(target.property_type_id),
            "listing_type": "RENT",
            "min_price": "1400",
            "max_price": "1600",
            "min_bedrooms": "2",
            "max_bedrooms": "4",
            "min_bathrooms": "1.5",
            "max_bathrooms": "2.5",
            "furnished": "true",
            "is_featured": "true",
            "search": "sea-view",
        },
    )

    assert response.status_code == 200
    assert response.data["count"] == 1
    assert str(response.data["results"][0]["id"]) == str(target.pk)


@pytest.mark.django_db
def test_private_status_visibility_is_applied_before_search_and_filters(search_properties, api_client):
    owner = search_properties["owner"]
    target = search_properties["target"]
    private = Property.objects.create(
        owner=owner,
        title="Hidden draft searchable phrase",
        slug="hidden-draft-search",
        property_type=search_properties["house"],
        listing_type=Property.ListingType.RENT,
        price="1550.00",
        currency="USD",
        location=search_properties["cap"],
        status=Property.Status.DRAFT,
    )

    public = api_client.get("/api/properties/", {"search": "Hidden draft searchable phrase"})
    assert public.data["count"] == 0

    login(api_client, owner)
    owner_results = api_client.get(
        "/api/properties/", {"location": str(private.location_id), "min_price": "1500"}
    )
    assert {str(row["id"]) for row in owner_results.data["results"]} == {
        str(target.pk), str(private.pk)
    }

    login(api_client, search_properties["admin"])
    admin_private = api_client.get("/api/properties/", {"search": "Hidden draft searchable phrase"})
    assert admin_private.data["count"] == 1
    assert str(admin_private.data["results"][0]["id"]) == str(private.pk)


@pytest.mark.django_db
def test_property_list_prefetches_many_to_many_amenities(search_properties, api_client, django_assert_num_queries):
    amenity = Amenity.objects.create(name="Search parking", slug="search-parking")
    for property_obj in (search_properties["target"], search_properties["comparison"]):
        PropertyAmenity.objects.create(property=property_obj, amenity=amenity)

    with django_assert_num_queries(3):
        response = api_client.get("/api/properties/")

    assert response.status_code == 200
    assert response.data["count"] == 2
