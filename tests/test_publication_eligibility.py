import pytest
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.locations.models import Location
from apps.monetization.models import Payment
from apps.monetization.services import (
    FREE_PUBLICATIONS_LIMIT,
    check_publication_eligibility,
)
from apps.properties.models import Property, PropertyType

User = get_user_model()
PASSWORD = "Secure-Password-521!"


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def test_setup(db):
    property_type = PropertyType.objects.create(name="Appartement", slug="appartement")
    location = Location.objects.create(name="Pétion-Ville", slug="petion-ville", type="CITY")

    owner = User.objects.create_user(
        email="owner1@example.com", password=PASSWORD, role=User.Role.OWNER
    )
    other_owner = User.objects.create_user(
        email="owner2@example.com", password=PASSWORD, role=User.Role.OWNER
    )
    normal_user = User.objects.create_user(
        email="regular@example.com", password=PASSWORD, role=User.Role.USER
    )
    admin = User.objects.create_user(
        email="admin@example.com", password=PASSWORD, role=User.Role.ADMIN
    )

    return property_type, location, owner, other_owner, normal_user, admin


def create_prop(owner, property_type, location, **overrides):
    import uuid
    suffix = uuid.uuid4().hex[:6]
    data = {
        "owner": owner,
        "title": f"Maison {suffix}",
        "slug": f"maison-{suffix}",
        "description": "Belle propriété de test.",
        "property_type": property_type,
        "listing_type": Property.ListingType.RENT,
        "price": Decimal("1000.00"),
        "currency": "USD",
        "bedrooms": 2,
        "bathrooms": Decimal("1.5"),
        "parking_spaces": 1,
        "area": Decimal("85.00"),
        "area_unit": Property.AreaUnit.SQM,
        "furnished": True,
        "location": location,
        "status": Property.Status.DRAFT,
    }
    data.update(overrides)
    return Property.objects.create(**data)


def authenticate_as(client, user):
    token = AccessToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")


# ---------------------------------------------------------------------------
# Cas 1 — Première publication
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_case_1_first_publication_is_free_and_submits(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)

    eligibility = check_publication_eligibility(prop)
    assert eligibility.free_publications_consumed == 0
    assert eligibility.free_remaining == 1
    assert eligibility.is_free is True
    assert eligibility.requires_payment is False
    assert eligibility.already_paid is False
    assert eligibility.free_publications_limit == FREE_PUBLICATIONS_LIMIT

    authenticate_as(api_client, owner)
    response = api_client.post(f"/api/properties/{prop.pk}/submit-for-review/")
    assert response.status_code == 200
    assert response.data["status"] == Property.Status.PENDING_REVIEW


# ---------------------------------------------------------------------------
# Cas 2 — Deuxième publication (PAIEMENT REQUIS)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_case_2_second_publication_requires_payment(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    # First property is already published
    create_prop(
        owner,
        pt,
        loc,
        status=Property.Status.PUBLISHED,
        published_at=timezone.now(),
    )
    # Second property in draft
    prop2 = create_prop(owner, pt, loc, status=Property.Status.DRAFT)

    eligibility = check_publication_eligibility(prop2)
    assert eligibility.free_publications_consumed == 1
    assert eligibility.free_remaining == 0
    assert eligibility.is_free is False
    assert eligibility.requires_payment is True
    assert eligibility.already_paid is False

    authenticate_as(api_client, owner)
    response = api_client.post(f"/api/properties/{prop2.pk}/submit-for-review/")
    assert response.status_code == 402
    assert response.data["requires_payment"] is True
    assert response.data["is_free"] is False
    assert response.data["free_remaining"] == 0
    assert response.data["free_publications_consumed"] == 1
    assert "detail" in response.data

    prop2.refresh_from_db()
    assert prop2.status == Property.Status.DRAFT


# ---------------------------------------------------------------------------
# Cas 3 — Plusieurs DRAFT ne consomment pas la publication gratuite
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_case_3_multiple_drafts_do_not_consume_free_publication(test_setup):
    pt, loc, owner, _, _, _ = test_setup
    prop_a = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    prop_b = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    prop_c = create_prop(owner, pt, loc, status=Property.Status.DRAFT)

    for p in (prop_a, prop_b, prop_c):
        eligibility = check_publication_eligibility(p)
        assert eligibility.free_publications_consumed == 0
        assert eligibility.free_remaining == 1
        assert eligibility.is_free is True
        assert eligibility.requires_payment is False


# ---------------------------------------------------------------------------
# Cas 4 — REJECTED ne consomme pas la publication gratuite
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_case_4_rejected_property_does_not_consume_and_can_resubmit_free(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    rejected_prop = create_prop(owner, pt, loc, status=Property.Status.REJECTED)

    eligibility = check_publication_eligibility(rejected_prop)
    assert eligibility.free_publications_consumed == 0
    assert eligibility.free_remaining == 1
    assert eligibility.is_free is True
    assert eligibility.requires_payment is False

    # Correction and resubmission
    authenticate_as(api_client, owner)
    response = api_client.post(f"/api/properties/{rejected_prop.pk}/submit-for-review/")
    assert response.status_code == 200
    assert response.data["status"] == Property.Status.PENDING_REVIEW


# ---------------------------------------------------------------------------
# Cas 5 — Property déjà publiée (published_at != null)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_case_5_already_published_property_does_not_require_new_payment(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    published_prop = create_prop(
        owner,
        pt,
        loc,
        status=Property.Status.SUSPENDED,
        published_at=timezone.now(),
    )

    eligibility = check_publication_eligibility(published_prop)
    assert eligibility.requires_payment is False
    assert eligibility.is_free is True


