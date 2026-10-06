import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.locations.models import Location
from apps.monetization.kobara_service import (
    KobaraAuthError,
    KobaraConfigError,
    KobaraNetworkError,
    KobaraService,
)
from apps.monetization.models import Invoice, Payment
from apps.monetization.services import get_publication_price
from apps.properties.models import Property, PropertyType
from kobara.errors import KobaraAPIError

User = get_user_model()
PASSWORD = "Secure-Password-521!"


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def kobara_test_settings(settings):
    settings.DEFAULT_PAYMENT_PROVIDER = "KOBARA"
    settings.KOBARA_SECRET_KEY = "kbr_sk_test_mock_dummy_secret"
    settings.KOBARA_WEBHOOK_SECRET = "kbr_whsec_mock_dummy"
    settings.KOBARA_PROVIDER = "kobara"
    settings.KOBARA_BASE_URL = "https://api.kobara.app/v1"
    settings.FRONTEND_URL = "http://localhost:3000"


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def test_setup(db):
    property_type = PropertyType.objects.create(name="Maison", slug="maison")
    location = Location.objects.create(name="Delmas", slug="delmas", type="CITY")

    owner = User.objects.create_user(
        email="owner_kobara@example.com",
        first_name="Jean",
        last_name="Pierre",
        phone="50937000000",
        password=PASSWORD,
        role=User.Role.OWNER,
    )
    other_owner = User.objects.create_user(
        email="other_owner_kobara@example.com",
        password=PASSWORD,
        role=User.Role.OWNER,
    )
    normal_user = User.objects.create_user(
        email="user_kobara@example.com",
        password=PASSWORD,
        role=User.Role.USER,
    )
    admin = User.objects.create_user(
        email="admin_kobara@example.com",
        password=PASSWORD,
        role=User.Role.ADMIN,
    )

    return property_type, location, owner, other_owner, normal_user, admin


def auth_client(client, user):
    token = str(AccessToken.for_user(user))
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def create_property(owner, property_type, location, title="Annonce Test", published=False, **overrides):
    import uuid
    suffix = uuid.uuid4().hex[:8]
    now = timezone.now()
    data = {
        "owner": owner,
        "property_type": property_type,
        "location": location,
        "title": title,
        "slug": f"prop-{suffix}",
        "description": "Une belle description pour le test",
        "price": Decimal("150000.00"),
        "currency": "USD",
        "listing_type": Property.ListingType.SALE,
        "status": Property.Status.PUBLISHED if published else Property.Status.DRAFT,
        "published_at": now if published else None,
    }
    data.update(overrides)
    return Property.objects.create(**data)


# --------------------------------------------------------------------------
# 1. Eligibility & Free / Paid Logic
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_first_publication_is_free_no_payment_initiated(api_client, test_setup):
    property_type, location, owner, _, _, _ = test_setup
    prop = create_property(owner, property_type, location)

    client = auth_client(api_client, owner)
    url = f"/api/properties/{prop.id}/initiate-publication-payment/"
    response = client.post(url)

    assert response.status_code == 400
    assert response.data.get("is_free") is True
    assert "gratuite" in response.data.get("detail", "").lower()
    assert Payment.objects.filter(property=prop).count() == 0


@pytest.mark.django_db
def test_second_publication_requires_payment(api_client, test_setup):
    property_type, location, owner, _, _, _ = test_setup
    # 1st published property consumes the free slot
    create_property(owner, property_type, location, title="Prop 1 Free", published=True)

    # 2nd property requires payment
    prop2 = create_property(owner, property_type, location, title="Prop 2 Paid")

    mock_client = MagicMock()
    mock_client.payments.create.return_value = {
        "data": {
            "id": "pay_test_001",
            "checkout_url": "https://pay.kobara.app/checkout/c_test_001",
            "status": "pending",
        }
    }

    with patch.object(KobaraService, "get_client", return_value=mock_client):
        client = auth_client(api_client, owner)
        url = f"/api/properties/{prop2.id}/initiate-publication-payment/"
        response = client.post(url)

    assert response.status_code == 200
    assert response.data["provider"] == "KOBARA"
    assert response.data["status"] == "PENDING"
    assert response.data["amount"] == "500.00"
    assert response.data["currency"] == "HTG"
    assert response.data["redirect_url"] == "https://pay.kobara.app/checkout/c_test_001"


