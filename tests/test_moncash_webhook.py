import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient
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
    property_type = PropertyType.objects.create(name="Appartement", slug="appartement")
    location = Location.objects.create(name="Petion-Ville", slug="petion-ville", type="CITY")

    owner = User.objects.create_user(
        email="webhook_owner@example.com", password=PASSWORD, role=User.Role.OWNER
    )
    admin = User.objects.create_user(
        email="webhook_admin@example.com", password=PASSWORD, role=User.Role.ADMIN
    )
    return property_type, location, owner, admin


def create_prop(owner, property_type, location, **overrides):
    import uuid
    suffix = uuid.uuid4().hex[:6]
    data = {
        "owner": owner,
        "title": f"Propriété {suffix}",
        "slug": f"propriete-{suffix}",
        "description": "Propriété pour test webhook MonCash.",
        "property_type": property_type,
        "listing_type": Property.ListingType.RENT,
        "price": Decimal("1500.00"),
        "currency": "USD",
        "bedrooms": 3,
        "bathrooms": Decimal("2"),
        "parking_spaces": 1,
        "area": Decimal("120.00"),
        "area_unit": Property.AreaUnit.SQM,
        "furnished": False,
        "location": location,
        "status": Property.Status.DRAFT,
    }
    data.update(overrides)
    return Property.objects.create(**data)


