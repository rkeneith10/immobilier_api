import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.locations.models import Location
from apps.monetization.models import Payment
from apps.monetization.moncash_service import MonCashService
from apps.monetization.services import get_publication_price
from apps.properties.models import Property, PropertyType

User = get_user_model()
PASSWORD = "Secure-Password-521!"


@pytest.fixture(autouse=True)
def clear_moncash_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def moncash_test_settings(settings):
    settings.MONCASH_CLIENT_ID = "test-client-id"
    settings.MONCASH_CLIENT_SECRET = "test-client-secret"
    settings.MONCASH_API_URL = "https://sandbox.moncashbutton.digicelgroup.com/Api"
    settings.MONCASH_GATEWAY_URL = "https://sandbox.moncashbutton.digicelgroup.com/Moncash-middleware"



@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def test_setup(db):
    property_type = PropertyType.objects.create(name="Maison", slug="maison")
    location = Location.objects.create(name="Delmas", slug="delmas", type="CITY")

    owner = User.objects.create_user(
        email="owner_moncash@example.com", password=PASSWORD, role=User.Role.OWNER
    )
    other_owner = User.objects.create_user(
        email="other_moncash@example.com", password=PASSWORD, role=User.Role.OWNER
    )
    normal_user = User.objects.create_user(
        email="regular_moncash@example.com", password=PASSWORD, role=User.Role.USER
    )
    admin = User.objects.create_user(
        email="admin_moncash@example.com", password=PASSWORD, role=User.Role.ADMIN
    )

    return property_type, location, owner, other_owner, normal_user, admin