# --------------------------------------------------------------------------
# 2. Server-Enforced Amount (Frontend Amount Ignored)
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_frontend_sent_amount_is_completely_ignored(api_client, test_setup):
    property_type, location, owner, _, _, _ = test_setup
    create_property(owner, property_type, location, published=True)
    prop2 = create_property(owner, property_type, location, title="Prop 2 Paid")

    mock_client = MagicMock()
    mock_client.payments.create.return_value = {
        "data": {
            "id": "pay_test_002",
            "checkout_url": "https://pay.kobara.app/checkout/c_test_002",
            "status": "pending",
        }
    }

    with patch.object(KobaraService, "get_client", return_value=mock_client):
        client = auth_client(api_client, owner)
        url = f"/api/properties/{prop2.id}/initiate-publication-payment/"
        # Client tries to send a malicious or customized amount
        response = client.post(url, {"amount": 10.0, "currency": "USD"}, format="json")

    assert response.status_code == 200
    assert response.data["amount"] == "500.00"
    assert response.data["currency"] == "HTG"

    # Verify what Kobara SDK received
    call_args, call_kwargs = mock_client.payments.create.call_args
    payload = call_args[0]
    assert payload["amount"] == 500
    assert payload["currency"] == "HTG"


# --------------------------------------------------------------------------
# 3. Customer Info, Metadata, Idempotency-Key
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_kobara_payload_metadata_and_idempotency_key(api_client, test_setup):
    property_type, location, owner, _, _, _ = test_setup
    create_property(owner, property_type, location, published=True)
    prop2 = create_property(owner, property_type, location, title="Prop 2 Paid")

    mock_client = MagicMock()
    mock_client.payments.create.return_value = {
        "data": {
            "id": "pay_test_003",
            "checkout_url": "https://pay.kobara.app/checkout/c_test_003",
            "status": "pending",
        }
    }

    with patch.object(KobaraService, "get_client", return_value=mock_client):
        client = auth_client(api_client, owner)
        url = f"/api/properties/{prop2.id}/initiate-publication-payment/"
        response = client.post(url)

    assert response.status_code == 200
    payment = Payment.objects.get(property=prop2)

    # Inspect call to SDK
    call_args, call_kwargs = mock_client.payments.create.call_args
    payload = call_args[0]
    idempotency_key = call_kwargs.get("idempotency_key")

    assert idempotency_key == str(payment.order_id)
    assert payload["metadata"]["order_id"] == str(payment.order_id)
    assert payload["metadata"]["property_id"] == str(prop2.id)
    assert payload["metadata"]["payment_id"] == str(payment.id)
    assert payload["customer"]["name"] == "Jean Pierre"
    assert payload["customer"]["email"] == "owner_kobara@example.com"
    assert payload["customer"]["phone"] == "50937000000"
    assert payload["success_url"] == f"http://localhost:3000/owner/properties/{prop2.id}/payment-return"
    assert payload["cancel_url"] == f"http://localhost:3000/owner/properties/{prop2.id}"

    # Database checks
    assert payment.provider == "KOBARA"
    assert payment.provider_payment_id == "pay_test_003"
    assert payment.status == Payment.Status.PENDING

    # Invoice created
    invoice = Invoice.objects.get(payment=payment)
    assert invoice.status == Invoice.Status.ISSUED
    assert invoice.amount == Decimal("500.00")
    assert invoice.currency == "HTG"


