from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.locations.models import Location
from apps.monetization.models import Plan, PropertyPromotion, Subscription
from apps.monetization.services import (
    SubscriptionLimitReached,
    SubscriptionService,
    create_property_promotion,
)
from apps.properties.models import Property, PropertyType

User = get_user_model()


@pytest.fixture
def subscription_data(db):
    owner = User.objects.create_user(email="quota-owner@example.com", password="Secure-Password-521!", role="OWNER")
    admin = User.objects.create_user(email="quota-admin@example.com", password="Secure-Password-521!", role="ADMIN")
    free = Plan.objects.get(code=Plan.Code.FREE)
    pro = Plan.objects.create(
        code=Plan.Code.PRO,
        name="Configured Pro",
        price="19.00",
        max_active_properties=5,
        can_promote_properties=True,
    )
    business = Plan.objects.create(
        code=Plan.Code.BUSINESS,
        name="Configured Business",
        price="49.00",
        max_active_properties=20,
        can_promote_properties=True,
    )
    kind = PropertyType.objects.create(name="Quota house", slug="quota-house")
    location = Location.objects.create(name="Quota city", slug="quota-city", type="CITY")
    return {"owner": owner, "admin": admin, "free": free, "pro": pro, "business": business,
            "kind": kind, "location": location}


def make_listing(data, owner=None, status=Property.Status.DRAFT, slug="quota-listing"):
    owner = owner or data["owner"]
    return Property.objects.create(
        owner=owner,
        title=slug,
        slug=slug,
        property_type=data["kind"],
        listing_type=Property.ListingType.RENT,
        price="100",
        currency="USD",
        location=data["location"],
        status=status,
    )


def activate_plan(data, plan):
    Subscription.objects.create(
        user=data["owner"], plan=plan, status=Subscription.Status.ACTIVE, starts_at=timezone.now()
    )


@pytest.mark.django_db
def test_free_plan_limit_is_database_configured_and_applies_to_active_inventory(subscription_data):
    data = subscription_data
    assert data["free"].max_active_properties == 2
    make_listing(data, slug="free-one")
    make_listing(data, status=Property.Status.PENDING_REVIEW, slug="free-two")
    with pytest.raises(SubscriptionLimitReached):
        SubscriptionService.ensure_property_capacity(data["owner"])

    # Non-active inventory does not consume a slot.
    Property.objects.filter(owner=data["owner"]).first().delete()
    Property.objects.filter(owner=data["owner"], status=Property.Status.PENDING_REVIEW).update(
        status=Property.Status.REJECTED
    )
    SubscriptionService.ensure_property_capacity(data["owner"])


@pytest.mark.django_db
@pytest.mark.parametrize("plan_code,expected_limit", [(Plan.Code.PRO, 5), (Plan.Code.BUSINESS, 20)])
def test_paid_plan_capacity_comes_from_plan_record(subscription_data, plan_code, expected_limit):
    data = subscription_data
    plan = data[plan_code.lower()]
    assert plan.max_active_properties == expected_limit
    activate_plan(data, plan)
    for index in range(expected_limit):
        make_listing(data, slug=f"{plan_code.lower()}-{index}")
    with pytest.raises(SubscriptionLimitReached):
        SubscriptionService.ensure_property_capacity(data["owner"])


@pytest.mark.django_db
def test_expired_or_past_due_subscription_falls_back_to_free(subscription_data):
    data = subscription_data
    Subscription.objects.create(
        user=data["owner"], plan=data["pro"], status=Subscription.Status.PAST_DUE,
        starts_at=timezone.now() - timedelta(days=1),
    )
    assert SubscriptionService.get_effective_plan(data["owner"]).code == Plan.Code.FREE

    Subscription.objects.filter(user=data["owner"]).update(status=Subscription.Status.EXPIRED)
    Subscription.objects.create(
        user=data["owner"], plan=data["pro"], status=Subscription.Status.ACTIVE,
        starts_at=timezone.now() - timedelta(days=2), ends_at=timezone.now() - timedelta(days=1),
    )
    assert SubscriptionService.get_effective_plan(data["owner"]).code == Plan.Code.FREE