# ---------------------------------------------------------------------------
# Cas 6 — Payment PAID autorise la publication
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_case_6_paid_payment_authorizes_publication(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    # 1st property consumed the free slot
    create_prop(
        owner,
        pt,
        loc,
        status=Property.Status.PUBLISHED,
        published_at=timezone.now(),
    )

    # 2nd property has a PAID payment
    prop2 = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    Payment.objects.create(
        user=owner,
        property=prop2,
        provider="MONCASH",
        amount=Decimal("1500.00"),
        currency="HTG",
        status=Payment.Status.PAID,
        paid_at=timezone.now(),
    )

    eligibility = check_publication_eligibility(prop2)
    assert eligibility.requires_payment is False
    assert eligibility.already_paid is True
    assert eligibility.is_free is False

    # submit_for_review should succeed
    authenticate_as(api_client, owner)
    response = api_client.post(f"/api/properties/{prop2.pk}/submit-for-review/")
    assert response.status_code == 200
    assert response.data["status"] == Property.Status.PENDING_REVIEW


# ---------------------------------------------------------------------------
# Cas 7 — Payment FAILED nécessite un nouveau paiement
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_case_7_failed_payment_still_requires_payment(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    # 1st property consumed free slot
    create_prop(
        owner,
        pt,
        loc,
        status=Property.Status.PUBLISHED,
        published_at=timezone.now(),
    )

    # 2nd property has a FAILED payment
    prop2 = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    Payment.objects.create(
        user=owner,
        property=prop2,
        provider="MONCASH",
        amount=Decimal("1500.00"),
        currency="HTG",
        status=Payment.Status.FAILED,
    )

    eligibility = check_publication_eligibility(prop2)
    assert eligibility.requires_payment is True
    assert eligibility.already_paid is False

    authenticate_as(api_client, owner)
    response = api_client.post(f"/api/properties/{prop2.pk}/submit-for-review/")
    assert response.status_code == 402


# ---------------------------------------------------------------------------
# Cas 8 — Permissions d'accès à publication-eligibility
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_case_8_publication_eligibility_permissions(test_setup, api_client):
    pt, loc, owner, other_owner, normal_user, admin = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)

    # 1. Unauthenticated user -> 401
    resp_anon = api_client.get(f"/api/properties/{prop.pk}/publication-eligibility/")
    assert resp_anon.status_code == 401

    # 2. Regular user (role=USER) -> 403
    authenticate_as(api_client, normal_user)
    resp_user = api_client.get(f"/api/properties/{prop.pk}/publication-eligibility/")
    assert resp_user.status_code == 403

    # 3. Other OWNER -> 404 (draft filtered out by get_queryset) or 403
    authenticate_as(api_client, other_owner)
    resp_other = api_client.get(f"/api/properties/{prop.pk}/publication-eligibility/")
    assert resp_other.status_code in (403, 404)

    # 4. Property owner -> 200
    authenticate_as(api_client, owner)
    resp_owner = api_client.get(f"/api/properties/{prop.pk}/publication-eligibility/")
    assert resp_owner.status_code == 200

    # 5. Admin -> 200
    authenticate_as(api_client, admin)
    resp_admin = api_client.get(f"/api/properties/{prop.pk}/publication-eligibility/")
    assert resp_admin.status_code == 200


# ---------------------------------------------------------------------------
# Cas 9 — Endpoint GET /api/properties/{id}/publication-eligibility/ structure
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_case_9_publication_eligibility_endpoint_structure(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)

    authenticate_as(api_client, owner)
    response = api_client.get(f"/api/properties/{prop.pk}/publication-eligibility/")
    assert response.status_code == 200

    data = response.data
    assert data["property_id"] == str(prop.pk)
    assert data["is_free"] is True
    assert data["requires_payment"] is False
    assert data["already_paid"] is False
    assert data["free_publications_limit"] == 1
    assert data["free_publications_consumed"] == 0
    assert data["free_remaining"] == 1
    assert data["amount"] is None
    assert data["currency"] == "HTG"


# ---------------------------------------------------------------------------
# Cas 10 — submit_for_review transitions and payment checks
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_case_10_submit_for_review_scenarios(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup

    # A. Free publication allowed
    prop1 = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    authenticate_as(api_client, owner)
    resp1 = api_client.post(f"/api/properties/{prop1.pk}/submit-for-review/")
    assert resp1.status_code == 200
    assert resp1.data["status"] == Property.Status.PENDING_REVIEW

    # B. Payment required refused while 1st property is in review (holding free slot)
    prop2 = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    resp2 = api_client.post(f"/api/properties/{prop2.pk}/submit-for-review/")
    assert resp2.status_code == 402
    assert resp2.data["requires_payment"] is True

    # C. Paid publication allowed
    Payment.objects.create(
        user=owner,
        property=prop2,
        provider="MONCASH",
        amount=Decimal("1500.00"),
        currency="HTG",
        status=Payment.Status.PAID,
        paid_at=timezone.now(),
    )
    resp2_paid = api_client.post(f"/api/properties/{prop2.pk}/submit-for-review/")
    assert resp2_paid.status_code == 200
    assert resp2_paid.data["status"] == Property.Status.PENDING_REVIEW