# --------------------------------------------------------------------------
# 4. Idempotence: Double-click / Retry
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_double_initiate_reuses_pending_payment(api_client, test_setup):
    property_type, location, owner, _, _, _ = test_setup
    create_property(owner, property_type, location, published=True)
    prop2 = create_property(owner, property_type, location, title="Prop 2 Paid")

    mock_client = MagicMock()
    mock_client.payments.create.return_value = {
        "data": {
            "id": "pay_test_004",
            "checkout_url": "https://pay.kobara.app/checkout/c_test_004",
            "status": "pending",
        }
    }

    with patch.object(KobaraService, "get_client", return_value=mock_client):
        client = auth_client(api_client, owner)
        url = f"/api/properties/{prop2.id}/initiate-publication-payment/"

        # First click
        res1 = client.post(url)
        assert res1.status_code == 200
        payment_id_1 = res1.data["payment_id"]

        # Second click (retry)
        res2 = client.post(url)
        assert res2.status_code == 200
        payment_id_2 = res2.data["payment_id"]

    # Both requests must return the exact same payment record
    assert payment_id_1 == payment_id_2
    assert Payment.objects.filter(property=prop2).count() == 1


# --------------------------------------------------------------------------
# 5. Error Handling
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_missing_secret_key_returns_500(api_client, test_setup, settings):
    settings.KOBARA_SECRET_KEY = ""
    property_type, location, owner, _, _, _ = test_setup
    create_property(owner, property_type, location, published=True)
    prop2 = create_property(owner, property_type, location, title="Prop 2 Paid")

    client = auth_client(api_client, owner)
    url = f"/api/properties/{prop2.id}/initiate-publication-payment/"
    response = client.post(url)

    assert response.status_code == 500
    assert "configurée" in response.data.get("detail", "").lower()
    # Property remains DRAFT
    prop2.refresh_from_db()
    assert prop2.status == Property.Status.DRAFT


@pytest.mark.django_db
def test_kobara_auth_error_returns_502(api_client, test_setup):
    property_type, location, owner, _, _, _ = test_setup
    create_property(owner, property_type, location, published=True)
    prop2 = create_property(owner, property_type, location, title="Prop 2 Paid")

    mock_client = MagicMock()
    mock_client.payments.create.side_effect = KobaraAPIError("Invalid API Key", status_code=401)

    with patch.object(KobaraService, "get_client", return_value=mock_client):
        client = auth_client(api_client, owner)
        url = f"/api/properties/{prop2.id}/initiate-publication-payment/"
        response = client.post(url)

    assert response.status_code == 502
    assert "impossible d’initialiser le paiement" in response.data.get("detail", "").lower()


@pytest.mark.django_db
def test_kobara_network_timeout_returns_502(api_client, test_setup):
    property_type, location, owner, _, _, _ = test_setup
    create_property(owner, property_type, location, published=True)
    prop2 = create_property(owner, property_type, location, title="Prop 2 Paid")

    mock_client = MagicMock()
    mock_client.payments.create.side_effect = RuntimeError("Failed to connect to Kobara API: Connection timed out")

    with patch.object(KobaraService, "get_client", return_value=mock_client):
        client = auth_client(api_client, owner)
        url = f"/api/properties/{prop2.id}/initiate-publication-payment/"
        response = client.post(url)

    assert response.status_code == 502
    assert "impossible d’initialiser le paiement" in response.data.get("detail", "").lower()


@pytest.mark.django_db
def test_kobara_invalid_response_missing_checkout_url_returns_502(api_client, test_setup):
    property_type, location, owner, _, _, _ = test_setup
    create_property(owner, property_type, location, published=True)
    prop2 = create_property(owner, property_type, location, title="Prop 2 Paid")

    mock_client = MagicMock()
    mock_client.payments.create.return_value = {
        "data": {
            "id": "pay_test_no_url",
            # missing checkout_url
        }
    }

    with patch.object(KobaraService, "get_client", return_value=mock_client):
        client = auth_client(api_client, owner)
        url = f"/api/properties/{prop2.id}/initiate-publication-payment/"
        response = client.post(url)

    assert response.status_code == 502