def create_prop(owner, property_type, location, **overrides):
    import uuid
    suffix = uuid.uuid4().hex[:6]
    data = {
        "owner": owner,
        "title": f"Villa {suffix}",
        "slug": f"villa-{suffix}",
        "description": "Propriété pour test MonCash.",
        "property_type": property_type,
        "listing_type": Property.ListingType.RENT,
        "price": Decimal("1200.00"),
        "currency": "USD",
        "bedrooms": 3,
        "bathrooms": Decimal("2.0"),
        "parking_spaces": 1,
        "area": Decimal("110.00"),
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
# Cas 1 — OWNER avec première publication : aucune initialisation MonCash
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_case_1_first_publication_is_free_no_moncash_payment(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)

    authenticate_as(api_client, owner)
    response = api_client.post(f"/api/properties/{prop.pk}/initiate-publication-payment/")
    assert response.status_code == 400
    assert "gratuite" in response.data["detail"]
    assert response.data["is_free"] is True
    assert Payment.objects.filter(property=prop).count() == 0


# ---------------------------------------------------------------------------
# Cas 2 — OWNER avec publication payante : création d'un Payment PENDING
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_case_2_second_publication_creates_pending_payment(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    # 1st property consumed free publication
    create_prop(owner, pt, loc, status=Property.Status.PUBLISHED, published_at=timezone.now())

    prop2 = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    authenticate_as(api_client, owner)

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "mock-token", "expires_in": 3600}

    mock_create = MagicMock(status_code=200)
    mock_create.json.return_value = {"status": 200, "payment_token": {"token": "mock-pay-token"}}

    with patch("requests.post", side_effect=[mock_oauth, mock_create]):
        response = api_client.post(f"/api/properties/{prop2.pk}/initiate-publication-payment/")

    assert response.status_code == 200
    assert response.data["status"] == "PENDING"
    assert response.data["provider"] == "MONCASH"
    assert response.data["currency"] == "HTG"
    assert Decimal(str(response.data["amount"])) == get_publication_price()

    payment = Payment.objects.get(property=prop2)
    assert payment.status == Payment.Status.PENDING
    assert payment.provider_payment_id == "mock-pay-token"
    assert payment.paid_at is None


# ---------------------------------------------------------------------------
# Cas 3 — CreatePayment MonCash réussi : redirect_url correctement générée
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_case_3_moncash_redirect_url_generated_correctly(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    create_prop(owner, pt, loc, status=Property.Status.PUBLISHED, published_at=timezone.now())
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    authenticate_as(api_client, owner)

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-xyz", "expires_in": 3600}

    mock_create = MagicMock(status_code=200)
    mock_create.json.return_value = {"status": 200, "payment_token": {"token": "token-12345"}}

    with patch("requests.post", side_effect=[mock_oauth, mock_create]):
        response = api_client.post(f"/api/properties/{prop.pk}/initiate-publication-payment/")

    assert response.status_code == 200
    redirect_url = response.data["redirect_url"]
    assert "token=token-12345" in redirect_url
    assert "/Payment/Redirect" in redirect_url


# ---------------------------------------------------------------------------
# Cas 4 — OAuth MonCash échoue : erreur propre et aucun PAID
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_case_4_oauth_failure_returns_clean_error(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    create_prop(owner, pt, loc, status=Property.Status.PUBLISHED, published_at=timezone.now())
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    authenticate_as(api_client, owner)

    mock_oauth = MagicMock(status_code=401)
    mock_oauth.json.return_value = {"error": "unauthorized"}

    with patch("requests.post", return_value=mock_oauth):
        response = api_client.post(f"/api/properties/{prop.pk}/initiate-publication-payment/")

    assert response.status_code == 502
    assert "Impossible d’initialiser le paiement" in response.data["detail"]

    # Verify payment status remains PENDING and never PAID
    payment = Payment.objects.get(property=prop)
    assert payment.status == Payment.Status.PENDING
    assert payment.paid_at is None


# ---------------------------------------------------------------------------
# Cas 5 — CreatePayment échoue : aucun PAID, reste PENDING
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_case_5_create_payment_failure_remains_pending(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    create_prop(owner, pt, loc, status=Property.Status.PUBLISHED, published_at=timezone.now())
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    authenticate_as(api_client, owner)

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-valid", "expires_in": 3600}

    mock_create = MagicMock(status_code=500)
    mock_create.json.return_value = {"error": "internal_error"}

    with patch("requests.post", side_effect=[mock_oauth, mock_create]):
        response = api_client.post(f"/api/properties/{prop.pk}/initiate-publication-payment/")

    assert response.status_code == 502
    payment = Payment.objects.get(property=prop)
    assert payment.status == Payment.Status.PENDING
    assert payment.paid_at is None
    assert prop.status == Property.Status.DRAFT


# ---------------------------------------------------------------------------
# Cas 6 — Propriété déjà payée : aucun nouveau paiement
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_case_6_already_paid_refuses_new_payment(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    create_prop(owner, pt, loc, status=Property.Status.PUBLISHED, published_at=timezone.now())
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)

    Payment.objects.create(
        user=owner,
        property=prop,
        provider="MONCASH",
        amount=Decimal("500.00"),
        currency="HTG",
        status=Payment.Status.PAID,
        paid_at=timezone.now(),
    )

    authenticate_as(api_client, owner)
    response = api_client.post(f"/api/properties/{prop.pk}/initiate-publication-payment/")
    assert response.status_code == 400
    assert "déjà un paiement confirmé" in response.data["detail"]
    assert Payment.objects.filter(property=prop).count() == 1


# ---------------------------------------------------------------------------
# Cas 7 — Paiement PENDING existant : comportement idempotent
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_case_7_existing_pending_payment_is_idempotent(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    create_prop(owner, pt, loc, status=Property.Status.PUBLISHED, published_at=timezone.now())
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)

    existing_payment = Payment.objects.create(
        user=owner,
        property=prop,
        provider="MONCASH",
        provider_payment_id="existing-token-888",
        amount=Decimal("500.00"),
        currency="HTG",
        status=Payment.Status.PENDING,
    )

    authenticate_as(api_client, owner)
    response = api_client.post(f"/api/properties/{prop.pk}/initiate-publication-payment/")

    assert response.status_code == 200
    assert response.data["payment_id"] == str(existing_payment.pk)
    assert "token=existing-token-888" in response.data["redirect_url"]
    assert Payment.objects.filter(property=prop).count() == 1


# ---------------------------------------------------------------------------
# Cas 8 — Autre OWNER : accès refusé
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_case_8_other_owner_cannot_initiate_payment(test_setup, api_client):
    pt, loc, owner, other_owner, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)

    authenticate_as(api_client, other_owner)
    response = api_client.post(f"/api/properties/{prop.pk}/initiate-publication-payment/")
    assert response.status_code in (403, 404)


# ---------------------------------------------------------------------------
# Cas 9 — Utilisateur non authentifié : accès refusé
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_case_9_unauthenticated_cannot_initiate_payment(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)

    response = api_client.post(f"/api/properties/{prop.pk}/initiate-publication-payment/")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Cas 10 — Frontend tente d'envoyer un montant différent : backend ignore/utilise serveur
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_case_10_client_amount_is_ignored_server_price_used(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    create_prop(owner, pt, loc, status=Property.Status.PUBLISHED, published_at=timezone.now())
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    authenticate_as(api_client, owner)

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-test", "expires_in": 3600}

    mock_create = MagicMock(status_code=200)
    mock_create.json.return_value = {"status": 200, "payment_token": {"token": "token-ignore-amt"}}

    with patch("requests.post", side_effect=[mock_oauth, mock_create]) as mock_post:
        # Client tries to send amount = 1 and currency = USD
        response = api_client.post(
            f"/api/properties/{prop.pk}/initiate-publication-payment/",
            {"amount": 1, "currency": "USD"},
            format="json",
        )

    assert response.status_code == 200
    assert Decimal(str(response.data["amount"])) == get_publication_price()
    assert response.data["currency"] == "HTG"

    # Verify CreatePayment received the server price, not 1
    create_call = mock_post.call_args_list[1]
    assert create_call.kwargs["json"]["amount"] == int(get_publication_price())