# ===========================================================================
# 1. Endpoint public accessible sans authentification (POST & GET)
# ===========================================================================
@pytest.mark.django_db
def test_webhook_accessible_publicly_without_jwt(test_setup, api_client):
    pt, loc, owner, _ = test_setup
    prop = create_prop(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.provider_payment_id = "token-webhook-1"
    payment.save(update_fields=("provider_payment_id",))

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-oauth", "expires_in": 3600}
    mock_retrieve = MagicMock(status_code=200)
    mock_retrieve.json.return_value = {
        "status": 200,
        "message": "successful",
        "payment": {
            "reference": "REF-WH-1",
            "transaction_id": "TXN-WH-001",
            "cost": 500,
            "message": "successful",
            "date": "2026-10-05 12:00:00",
        },
    }

    # Calling without any Authorization header
    with patch("requests.post", side_effect=[mock_oauth, mock_retrieve]):
        response = api_client.post("/api/moncash/webhook/", {"orderId": payment.order_id}, format="json")

    assert response.status_code == 200
    assert response.data["status"] == "PAID"
    assert response.data["order_id"] == payment.order_id


# ===========================================================================
# 2. Notification valide via POST orderId & confirmation MonCash -> PAID
# ===========================================================================
@pytest.mark.django_db
def test_webhook_post_order_id_confirmed_paid(test_setup, api_client):
    pt, loc, owner, _ = test_setup
    prop = create_prop(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.provider_payment_id = "token-wh-paid"
    payment.save(update_fields=("provider_payment_id",))

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-oauth", "expires_in": 3600}
    mock_retrieve = MagicMock(status_code=200)
    mock_retrieve.json.return_value = {
        "status": 200,
        "message": "successful",
        "payment": {
            "reference": "REF-WH-PAID",
            "transaction_id": "TXN-PAID-999",
            "cost": 500,
            "message": "successful",
            "date": "2026-10-05 12:10:00",
        },
    }

    with patch("requests.post", side_effect=[mock_oauth, mock_retrieve]):
        response = api_client.post("/api/moncash/webhook/", {"orderId": payment.order_id}, format="json")

    assert response.status_code == 200
    assert response.data["status"] == "PAID"
    assert response.data["payment_id"] == payment.pk

    payment.refresh_from_db()
    assert payment.status == Payment.Status.PAID
    assert payment.provider_transaction_id == "TXN-PAID-999"
    assert payment.paid_at is not None

    # Invoice synced
    invoice = Invoice.objects.get(payment=payment)
    assert invoice.status == Invoice.Status.PAID
    assert invoice.paid_at == payment.paid_at

    # Property remains DRAFT
    prop.refresh_from_db()
    assert prop.status == Property.Status.DRAFT


# ===========================================================================
# 3. Notification valide via snake_case order_id
# ===========================================================================
@pytest.mark.django_db
def test_webhook_post_snake_case_order_id(test_setup, api_client):
    pt, loc, owner, _ = test_setup
    prop = create_prop(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.provider_payment_id = "token-snake"
    payment.save(update_fields=("provider_payment_id",))

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-oauth", "expires_in": 3600}
    mock_retrieve = MagicMock(status_code=200)
    mock_retrieve.json.return_value = {
        "status": 200,
        "message": "successful",
        "payment": {
            "reference": "REF-SNAKE",
            "transaction_id": "TXN-SNAKE-123",
            "cost": 500,
            "message": "successful",
            "date": "2026-10-05 12:15:00",
        },
    }

    with patch("requests.post", side_effect=[mock_oauth, mock_retrieve]):
        response = api_client.post("/api/moncash/webhook/", {"order_id": payment.order_id}, format="json")

    assert response.status_code == 200
    assert response.data["status"] == "PAID"


# ===========================================================================
# 4. Support GET via query parameters (Alert URL / Return URL)
# ===========================================================================
@pytest.mark.django_db
def test_webhook_get_alert_url(test_setup, api_client):
    pt, loc, owner, _ = test_setup
    prop = create_prop(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.provider_payment_id = "token-get-alert"
    payment.save(update_fields=("provider_payment_id",))

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-oauth", "expires_in": 3600}
    mock_retrieve = MagicMock(status_code=200)
    mock_retrieve.json.return_value = {
        "status": 200,
        "message": "successful",
        "payment": {
            "reference": "REF-GET",
            "transaction_id": "TXN-GET-456",
            "cost": 500,
            "message": "successful",
            "date": "2026-10-05 12:20:00",
        },
    }

    with patch("requests.post", side_effect=[mock_oauth, mock_retrieve]):
        response = api_client.get(f"/api/moncash/alert/?orderId={payment.order_id}")

    assert response.status_code == 200
    assert response.data["status"] == "PAID"
    assert response.data["order_id"] == payment.order_id


# ===========================================================================
# 5. Notification avec transactionId au lieu de orderId
# ===========================================================================
@pytest.mark.django_db
def test_webhook_find_by_transaction_id(test_setup, api_client):
    pt, loc, owner, _ = test_setup
    prop = create_prop(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.provider_payment_id = "token-by-tx"
    payment.provider_transaction_id = "TXN-KNOWN-888"
    payment.save(update_fields=("provider_payment_id", "provider_transaction_id"))

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-oauth", "expires_in": 3600}
    mock_retrieve = MagicMock(status_code=200)
    mock_retrieve.json.return_value = {
        "status": 200,
        "message": "successful",
        "payment": {
            "reference": "REF-TX",
            "transaction_id": "TXN-KNOWN-888",
            "cost": 500,
            "message": "successful",
            "date": "2026-10-05 12:25:00",
        },
    }

    with patch("requests.post", side_effect=[mock_oauth, mock_retrieve]):
        response = api_client.post("/api/moncash/webhook/", {"transactionId": "TXN-KNOWN-888"}, format="json")

    assert response.status_code == 200
    assert response.data["status"] == "PAID"


# ===========================================================================
# 6. Requête sans identifiant -> 400 Bad Request
# ===========================================================================
@pytest.mark.django_db
def test_webhook_missing_identifiers_returns_400(api_client):
    response = api_client.post("/api/moncash/webhook/", {}, format="json")
    assert response.status_code == 400
    assert "manquant" in response.data["detail"]

    response_get = api_client.get("/api/moncash/webhook/")
    assert response_get.status_code == 400
    assert "manquant" in response_get.data["detail"]


# ===========================================================================
# 7. Paiement introuvable -> 404 Not Found
# ===========================================================================
@pytest.mark.django_db
def test_webhook_payment_not_found_returns_404(api_client):
    response = api_client.post("/api/moncash/webhook/", {"orderId": "UNKNOWN-ORDER-999"}, format="json")
    assert response.status_code == 404
    assert "Aucun paiement" in response.data["detail"]


# ===========================================================================
# 8. Ne jamais faire confiance au payload client/webhook : vérification MonCash
# ===========================================================================
@pytest.mark.django_db
def test_webhook_never_trusts_payload_status(test_setup, api_client):
    """Même si le payload prétend status='PAID', le serveur interroge MonCash."""
    pt, loc, owner, _ = test_setup
    prop = create_prop(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.provider_payment_id = "token-fraud-test"
    payment.save(update_fields=("provider_payment_id",))

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-oauth", "expires_in": 3600}
    # MonCash says Order Not Found / 404 (or still pending)
    mock_retrieve = MagicMock(status_code=404)
    mock_retrieve.json.return_value = {"status": 404, "message": "Order Not Found"}

    with patch("requests.post", side_effect=[mock_oauth, mock_retrieve]):
        # Attacker posts {"orderId": ..., "status": "PAID", "amount": 0}
        response = api_client.post(
            "/api/moncash/webhook/",
            {"orderId": payment.order_id, "status": "PAID", "amount": 0},
            format="json",
        )

    assert response.status_code == 200
    assert response.data["status"] == "PENDING"

    # Payment in database MUST remain PENDING
    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING
    assert payment.paid_at is None


# ===========================================================================
# 9. Statut FAILED chez MonCash -> FAILED & Invoice VOID
# ===========================================================================
@pytest.mark.django_db
def test_webhook_failed_status(test_setup, api_client):
    pt, loc, owner, _ = test_setup
    prop = create_prop(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.provider_payment_id = "token-wh-fail"
    payment.save(update_fields=("provider_payment_id",))

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-oauth", "expires_in": 3600}
    mock_retrieve = MagicMock(status_code=200)
    mock_retrieve.json.return_value = {
        "status": 200,
        "message": "failed",
        "payment": {
            "reference": "REF-FAIL-WH",
            "transaction_id": "TXN-FAIL-001",
            "cost": 500,
            "message": "failed",
        },
    }

    with patch("requests.post", side_effect=[mock_oauth, mock_retrieve]):
        response = api_client.post("/api/moncash/webhook/", {"orderId": payment.order_id}, format="json")

    assert response.status_code == 200
    assert response.data["status"] == "FAILED"

    payment.refresh_from_db()
    assert payment.status == Payment.Status.FAILED
    assert payment.paid_at is None

    invoice = Invoice.objects.get(payment=payment)
    assert invoice.status == Invoice.Status.VOID


# ===========================================================================
# 10. Statut PENDING chez MonCash -> reste PENDING
# ===========================================================================
@pytest.mark.django_db
def test_webhook_pending_status(test_setup, api_client):
    pt, loc, owner, _ = test_setup
    prop = create_prop(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.provider_payment_id = "token-wh-pending"
    payment.save(update_fields=("provider_payment_id",))

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-oauth", "expires_in": 3600}
    mock_retrieve = MagicMock(status_code=404)
    mock_retrieve.json.return_value = {"status": 404, "message": "Order Not Found"}

    with patch("requests.post", side_effect=[mock_oauth, mock_retrieve]):
        response = api_client.post("/api/moncash/webhook/", {"orderId": payment.order_id}, format="json")

    assert response.status_code == 200
    assert response.data["status"] == "PENDING"

    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING


# ===========================================================================
# 11. Idempotence : si le paiement est déjà PAID, pas d'appel réseau MonCash
# ===========================================================================
@pytest.mark.django_db
def test_webhook_idempotence_already_paid(test_setup, api_client):
    pt, loc, owner, _ = test_setup
    prop = create_prop(owner, pt, loc)
    paid_time = timezone.now()
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.status = Payment.Status.PAID
    payment.paid_at = paid_time
    payment.provider_payment_id = "token-idem"
    payment.provider_transaction_id = "TXN-IDEM-001"
    payment.save()

    with patch("requests.post") as mock_post:
        response = api_client.post("/api/moncash/webhook/", {"orderId": payment.order_id}, format="json")

    assert response.status_code == 200
    assert response.data["status"] == "PAID"
    assert response.data["detail"] == "Paiement déjà confirmé."
    assert mock_post.call_count == 0

    payment.refresh_from_db()
    assert payment.paid_at == paid_time


# ===========================================================================
# 12. Double notification successive (replay)
# ===========================================================================
@pytest.mark.django_db
def test_webhook_double_notification(test_setup, api_client):
    pt, loc, owner, _ = test_setup
    prop = create_prop(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.provider_payment_id = "token-double"
    payment.save(update_fields=("provider_payment_id",))

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-oauth", "expires_in": 3600}
    mock_retrieve = MagicMock(status_code=200)
    mock_retrieve.json.return_value = {
        "status": 200,
        "message": "successful",
        "payment": {
            "reference": "REF-DOUBLE",
            "transaction_id": "TXN-DOUBLE-1",
            "cost": 500,
            "message": "successful",
            "date": "2026-10-05 12:30:00",
        },
    }

    # Notification 1
    with patch("requests.post", side_effect=[mock_oauth, mock_retrieve]):
        res1 = api_client.post("/api/moncash/webhook/", {"orderId": payment.order_id}, format="json")

    assert res1.status_code == 200
    assert res1.data["status"] == "PAID"

    # Notification 2
    with patch("requests.post") as mock_post2:
        res2 = api_client.post("/api/moncash/webhook/", {"orderId": payment.order_id}, format="json")

    assert res2.status_code == 200
    assert res2.data["status"] == "PAID"
    assert mock_post2.call_count == 0

    # Ensure single payment and invoice
    assert Payment.objects.filter(property=prop).count() == 1
    assert Invoice.objects.filter(payment=payment).count() == 1


# ===========================================================================
# 13. Concurrence / convergence avec verify-publication-payment
# ===========================================================================
@pytest.mark.django_db
def test_webhook_and_frontend_verify_convergence(test_setup, api_client):
    from rest_framework_simplejwt.tokens import AccessToken
    pt, loc, owner, _ = test_setup
    prop = create_prop(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.provider_payment_id = "token-converge"
    payment.save(update_fields=("provider_payment_id",))

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-oauth", "expires_in": 3600}
    mock_retrieve = MagicMock(status_code=200)
    mock_retrieve.json.return_value = {
        "status": 200,
        "message": "successful",
        "payment": {
            "reference": "REF-CONV",
            "transaction_id": "TXN-CONV-777",
            "cost": 500,
            "message": "successful",
            "date": "2026-10-05 12:35:00",
        },
    }

    # 1. Webhook confirms first
    with patch("requests.post", side_effect=[mock_oauth, mock_retrieve]):
        res_webhook = api_client.post("/api/moncash/webhook/", {"orderId": payment.order_id}, format="json")
    assert res_webhook.status_code == 200
    assert res_webhook.data["status"] == "PAID"

    # 2. Frontend returns later and calls verify-publication-payment
    token = AccessToken.for_user(owner)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    with patch("requests.post") as mock_post_fe:
        res_fe = api_client.post(f"/api/properties/{prop.pk}/verify-publication-payment/")

    assert res_fe.status_code == 200
    assert res_fe.data["status"] == "PAID"
    assert res_fe.data["provider_transaction_id"] == "TXN-CONV-777"
    assert mock_post_fe.call_count == 0


# ===========================================================================
# 14. Erreur réseau / 500 de MonCash -> 502 Bad Gateway
# ===========================================================================
@pytest.mark.django_db
def test_webhook_moncash_gateway_error_returns_502(test_setup, api_client):
    pt, loc, owner, _ = test_setup
    prop = create_prop(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.provider_payment_id = "token-gw-err"
    payment.save(update_fields=("provider_payment_id",))

    mock_oauth = MagicMock(status_code=500)
    mock_oauth.json.return_value = {"error": "Internal MonCash Error"}

    with patch("requests.post", return_value=mock_oauth):
        response = api_client.post("/api/moncash/webhook/", {"orderId": payment.order_id}, format="json")

    assert response.status_code == 502
    assert "Impossible de joindre la passerelle MonCash" in response.data["detail"]

    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING


# ===========================================================================
# 15. Timeout MonCash -> 502 Bad Gateway
# ===========================================================================
@pytest.mark.django_db
def test_webhook_moncash_timeout_returns_502(test_setup, api_client):
    pt, loc, owner, _ = test_setup
    prop = create_prop(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.provider_payment_id = "token-timeout"
    payment.save(update_fields=("provider_payment_id",))

    with patch("requests.post", side_effect=requests.exceptions.Timeout("Timeout")):
        response = api_client.post("/api/moncash/webhook/", {"orderId": payment.order_id}, format="json")

    assert response.status_code == 502
    assert "Impossible de joindre la passerelle MonCash" in response.data["detail"]

    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING


# ===========================================================================
# 16. Management command sync_pending_moncash_payments
# ===========================================================================
@pytest.mark.django_db
def test_management_command_sync_pending_moncash_payments(test_setup):
    pt, loc, owner, _ = test_setup
    prop = create_prop(owner, pt, loc)
    # Payment created 2 hours ago (within 48h and > 30s)
    created_time = timezone.now() - timezone.timedelta(hours=2)
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )
    payment.provider_payment_id = "token-sync-cmd"
    payment.created_at = created_time
    payment.save(update_fields=("provider_payment_id", "created_at"))

    mock_oauth = MagicMock(status_code=200)
    mock_oauth.json.return_value = {"access_token": "token-oauth", "expires_in": 3600}
    mock_retrieve = MagicMock(status_code=200)
    mock_retrieve.json.return_value = {
        "status": 200,
        "message": "successful",
        "payment": {
            "reference": "REF-SYNC",
            "transaction_id": "TXN-SYNC-999",
            "cost": 500,
            "message": "successful",
            "date": "2026-10-05 12:40:00",
        },
    }

    out = StringIO()
    with patch("requests.post", side_effect=[mock_oauth, mock_retrieve]):
        call_command("sync_pending_moncash_payments", "--max-age-hours=24", "--min-age-seconds=10", stdout=out)

    output = out.getvalue()
    assert "Synchronisation terminée" in output
    assert "Confirmés (PAID) : 1" in output

    payment.refresh_from_db()
    assert payment.status == Payment.Status.PAID
    assert payment.provider_transaction_id == "TXN-SYNC-999"