@pytest.mark.django_db
def test_admin_bypasses_property_limit(subscription_data):
    data = subscription_data
    SubscriptionService.ensure_property_capacity(data["admin"])


@pytest.mark.django_db
def test_free_users_cannot_create_promotions_but_entitled_plan_and_admin_can(subscription_data):
    data = subscription_data
    listing = make_listing(data, status=Property.Status.PUBLISHED)
    starts = timezone.now()
    with pytest.raises(SubscriptionLimitReached):
        create_property_promotion(
            property_obj=listing, actor=data["owner"], promotion_type=PropertyPromotion.Type.BOOST,
            starts_at=starts, ends_at=starts + timedelta(days=1),
        )

    activate_plan(data, data["pro"])
    promo = create_property_promotion(
        property_obj=listing, actor=data["owner"], promotion_type=PropertyPromotion.Type.BOOST,
        starts_at=starts, ends_at=starts + timedelta(days=1),
    )
    assert promo.status == PropertyPromotion.Status.PENDING

    admin_listing = make_listing(data, owner=data["admin"], status=Property.Status.PUBLISHED, slug="admin-listing")
    admin_promo = create_property_promotion(
        property_obj=admin_listing, actor=data["admin"], promotion_type=PropertyPromotion.Type.FEATURED,
        starts_at=starts, ends_at=starts + timedelta(days=1),
    )
    assert admin_promo.status == PropertyPromotion.Status.PENDING


@pytest.mark.django_db
def test_property_api_enforces_paid_subscription_quota(subscription_data):
    data = subscription_data
    # Active un abonnement PRO avec quota de 5 annonces
    activate_plan(data, data["pro"])
    client = APIClient()
    client.force_authenticate(data["owner"])
    for index in range(5):
        response = client.post("/api/properties/", {
            "title": f"Quota property {index}", "slug": f"quota-api-{index}",
            "property_type": str(data["kind"].pk), "listing_type": "RENT", "price": "100",
            "currency": "USD", "location": str(data["location"].pk),
        }, format="json")
        assert response.status_code == 201
    denied = client.post("/api/properties/", {
        "title": "Quota property 6", "slug": "quota-api-6", "property_type": str(data["kind"].pk),
        "listing_type": "RENT", "price": "100", "currency": "USD",
        "location": str(data["location"].pk),
    }, format="json")
    assert denied.status_code == 400
    assert "plan" in denied.data


@pytest.mark.django_db
def test_free_tier_allows_draft_creation_and_enforces_moncash_at_publication(subscription_data):
    data = subscription_data
    client = APIClient()
    client.force_authenticate(data["owner"])
    # Le propriétaire sans abonnement peut créer plus de 2 brouillons
    created_ids = []
    for index in range(3):
        response = client.post("/api/properties/", {
            "title": f"Free tier draft {index}", "slug": f"free-draft-{index}",
            "property_type": str(data["kind"].pk), "listing_type": "RENT", "price": "100",
            "currency": "USD", "location": str(data["location"].pk),
        }, format="json")
        assert response.status_code == 201
        created_ids.append(response.data["id"])

    # 1ère annonce soumise -> gratuite (200 OK -> PENDING_REVIEW)
    first_submit = client.post(f"/api/properties/{created_ids[0]}/submit-for-review/")
    assert first_submit.status_code == 200
    assert first_submit.data["status"] == Property.Status.PENDING_REVIEW

    # 2ème annonce soumise -> PAIEMENT MONCASH REQUIS (402 Payment Required)
    second_submit = client.post(f"/api/properties/{created_ids[1]}/submit-for-review/")
    assert second_submit.status_code == 402
    assert second_submit.data["requires_payment"] is True
    assert second_submit.data["amount"] == "500.00"

