import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken
import requests

from apps.locations.models import Location
from apps.monetization.models import Invoice, Payment
from apps.monetization.moncash_service import MonCashService
from apps.monetization.services import create_property_publication_payment
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
        email="owner_verify@example.com", password=PASSWORD, role=User.Role.OWNER
    )
    other_owner = User.objects.create_user(
        email="other_verify@example.com", password=PASSWORD, role=User.Role.OWNER
    )
    normal_user = User.objects.create_user(
        email="regular_verify@example.com", password=PASSWORD, role=User.Role.USER
    )
    admin = User.objects.create_user(
        email="admin_verify@example.com", password=PASSWORD, role=User.Role.ADMIN
    )

    return property_type, location, owner, other_owner, normal_user, admin


def create_prop(owner, property_type, location, **overrides):
    import uuid
    suffix = uuid.uuid4().hex[:6]
    data = {
        "owner": owner,
        "title": f"Maison {suffix}",
        "slug": f"maison-{suffix}",
        "description": "Propriété pour test de vérification MonCash.",
        "property_type": property_type,
        "listing_type": Property.ListingType.RENT,
        "price": Decimal("1000.00"),
        "currency": "USD",
        "bedrooms": 2,
        "bathrooms": Decimal("1.5"),
        "parking_spaces": 1,
        "area": Decimal("90.00"),
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
# 1. Paiement PENDING confirmé par MonCash -> PAID
# ===========================================================================
@pytest.mark.django_db
def test_scenario_1_pending_confirmed_by_moncash_becomes_paid(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.provider_payment_id = "token-test-1"
    payment.save(update_fields=("provider_payment_id",))

    authenticate_as(api_client, owner)

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-oauth-1", "expires_in": 3600}

    mock_retrieve = MagicMock(status_code=200)
    mock_retrieve.json.return_value = {
        "status": 200,
        "message": "successful",
        "payment": {
            "reference": "REF-MONCASH-001",
            "transaction_id": "TXN-987654321",
            "cost": 500,
            "message": "successful",
            "payer": "50937123456",
            "date": "2026-10-03 16:30:00",
        },
    }

    with patch("requests.post", side_effect=[mock_oauth, mock_retrieve]):
        response = api_client.post(f"/api/properties/{prop.pk}/verify-publication-payment/")

    assert response.status_code == 200
    assert response.data["status"] == "PAID"
    assert response.data["payment_id"] == str(payment.pk)
    assert response.data["property_id"] == str(prop.pk)
    assert response.data["order_id"] == payment.order_id
    assert response.data["amount"] == "500.00"
    assert response.data["currency"] == "HTG"
    assert response.data["provider"] == "MONCASH"
    assert response.data["provider_transaction_id"] == "TXN-987654321"
    assert response.data["paid_at"] is not None

    # Check database persistence
    payment.refresh_from_db()
    assert payment.status == Payment.Status.PAID
    assert payment.paid_at is not None
    assert payment.provider_transaction_id == "TXN-987654321"

    # Check Invoice status
    invoice = Invoice.objects.get(payment=payment)
    assert invoice.status == Invoice.Status.PAID
    assert invoice.paid_at == payment.paid_at

    # Property status remains DRAFT (not auto-published!)
    prop.refresh_from_db()
    assert prop.status == Property.Status.DRAFT


# ===========================================================================
# 2. Paiement déjà PAID -> réponse idempotente sans appel MonCash
# ===========================================================================
@pytest.mark.django_db
def test_scenario_2_already_paid_idempotent_no_moncash_call(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    paid_time = timezone.now()
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.status = Payment.Status.PAID
    payment.paid_at = paid_time
    payment.provider_payment_id = "token-already-paid"
    payment.provider_transaction_id = "TXN-ALREADY-CONFIRMED"
    payment.save()

    authenticate_as(api_client, owner)

    with patch("requests.post") as mock_post:
        response = api_client.post(f"/api/properties/{prop.pk}/verify-publication-payment/")

    assert response.status_code == 200
    assert response.data["status"] == "PAID"
    assert response.data["provider_transaction_id"] == "TXN-ALREADY-CONFIRMED"
    assert mock_post.call_count == 0

    # Ensure paid_at has not changed
    payment.refresh_from_db()
    assert payment.paid_at == paid_time
    assert Payment.objects.filter(property=prop).count() == 1


# ===========================================================================
# 3. Paiement encore PENDING chez MonCash -> reste PENDING
# ===========================================================================
@pytest.mark.django_db
def test_scenario_3_still_pending_at_moncash_remains_pending(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.provider_payment_id = "token-pending-1"
    payment.save(update_fields=("provider_payment_id",))

    authenticate_as(api_client, owner)

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-valid", "expires_in": 3600}

    # MonCash returns HTTP 404 for uncompleted order
    mock_retrieve = MagicMock(status_code=404)
    mock_retrieve.json.return_value = {"status": 404, "error": "Not Found", "message": "Order Not Found"}

    with patch("requests.post", side_effect=[mock_oauth, mock_retrieve]):
        response = api_client.post(f"/api/properties/{prop.pk}/verify-publication-payment/")

    assert response.status_code == 200
    assert response.data["status"] == "PENDING"
    assert response.data["paid_at"] is None

    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING
    assert payment.paid_at is None


# ===========================================================================
# 4. Paiement échoué chez MonCash -> pas de PAID
# ===========================================================================
@pytest.mark.django_db
def test_scenario_4_failed_payment_at_moncash_does_not_become_paid(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.provider_payment_id = "token-fail-1"
    payment.save(update_fields=("provider_payment_id",))

    authenticate_as(api_client, owner)

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-valid", "expires_in": 3600}

    mock_retrieve = MagicMock(status_code=200)
    mock_retrieve.json.return_value = {
        "status": 200,
        "message": "failed",
        "payment": {
            "reference": "REF-FAIL",
            "transaction_id": "TXN-FAIL",
            "cost": 500,
            "message": "failed",
        },
    }

    with patch("requests.post", side_effect=[mock_oauth, mock_retrieve]):
        response = api_client.post(f"/api/properties/{prop.pk}/verify-publication-payment/")

    assert response.status_code == 200
    assert response.data["status"] == "FAILED"
    assert response.data["paid_at"] is None

    payment.refresh_from_db()
    assert payment.status == Payment.Status.FAILED
    assert payment.paid_at is None

    # Invoice becomes VOID
    invoice = Invoice.objects.get(payment=payment)
    assert invoice.status == Invoice.Status.VOID


# ===========================================================================
# 5. Erreur OAuth -> réponse propre HTTP 502
# ===========================================================================
@pytest.mark.django_db
def test_scenario_5_oauth_failure_returns_clean_502(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )

    authenticate_as(api_client, owner)

    mock_oauth = MagicMock(status_code=401)
    mock_oauth.json.return_value = {"error": "invalid_client"}

    with patch("requests.post", return_value=mock_oauth):
        response = api_client.post(f"/api/properties/{prop.pk}/verify-publication-payment/")

    assert response.status_code == 502
    assert "Impossible de vérifier le paiement" in response.data["detail"]

    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING
    assert payment.paid_at is None


# ===========================================================================
# 6. Timeout MonCash -> réponse propre HTTP 502
# ===========================================================================
@pytest.mark.django_db
def test_scenario_6_timeout_returns_clean_502(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )

    authenticate_as(api_client, owner)

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-valid", "expires_in": 3600}

    with patch("requests.post", side_effect=[mock_oauth, requests.exceptions.Timeout("Timeout error")]):
        response = api_client.post(f"/api/properties/{prop.pk}/verify-publication-payment/")

    assert response.status_code == 502
    assert "Impossible de vérifier le paiement" in response.data["detail"]

    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING


# ===========================================================================
# 7. Erreur HTTP MonCash (500) -> réponse propre HTTP 502
# ===========================================================================
@pytest.mark.django_db
def test_scenario_7_http_500_from_moncash_returns_clean_502(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )

    authenticate_as(api_client, owner)

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-valid", "expires_in": 3600}

    mock_retrieve = MagicMock(status_code=500)
    mock_retrieve.json.return_value = {"error": "Internal Server Error"}

    with patch("requests.post", side_effect=[mock_oauth, mock_retrieve]):
        response = api_client.post(f"/api/properties/{prop.pk}/verify-publication-payment/")

    assert response.status_code == 502
    assert "Impossible de vérifier le paiement" in response.data["detail"]

    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING


# ===========================================================================
# 8. Réponse MonCash invalide (JSON corrompu) -> pas de PAID
# ===========================================================================
@pytest.mark.django_db
def test_scenario_8_invalid_json_returns_502_not_paid(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )

    authenticate_as(api_client, owner)

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-valid", "expires_in": 3600}

    mock_retrieve = MagicMock(status_code=200)
    mock_retrieve.json.side_effect = ValueError("Invalid JSON format")

    with patch("requests.post", side_effect=[mock_oauth, mock_retrieve]):
        response = api_client.post(f"/api/properties/{prop.pk}/verify-publication-payment/")

    assert response.status_code == 502

    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING
    assert payment.paid_at is None


# ===========================================================================
# 9. Utilisateur non authentifié -> 401
# ===========================================================================
@pytest.mark.django_db
def test_scenario_9_unauthenticated_user_rejected_401(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )

    response = api_client.post(f"/api/properties/{prop.pk}/verify-publication-payment/")
    assert response.status_code == 401


# ===========================================================================
# 10. Autre propriétaire -> 403 ou 404
# ===========================================================================
@pytest.mark.django_db
def test_scenario_10_other_owner_rejected(test_setup, api_client):
    pt, loc, owner, other_owner, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )

    authenticate_as(api_client, other_owner)
    response = api_client.post(f"/api/properties/{prop.pk}/verify-publication-payment/")
    assert response.status_code in (403, 404)


# ===========================================================================
# 11. ADMIN peut vérifier
# ===========================================================================
@pytest.mark.django_db
def test_scenario_11_admin_can_verify(test_setup, api_client):
    pt, loc, owner, _, _, admin = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.provider_payment_id = "token-admin-1"
    payment.save(update_fields=("provider_payment_id",))

    authenticate_as(api_client, admin)

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-admin-oauth", "expires_in": 3600}

    mock_retrieve = MagicMock(status_code=200)
    mock_retrieve.json.return_value = {
        "status": 200,
        "message": "successful",
        "payment": {
            "reference": "REF-ADMIN",
            "transaction_id": "TXN-ADMIN-001",
            "cost": 500,
            "message": "successful",
            "date": "2026-10-03 17:00:00",
        },
    }

    with patch("requests.post", side_effect=[mock_oauth, mock_retrieve]):
        response = api_client.post(f"/api/properties/{prop.pk}/verify-publication-payment/")

    assert response.status_code == 200
    assert response.data["status"] == "PAID"
    assert response.data["provider_transaction_id"] == "TXN-ADMIN-001"


# ===========================================================================
# 12. Propriété sans paiement -> erreur propre HTTP 404
# ===========================================================================
@pytest.mark.django_db
def test_scenario_12_property_without_payment_returns_clean_404(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)

    authenticate_as(api_client, owner)
    response = api_client.post(f"/api/properties/{prop.pk}/verify-publication-payment/")

    assert response.status_code == 404
    assert "Aucun paiement de publication trouvé" in response.data["detail"]


# ===========================================================================
# 13. Plusieurs appels simultanés ne créent pas de double confirmation
# ===========================================================================
@pytest.mark.django_db
def test_scenario_13_repeated_confirmations_do_not_duplicate(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.provider_payment_id = "token-multi-1"
    payment.save(update_fields=("provider_payment_id",))

    authenticate_as(api_client, owner)

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-valid", "expires_in": 3600}

    mock_retrieve = MagicMock(status_code=200)
    mock_retrieve.json.return_value = {
        "status": 200,
        "message": "successful",
        "payment": {
            "reference": "REF-MULTI",
            "transaction_id": "TXN-MULTI-123",
            "cost": 500,
            "message": "successful",
            "date": "2026-10-03 17:10:00",
        },
    }

    # First call: confirms payment
    with patch("requests.post", side_effect=[mock_oauth, mock_retrieve]):
        res1 = api_client.post(f"/api/properties/{prop.pk}/verify-publication-payment/")

    assert res1.status_code == 200
    assert res1.data["status"] == "PAID"
    first_paid_at = res1.data["paid_at"]

    # Second call: immediately returns PAID without network call
    with patch("requests.post") as mock_second_call:
        res2 = api_client.post(f"/api/properties/{prop.pk}/verify-publication-payment/")

    assert res2.status_code == 200
    assert res2.data["status"] == "PAID"
    assert res2.data["paid_at"] == first_paid_at
    assert mock_second_call.call_count == 0

    # Ensure only 1 payment and 1 invoice exist
    assert Payment.objects.filter(property=prop).count() == 1
    assert Invoice.objects.filter(payment=payment).count() == 1


# ===========================================================================
# 14. paid_at est défini une seule fois
# ===========================================================================
@pytest.mark.django_db
def test_scenario_14_paid_at_defined_only_once(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.provider_payment_id = "token-once-1"
    payment.save(update_fields=("provider_payment_id",))

    authenticate_as(api_client, owner)

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-valid", "expires_in": 3600}

    mock_retrieve = MagicMock(status_code=200)
    mock_retrieve.json.return_value = {
        "status": 200,
        "message": "successful",
        "payment": {
            "reference": "REF-ONCE",
            "transaction_id": "TXN-ONCE-999",
            "cost": 500,
            "message": "successful",
            "date": "2026-10-03 17:15:00",
        },
    }

    with patch("requests.post", side_effect=[mock_oauth, mock_retrieve]):
        res1 = api_client.post(f"/api/properties/{prop.pk}/verify-publication-payment/")

    assert res1.status_code == 200
    payment.refresh_from_db()
    initial_paid_at = payment.paid_at
    assert initial_paid_at is not None

    # Subsequent confirmation call
    res2 = api_client.post(f"/api/properties/{prop.pk}/verify-publication-payment/")
    assert res2.status_code == 200
    payment.refresh_from_db()
    assert payment.paid_at == initial_paid_at


# ===========================================================================
# 15. provider_transaction_id est correctement enregistré si disponible
# ===========================================================================
@pytest.mark.django_db
def test_scenario_15_provider_transaction_id_recorded_properly(test_setup, api_client):
    pt, loc, owner, _, _, _ = test_setup
    prop = create_prop(owner, pt, loc, status=Property.Status.DRAFT)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.provider_payment_id = "token-txn-test"
    payment.save(update_fields=("provider_payment_id",))

    authenticate_as(api_client, owner)

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-valid", "expires_in": 3600}

    mock_retrieve = MagicMock(status_code=200)
    mock_retrieve.json.return_value = {
        "status": 200,
        "message": "successful",
        "payment": {
            "reference": "REF-RECORD-001",
            "transaction_id": "OFFICIAL-MONCASH-TRANSACTION-777",
            "cost": 500,
            "message": "successful",
            "date": "2026-10-03 17:20:00",
        },
    }

    with patch("requests.post", side_effect=[mock_oauth, mock_retrieve]):
        response = api_client.post(f"/api/properties/{prop.pk}/verify-publication-payment/")

    assert response.status_code == 200
    assert response.data["provider_transaction_id"] == "OFFICIAL-MONCASH-TRANSACTION-777"

    payment.refresh_from_db()
    assert payment.provider_transaction_id == "OFFICIAL-MONCASH-TRANSACTION-777"
