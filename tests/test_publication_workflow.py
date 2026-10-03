import concurrent.futures
from decimal import Decimal
import pytest

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.locations.models import Location
from apps.monetization.models import Payment
from apps.properties.models import Property, PropertyType

User = get_user_model()
PASSWORD = "Secure-Password-521!"


@pytest.fixture(autouse=True)
def clear_caches():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def test_setup(db):
    property_type = PropertyType.objects.create(name="Villa", slug="villa")
    location = Location.objects.create(name="Jacmel", slug="jacmel", type="CITY")

    owner = User.objects.create_user(
        email="owner_wf@example.com", password=PASSWORD, role=User.Role.OWNER
    )
    other_owner = User.objects.create_user(
        email="other_wf@example.com", password=PASSWORD, role=User.Role.OWNER
    )
    admin = User.objects.create_user(
        email="admin_wf@example.com", password=PASSWORD, role=User.Role.ADMIN
    )
    normal_user = User.objects.create_user(
        email="user_wf@example.com", password=PASSWORD, role=User.Role.USER
    )

    return property_type, location, owner, other_owner, admin, normal_user


def create_prop(owner, property_type, location, **overrides):
    import uuid
    suffix = uuid.uuid4().hex[:6]
    data = {
        "owner": owner,
        "title": f"Maison {suffix}",
        "slug": f"maison-{suffix}",
        "description": "Propriété pour test workflow de publication.",
        "property_type": property_type,
        "listing_type": Property.ListingType.RENT,
        "price": Decimal("800.00"),
        "currency": "USD",
        "bedrooms": 2,
        "bathrooms": Decimal("1.0"),
        "parking_spaces": 1,
        "area": Decimal("75.00"),
        "area_unit": Property.AreaUnit.SQM,
        "furnished": False,
        "location": location,
        "status": Property.Status.DRAFT,
    }
    data.update(overrides)
    return Property.objects.create(**data)


def authenticate_as(client, user):
    token = AccessToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")


# ===========================================================================
# 1. Première propriété -> soumission gratuite acceptée
# ===========================================================================
@pytest.mark.django_db
def test_scenario_1_first_property_free_submission_accepted(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)

    authenticate_as(api_client, owner)
    response = api_client.post(f"/api/properties/{prop.pk}/submit-for-review/")

    assert response.status_code == 200
    assert response.data["status"] == Property.Status.PENDING_REVIEW

    prop.refresh_from_db()
    assert prop.status == Property.Status.PENDING_REVIEW
    # No payment was created
    assert Payment.objects.filter(property=prop).count() == 0