# --------------------------------------------------------------------------
# 6. Permissions and Ownership
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_unauthenticated_user_cannot_initiate_payment(api_client, test_setup):
    property_type, location, owner, _, _, _ = test_setup
    prop = create_property(owner, property_type, location)

    url = f"/api/properties/{prop.id}/initiate-publication-payment/"
    response = api_client.post(url)
    assert response.status_code == 401


@pytest.mark.django_db
def test_other_owner_cannot_initiate_payment(api_client, test_setup):
    property_type, location, owner, other_owner, _, _ = test_setup
    create_property(owner, property_type, location, published=True)
    prop = create_property(owner, property_type, location, title="Owner 1 Property")

    client = auth_client(api_client, other_owner)
    url = f"/api/properties/{prop.id}/initiate-publication-payment/"
    response = client.post(url)
    assert response.status_code in (403, 404)


@pytest.mark.django_db
def test_admin_can_initiate_payment_for_owner(api_client, test_setup):
    property_type, location, owner, _, _, admin = test_setup
    create_property(owner, property_type, location, published=True)
    prop = create_property(owner, property_type, location, title="Owner 1 Property")

    mock_client = MagicMock()
    mock_client.payments.create.return_value = {
        "data": {
            "id": "pay_admin_001",
            "checkout_url": "https://pay.kobara.app/checkout/c_admin_001",
            "status": "pending",
        }
    }

    with patch.object(KobaraService, "get_client", return_value=mock_client):
        client = auth_client(api_client, admin)
        url = f"/api/properties/{prop.id}/initiate-publication-payment/"
        response = client.post(url)

    assert response.status_code == 200
    assert response.data["provider"] == "KOBARA"


# --------------------------------------------------------------------------
# 7. Verification Endpoint Behavior with Kobara
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_verify_publication_payment_keeps_pending_without_premature_paid(api_client, test_setup):
    property_type, location, owner, _, _, _ = test_setup
    create_property(owner, property_type, location, published=True)
    prop = create_property(owner, property_type, location, title="Prop Pending")

    # Manually create a PENDING Kobara payment
    payment = Payment.objects.create(
        user=owner,
        property=prop,
        provider="KOBARA",
        provider_payment_id="pay_pending_123",
        amount=Decimal("500.00"),
        currency="HTG",
        status=Payment.Status.PENDING,
    )

    client = auth_client(api_client, owner)
    url = f"/api/properties/{prop.id}/verify-publication-payment/"
    response = client.post(url)

    assert response.status_code == 200
    assert response.data["status"] == "PENDING"
    assert response.data["provider"] == "KOBARA"

    # Property remains DRAFT
    prop.refresh_from_db()
    assert prop.status == Property.Status.DRAFT
    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING


@pytest.mark.django_db
def test_verify_publication_payment_when_already_paid(api_client, test_setup):
    property_type, location, owner, _, _, _ = test_setup
    prop = create_property(owner, property_type, location, title="Prop Paid")

    now = timezone.now()
    payment = Payment.objects.create(
        user=owner,
        property=prop,
        provider="KOBARA",
        provider_payment_id="pay_confirmed_123",
        provider_transaction_id="tx_123",
        amount=Decimal("500.00"),
        currency="HTG",
        status=Payment.Status.PAID,
        paid_at=now,
    )

    client = auth_client(api_client, owner)
    url = f"/api/properties/{prop.id}/verify-publication-payment/"
    response = client.post(url)

    assert response.status_code == 200
    assert response.data["status"] == "PAID"
    assert response.data["provider"] == "KOBARA"
    assert response.data["provider_transaction_id"] == "tx_123"