# ===========================================================================
# 2. Deuxième propriété sans paiement -> refusée HTTP 402
# ===========================================================================
@pytest.mark.django_db
def test_scenario_2_second_property_without_payment_refused(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    # 1st property is published
    create_prop(
        owner, pt, loc, status=Property.Status.PUBLISHED, published_at=timezone.now()
    )

    prop2 = create_prop(owner, pt, loc, status=Property.Status.DRAFT)

    authenticate_as(api_client, owner)
    response = api_client.post(f"/api/properties/{prop2.pk}/submit-for-review/")

    assert response.status_code == 402
    assert response.data["requires_payment"] is True
    assert response.data["already_paid"] is False

    prop2.refresh_from_db()
    assert prop2.status == Property.Status.DRAFT


# ===========================================================================
# 3. Deuxième propriété avec paiement PENDING -> refusée HTTP 402
# ===========================================================================
@pytest.mark.django_db
def test_scenario_3_second_property_with_pending_payment_refused(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    create_prop(
        owner, pt, loc, status=Property.Status.PUBLISHED, published_at=timezone.now()
    )

    prop2 = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    Payment.objects.create(
        user=owner,
        property=prop2,
        provider="MONCASH",
        amount=Decimal("500.00"),
        currency="HTG",
        status=Payment.Status.PENDING,
    )

    authenticate_as(api_client, owner)
    response = api_client.post(f"/api/properties/{prop2.pk}/submit-for-review/")

    assert response.status_code == 402
    assert response.data["requires_payment"] is True
    assert response.data["already_paid"] is False

    prop2.refresh_from_db()
    assert prop2.status == Property.Status.DRAFT


# ===========================================================================
# 4. Deuxième propriété avec paiement FAILED -> refusée HTTP 402
# ===========================================================================
@pytest.mark.django_db
def test_scenario_4_second_property_with_failed_payment_refused(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    create_prop(
        owner, pt, loc, status=Property.Status.PUBLISHED, published_at=timezone.now()
    )

    prop2 = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    Payment.objects.create(
        user=owner,
        property=prop2,
        provider="MONCASH",
        amount=Decimal("500.00"),
        currency="HTG",
        status=Payment.Status.FAILED,
    )

    authenticate_as(api_client, owner)
    response = api_client.post(f"/api/properties/{prop2.pk}/submit-for-review/")

    assert response.status_code == 402
    assert response.data["requires_payment"] is True
    assert response.data["already_paid"] is False

    prop2.refresh_from_db()
    assert prop2.status == Property.Status.DRAFT


# ===========================================================================
# 5. Deuxième propriété avec paiement PAID -> soumission acceptée
# ===========================================================================
@pytest.mark.django_db
def test_scenario_5_second_property_with_paid_payment_accepted(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    create_prop(
        owner, pt, loc, status=Property.Status.PUBLISHED, published_at=timezone.now()
    )

    prop2 = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    Payment.objects.create(
        user=owner,
        property=prop2,
        provider="MONCASH",
        amount=Decimal("500.00"),
        currency="HTG",
        status=Payment.Status.PAID,
        paid_at=timezone.now(),
    )

    authenticate_as(api_client, owner)
    response = api_client.post(f"/api/properties/{prop2.pk}/submit-for-review/")

    assert response.status_code == 200
    assert response.data["status"] == Property.Status.PENDING_REVIEW

    prop2.refresh_from_db()
    assert prop2.status == Property.Status.PENDING_REVIEW


# ===========================================================================
# 6. Propriété REJECTED corrigée -> resoumission correcte
# ===========================================================================
@pytest.mark.django_db
def test_scenario_6_rejected_property_can_be_edited_and_resubmitted_free(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.REJECTED)

    authenticate_as(api_client, owner)

    # Owner edits property
    edit_resp = api_client.patch(
        f"/api/properties/{prop.pk}/",
        {"title": "Maison corrigée après rejet"},
        format="json",
    )
    assert edit_resp.status_code == 200
    assert edit_resp.data["title"] == "Maison corrigée après rejet"

    # Resubmission
    resubmit_resp = api_client.post(f"/api/properties/{prop.pk}/submit-for-review/")
    assert resubmit_resp.status_code == 200
    assert resubmit_resp.data["status"] == Property.Status.PENDING_REVIEW

    prop.refresh_from_db()
    assert prop.status == Property.Status.PENDING_REVIEW


# ===========================================================================
# 7. Propriété REJECTED déjà payée -> aucun second paiement
# ===========================================================================
@pytest.mark.django_db
def test_scenario_7_rejected_paid_property_resubmits_without_second_payment(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    # 1st property consumed free slot
    create_prop(
        owner, pt, loc, status=Property.Status.PUBLISHED, published_at=timezone.now()
    )

    # 2nd property was paid and then rejected
    prop2 = create_prop(owner, pt, loc, status=Property.Status.REJECTED)
    Payment.objects.create(
        user=owner,
        property=prop2,
        provider="MONCASH",
        amount=Decimal("500.00"),
        currency="HTG",
        status=Payment.Status.PAID,
        paid_at=timezone.now(),
    )

    authenticate_as(api_client, owner)
    response = api_client.post(f"/api/properties/{prop2.pk}/submit-for-review/")

    assert response.status_code == 200
    assert response.data["status"] == Property.Status.PENDING_REVIEW

    # Still only 1 payment in database
    assert Payment.objects.filter(property=prop2).count() == 1


# ===========================================================================
# 8. Propriété PUBLISHED modifiée -> aucun nouveau paiement
# ===========================================================================
@pytest.mark.django_db
def test_scenario_8_published_property_update_requires_no_payment(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(
        owner, pt, loc, status=Property.Status.PUBLISHED, published_at=timezone.now()
    )

    authenticate_as(api_client, owner)

    # Edit property
    edit_resp = api_client.patch(
        f"/api/properties/{prop.pk}/",
        {"price": "950.00"},
        format="json",
    )
    assert edit_resp.status_code == 200
    assert Decimal(str(edit_resp.data["price"])) == Decimal("950.00")

    # Check eligibility
    elig_resp = api_client.get(f"/api/properties/{prop.pk}/publication-eligibility/")
    assert elig_resp.status_code == 200
    assert elig_resp.data["is_free"] is True
    assert elig_resp.data["requires_payment"] is False


# ===========================================================================
# 9. Faux payment_status=PAID envoyé par client -> ignoré
# ===========================================================================
@pytest.mark.django_db
def test_scenario_9_client_fake_payment_status_ignored(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    create_prop(
        owner, pt, loc, status=Property.Status.PUBLISHED, published_at=timezone.now()
    )

    prop2 = create_prop(owner, pt, loc, status=Property.Status.DRAFT)

    authenticate_as(api_client, owner)
    response = api_client.post(
        f"/api/properties/{prop2.pk}/submit-for-review/",
        {"payment_status": "PAID", "paid": True, "status": "PAID"},
        format="json",
    )

    assert response.status_code == 402
    assert response.data["requires_payment"] is True
    assert response.data["already_paid"] is False

    prop2.refresh_from_db()
    assert prop2.status == Property.Status.DRAFT


# ===========================================================================
# 10. Faux montant envoyé par client -> ignoré
# ===========================================================================
@pytest.mark.django_db
def test_scenario_10_client_fake_amount_ignored(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    create_prop(
        owner, pt, loc, status=Property.Status.PUBLISHED, published_at=timezone.now()
    )

    prop2 = create_prop(owner, pt, loc, status=Property.Status.DRAFT)

    authenticate_as(api_client, owner)
    response = api_client.post(
        f"/api/properties/{prop2.pk}/submit-for-review/",
        {"amount": "0.00", "currency": "USD"},
        format="json",
    )

    assert response.status_code == 402
    assert response.data["requires_payment"] is True


# ===========================================================================
# 11. ADMIN peut publier une propriété gratuite
# ===========================================================================
@pytest.mark.django_db
def test_scenario_11_admin_can_publish_free_property(test_setup, api_client):
    pt, loc, owner, _, admin, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.PENDING_REVIEW)

    authenticate_as(api_client, admin)
    response = api_client.post(f"/api/properties/{prop.pk}/publish/")

    assert response.status_code == 200
    assert response.data["status"] == Property.Status.PUBLISHED

    prop.refresh_from_db()
    assert prop.status == Property.Status.PUBLISHED
    assert prop.published_at is not None


# ===========================================================================
# 12. ADMIN peut publier une propriété payée
# ===========================================================================
@pytest.mark.django_db
def test_scenario_12_admin_can_publish_paid_property(test_setup, api_client):
    pt, loc, owner, _, admin, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.PENDING_REVIEW)
    Payment.objects.create(
        user=owner,
        property=prop,
        provider="MONCASH",
        amount=Decimal("500.00"),
        currency="HTG",
        status=Payment.Status.PAID,
        paid_at=timezone.now(),
    )

    authenticate_as(api_client, admin)
    response = api_client.post(f"/api/properties/{prop.pk}/publish/")

    assert response.status_code == 200
    assert response.data["status"] == Property.Status.PUBLISHED

    prop.refresh_from_db()
    assert prop.status == Property.Status.PUBLISHED
    assert prop.published_at is not None


# ===========================================================================
# 13. Utilisateur non propriétaire -> accès refusé
# ===========================================================================
@pytest.mark.django_db
def test_scenario_13_non_owner_cannot_submit_for_review(test_setup, api_client):
    pt, loc, owner, other_owner, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)

    authenticate_as(api_client, other_owner)
    response = api_client.post(f"/api/properties/{prop.pk}/submit-for-review/")

    assert response.status_code in (403, 404)
    prop.refresh_from_db()
    assert prop.status == Property.Status.DRAFT


# ===========================================================================
# 14. Utilisateur non authentifié -> 401
# ===========================================================================
@pytest.mark.django_db
def test_scenario_14_unauthenticated_cannot_submit(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)

    response = api_client.post(f"/api/properties/{prop.pk}/submit-for-review/")
    assert response.status_code == 401


# ===========================================================================
# 15. Requêtes concurrentes -> aucune double consommation du quota gratuit
# ===========================================================================
@pytest.mark.django_db(transaction=True)
def test_scenario_15_concurrent_submissions_prevent_double_free_consumption(test_setup):
    pt, loc, owner, _, _, _ = test_setup
    prop_a = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    prop_b = create_prop(owner, pt, loc, status=Property.Status.DRAFT)

    def submit_property(prop_pk):
        client = APIClient()
        token = AccessToken.for_user(owner)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        try:
            resp = client.post(f"/api/properties/{prop_pk}/submit-for-review/")
            return resp.status_code
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_a = executor.submit(submit_property, prop_a.pk)
        future_b = executor.submit(submit_property, prop_b.pk)
        status_a = future_a.result()
        status_b = future_b.result()

    statuses = [status_a, status_b]
    # Exactly one request must succeed (200) and the other must be blocked (402)
    assert 200 in statuses, f"Expected one 200 OK, got: {statuses}"
    assert 402 in statuses, f"Expected one 402 Payment Required, got: {statuses}"

    prop_a.refresh_from_db()
    prop_b.refresh_from_db()
    review_count = sum(
        1 for p in (prop_a, prop_b) if p.status == Property.Status.PENDING_REVIEW
    )
    draft_count = sum(
        1 for p in (prop_a, prop_b) if p.status == Property.Status.DRAFT
    )
    assert review_count == 1, "Exactly one property should be in PENDING_REVIEW"
    assert draft_count == 1, "The second property should have remained in DRAFT"
