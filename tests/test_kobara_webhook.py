import json
import time
import uuid
from decimal import Decimal
import hmac
import hashlib

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.locations.models import Location
from apps.monetization.models import Invoice, Payment
from apps.monetization.services import create_property_publication_payment
from apps.properties.models import Property, PropertyType

User = get_user_model()
PASSWORD = "Secure-Password-521!"
TEST_WEBHOOK_SECRET = "kbr_whsec_test_secret_key_12345"


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def kobara_test_settings(settings):
    settings.DEFAULT_PAYMENT_PROVIDER = "KOBARA"
    settings.KOBARA_SECRET_KEY = "kbr_sk_test_mock_dummy_secret"
    settings.KOBARA_WEBHOOK_SECRET = TEST_WEBHOOK_SECRET
    settings.KOBARA_PROVIDER = "kobara"
    settings.KOBARA_BASE_URL = "https://api.kobara.app/v1"
    settings.FRONTEND_URL = "http://localhost:3000"
    settings.PUBLICATION_PRICE_HTG = "500.00"


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def test_setup(db):
    property_type = PropertyType.objects.create(name="Maison", slug=f"maison-{uuid.uuid4().hex[:6]}")
    location = Location.objects.create(name="Delmas", slug=f"delmas-{uuid.uuid4().hex[:6]}", type="CITY")

    owner = User.objects.create_user(
        email=f"owner_{uuid.uuid4().hex[:6]}@example.com",
        first_name="Jean",
        last_name="Pierre",
        phone="50937000000",
        password=PASSWORD,
        role=User.Role.OWNER,
    )
    return property_type, location, owner


def create_test_property(owner, property_type, location, **overrides):
    suffix = uuid.uuid4().hex[:8]
    data = {
        "owner": owner,
        "property_type": property_type,
        "location": location,
        "title": f"Maison Test {suffix}",
        "slug": f"prop-{suffix}",
        "description": "Une superbe propriété pour test webhook",
        "price": Decimal("100000.00"),
        "currency": "USD",
        "listing_type": Property.ListingType.SALE,
        "status": Property.Status.DRAFT,
    }
    data.update(overrides)
    return Property.objects.create(**data)


def build_signature_header(payload_str: str, secret: str = TEST_WEBHOOK_SECRET, timestamp: int | None = None) -> str:
    """Helper to generate a valid Kobara-Signature header."""
    ts = timestamp if timestamp is not None else int(time.time())
    signature = hmac.new(
        secret.encode("utf-8"),
        f"{ts}.{payload_str}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"t={ts},v1={signature}"


def build_kobara_payload(
    order_id: str,
    amount: str = "500.00",
    currency: str = "HTG",
    event_type: str = "payment.succeeded",
    payment_id: str = "pay_kobara_live_123",
    transaction_id: str = "tx_kobara_ref_456",
) -> tuple[dict, str]:
    """Helper to generate standard Kobara webhook payload dictionary and serialized JSON."""
    payload = {
        "id": f"evt_{uuid.uuid4().hex[:12]}",
        "event_type": event_type,
        "created_at": int(time.time()),
        "data": {
            "id": payment_id,
            "amount": amount,
            "currency": currency,
            "status": "succeeded",
            "transaction_id": transaction_id,
            "metadata": {
                "order_id": order_id,
            },
        },
    }
    return payload, json.dumps(payload)


# ==============================================================================
# 1. SÉCURITÉ & SIGNATURES
# ==============================================================================

@pytest.mark.django_db
def test_valid_signature_accepted(api_client, test_setup):
    """1. Signature valide -> acceptée avec HTTP 200."""
    pt, loc, owner = test_setup
    prop = create_test_property(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="KOBARA", amount=Decimal("500.00"), currency="HTG"
    )

    _, raw_payload = build_kobara_payload(order_id=payment.order_id)
    sig_header = build_signature_header(raw_payload)

    response = api_client.post(
        "/api/webhooks/kobara/",
        data=raw_payload,
        content_type="application/json",
        HTTP_KOBARA_SIGNATURE=sig_header,
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data.get("status") == "PAID"
    assert response.data.get("order_id") == payment.order_id


@pytest.mark.django_db
def test_invalid_signature_rejected(api_client, test_setup):
    """2. Signature invalide (tampered HMAC) -> rejetée avec HTTP 400."""
    pt, loc, owner = test_setup
    prop = create_test_property(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="KOBARA", amount=Decimal("500.00"), currency="HTG"
    )

    _, raw_payload = build_kobara_payload(order_id=payment.order_id)
    bad_sig_header = f"t={int(time.time())},v1={'0' * 64}"

    response = api_client.post(
        "/api/webhooks/kobara/",
        data=raw_payload,
        content_type="application/json",
        HTTP_KOBARA_SIGNATURE=bad_sig_header,
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "invalide" in response.data.get("detail", "").lower()
    # Le paiement ne doit pas être modifié
    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING


@pytest.mark.django_db
def test_missing_signature_rejected(api_client, test_setup):
    """3. Signature absente -> rejetée avec HTTP 400."""
    pt, loc, owner = test_setup
    prop = create_test_property(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="KOBARA", amount=Decimal("500.00"), currency="HTG"
    )

    _, raw_payload = build_kobara_payload(order_id=payment.order_id)

    response = api_client.post(
        "/api/webhooks/kobara/",
        data=raw_payload,
        content_type="application/json",
        # No HTTP_KOBARA_SIGNATURE
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "manquant" in response.data.get("detail", "").lower()
    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING


@pytest.mark.django_db
def test_replay_expired_timestamp_rejected(api_client, test_setup):
    """4. Replay / timestamp expiré (> 300s) -> rejeté avec HTTP 400."""
    pt, loc, owner = test_setup
    prop = create_test_property(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="KOBARA", amount=Decimal("500.00"), currency="HTG"
    )

    _, raw_payload = build_kobara_payload(order_id=payment.order_id)
    expired_ts = int(time.time()) - 400  # 400s > tolerance (300s)
    expired_sig = build_signature_header(raw_payload, timestamp=expired_ts)

    response = api_client.post(
        "/api/webhooks/kobara/",
        data=raw_payload,
        content_type="application/json",
        HTTP_KOBARA_SIGNATURE=expired_sig,
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "expirée" in response.data.get("detail", "").lower() or "invalide" in response.data.get("detail", "").lower()
    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING


@pytest.mark.django_db
def test_missing_webhook_secret_returns_500(api_client, test_setup, settings):
    """Configuration serveur incomplète (KOBARA_WEBHOOK_SECRET vide) -> HTTP 500."""
    settings.KOBARA_WEBHOOK_SECRET = ""
    pt, loc, owner = test_setup
    prop = create_test_property(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="KOBARA", amount=Decimal("500.00"), currency="HTG"
    )

    _, raw_payload = build_kobara_payload(order_id=payment.order_id)
    sig_header = build_signature_header(raw_payload)

    response = api_client.post(
        "/api/webhooks/kobara/",
        data=raw_payload,
        content_type="application/json",
        HTTP_KOBARA_SIGNATURE=sig_header,
    )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "incomplète" in response.data.get("detail", "").lower()


# ==============================================================================
# 2. SUCCÈS & TRANSITIONS ATOMIQUES
# ==============================================================================

@pytest.mark.django_db
def test_payment_succeeded_full_lifecycle(api_client, test_setup):
    """5-9. payment.succeeded: Payment devient PAID, Invoice PAID, paid_at et IDs renseignés."""
    pt, loc, owner = test_setup
    prop = create_test_property(owner, pt, loc, status=Property.Status.DRAFT)
    payment = create_property_publication_payment(
        property_obj=prop, provider="KOBARA", amount=Decimal("500.00"), currency="HTG"
    )
    invoice = Invoice.objects.get(payment=payment)

    assert payment.status == Payment.Status.PENDING
    assert payment.paid_at is None
    assert invoice.status == Invoice.Status.ISSUED
    assert invoice.paid_at is None

    provider_pay_id = "pay_live_kobara_999"
    provider_tx_id = "tx_ref_moncash_888"

    _, raw_payload = build_kobara_payload(
        order_id=payment.order_id,
        amount="500.00",
        currency="HTG",
        event_type="payment.succeeded",
        payment_id=provider_pay_id,
        transaction_id=provider_tx_id,
    )
    sig_header = build_signature_header(raw_payload)

    response = api_client.post(
        "/api/webhooks/kobara/",
        data=raw_payload,
        content_type="application/json",
        HTTP_KOBARA_SIGNATURE=sig_header,
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["status"] == "PAID"
    assert response.data["order_id"] == payment.order_id

    # 5. Payment devient PAID
    payment.refresh_from_db()
    assert payment.status == Payment.Status.PAID

    # 6. Invoice devient PAID
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID

    # 7. paid_at est renseigné sur les deux
    assert payment.paid_at is not None
    assert invoice.paid_at is not None
    assert invoice.paid_at == payment.paid_at

    # 8. provider_payment_id enregistré
    assert payment.provider_payment_id == provider_pay_id

    # 9. provider_transaction_id enregistré
    assert payment.provider_transaction_id == provider_tx_id

    # RÈGLE MÉTIER CRITIQUE : La propriété doit RESTER DRAFT (pas d'auto-publication !)
    prop.refresh_from_db()
    assert prop.status == Property.Status.DRAFT
    assert prop.published_at is None


# ==============================================================================
# 3. COHÉRENCE & INTÉGRITÉ DES DONNÉES
# ==============================================================================

@pytest.mark.django_db
def test_amount_mismatch_rejected(api_client, test_setup):
    """10. Montant incohérent (tampering) -> rejeté avec HTTP 400, pas de PAID."""
    pt, loc, owner = test_setup
    prop = create_test_property(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="KOBARA", amount=Decimal("500.00"), currency="HTG"
    )

    # Payload altéré avec 100.00 au lieu de 500.00
    _, raw_payload = build_kobara_payload(order_id=payment.order_id, amount="100.00")
    sig_header = build_signature_header(raw_payload)

    response = api_client.post(
        "/api/webhooks/kobara/",
        data=raw_payload,
        content_type="application/json",
        HTTP_KOBARA_SIGNATURE=sig_header,
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "montant" in response.data.get("detail", "").lower()

    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING
    assert payment.paid_at is None


@pytest.mark.django_db
def test_currency_mismatch_rejected(api_client, test_setup):
    """11. Devise incohérente (USD au lieu de HTG) -> rejeté avec HTTP 400, pas de PAID."""
    pt, loc, owner = test_setup
    prop = create_test_property(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="KOBARA", amount=Decimal("500.00"), currency="HTG"
    )

    _, raw_payload = build_kobara_payload(order_id=payment.order_id, currency="USD")
    sig_header = build_signature_header(raw_payload)

    response = api_client.post(
        "/api/webhooks/kobara/",
        data=raw_payload,
        content_type="application/json",
        HTTP_KOBARA_SIGNATURE=sig_header,
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "devise" in response.data.get("detail", "").lower()

    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING


@pytest.mark.django_db
def test_provider_mismatch_rejected(api_client, test_setup):
    """12. Paiement MonCash recevant un webhook Kobara -> rejeté avec HTTP 400."""
    pt, loc, owner = test_setup
    prop = create_test_property(owner, pt, loc)
    # Création explicite avec provider="MONCASH"
    payment = create_property_publication_payment(
        property_obj=prop, provider="MONCASH", amount=Decimal("500.00"), currency="HTG"
    )

    _, raw_payload = build_kobara_payload(order_id=payment.order_id)
    sig_header = build_signature_header(raw_payload)

    response = api_client.post(
        "/api/webhooks/kobara/",
        data=raw_payload,
        content_type="application/json",
        HTTP_KOBARA_SIGNATURE=sig_header,
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "fournisseur" in response.data.get("detail", "").lower()

    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING


@pytest.mark.django_db
def test_nonexistent_order_id_returns_404(api_client, test_setup):
    """13. order_id inexistant dans la base locale -> HTTP 404 sécurisé sans création sauvage."""
    unknown_order_id = f"unknown-order-{uuid.uuid4().hex[:8]}"
    _, raw_payload = build_kobara_payload(order_id=unknown_order_id)
    sig_header = build_signature_header(raw_payload)

    initial_payment_count = Payment.objects.count()

    response = api_client.post(
        "/api/webhooks/kobara/",
        data=raw_payload,
        content_type="application/json",
        HTTP_KOBARA_SIGNATURE=sig_header,
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "introuvable" in response.data.get("detail", "").lower() or "aucun paiement" in response.data.get("detail", "").lower()
    # Aucun paiement sauvage créé
    assert Payment.objects.count() == initial_payment_count


# ==============================================================================
# 4. IDEMPOTENCE
# ==============================================================================

@pytest.mark.django_db
def test_idempotence_duplicate_webhook(api_client, test_setup):
    """14-15. Même webhook reçu deux fois -> un seul traitement, aucun effet secondaire supplémentaire."""
    pt, loc, owner = test_setup
    prop = create_test_property(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="KOBARA", amount=Decimal("500.00"), currency="HTG"
    )

    _, raw_payload = build_kobara_payload(order_id=payment.order_id)
    sig_header = build_signature_header(raw_payload)

    # 1er webhook
    response1 = api_client.post(
        "/api/webhooks/kobara/",
        data=raw_payload,
        content_type="application/json",
        HTTP_KOBARA_SIGNATURE=sig_header,
    )
    assert response1.status_code == status.HTTP_200_OK

    payment.refresh_from_db()
    initial_paid_at = payment.paid_at
    invoice_count_1 = Invoice.objects.filter(payment=payment).count()
    payment_count_1 = Payment.objects.filter(order_id=payment.order_id).count()

    # 2ème webhook identique
    response2 = api_client.post(
        "/api/webhooks/kobara/",
        data=raw_payload,
        content_type="application/json",
        HTTP_KOBARA_SIGNATURE=sig_header,
    )
    assert response2.status_code == status.HTTP_200_OK
    assert "déjà confirmé" in response2.data.get("detail", "").lower()

    payment.refresh_from_db()
    # paid_at ne doit pas être altéré
    assert payment.paid_at == initial_paid_at
    # Pas de doublon de paiement ni de facture
    assert Payment.objects.filter(order_id=payment.order_id).count() == payment_count_1
    assert Invoice.objects.filter(payment=payment).count() == invoice_count_1


# ==============================================================================
# 5. GESTION DES ÉVÉNEMENTS NON CONCERNÉS
# ==============================================================================

@pytest.mark.django_db
def test_unknown_or_unhandled_event_safely_ignored(api_client, test_setup):
    """16. Événement inconnu (ex: refund.created, payout.succeeded) -> 200 OK sans altération."""
    pt, loc, owner = test_setup
    prop = create_test_property(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="KOBARA", amount=Decimal("500.00"), currency="HTG"
    )

    _, raw_payload = build_kobara_payload(
        order_id=payment.order_id,
        event_type="charge.refunded",
    )
    sig_header = build_signature_header(raw_payload)

    response = api_client.post(
        "/api/webhooks/kobara/",
        data=raw_payload,
        content_type="application/json",
        HTTP_KOBARA_SIGNATURE=sig_header,
    )

    assert response.status_code == status.HTTP_200_OK
    assert "ignoré" in response.data.get("detail", "").lower()

    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING
    assert payment.paid_at is None


@pytest.mark.django_db
def test_fallback_lookup_by_payment_id(api_client, test_setup):
    """Paiement retrouvé par fallback provider_payment_id lorsque order_id absent des métadonnées."""
    pt, loc, owner = test_setup
    prop = create_test_property(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="KOBARA", amount=Decimal("500.00"), currency="HTG"
    )
    custom_provider_id = f"pay_custom_{uuid.uuid4().hex[:8]}"
    payment.provider_payment_id = custom_provider_id
    payment.save(update_fields=("provider_payment_id",))

    payload = {
        "id": f"evt_{uuid.uuid4().hex[:8]}",
        "event_type": "payment.succeeded",
        "data": {
            "id": custom_provider_id,
            "amount": "500.00",
            "currency": "HTG",
            "status": "succeeded",
            "metadata": {},  # No order_id in metadata
        },
    }
    raw_payload = json.dumps(payload)
    sig_header = build_signature_header(raw_payload)

    response = api_client.post(
        "/api/webhooks/kobara/",
        data=raw_payload,
        content_type="application/json",
        HTTP_KOBARA_SIGNATURE=sig_header,
    )

    assert response.status_code == status.HTTP_200_OK
    payment.refresh_from_db()
    assert payment.status == Payment.Status.PAID
    assert payment.paid_at is not None


@pytest.mark.django_db
def test_payment_already_paid_returns_200_no_side_effects(api_client, test_setup):
    """15. Payment déjà PAID -> retour 200 immédiat, aucun effet de bord, paid_at inchangé."""
    pt, loc, owner = test_setup
    prop = create_test_property(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="KOBARA", amount=Decimal("500.00"), currency="HTG"
    )
    initial_paid_at = timezone.now()
    payment.status = Payment.Status.PAID
    payment.paid_at = initial_paid_at
    payment.save(update_fields=("status", "paid_at"))

    invoice = Invoice.objects.get(payment=payment)
    invoice.status = Invoice.Status.PAID
    invoice.paid_at = initial_paid_at
    invoice.save(update_fields=("status", "paid_at"))

    _, raw_payload = build_kobara_payload(order_id=payment.order_id)
    sig_header = build_signature_header(raw_payload)

    response = api_client.post(
        "/api/webhooks/kobara/",
        data=raw_payload,
        content_type="application/json",
        HTTP_KOBARA_SIGNATURE=sig_header,
    )

    assert response.status_code == status.HTTP_200_OK
    assert "déjà confirmé" in response.data.get("detail", "").lower()

    payment.refresh_from_db()
    assert payment.status == Payment.Status.PAID
    assert payment.paid_at == initial_paid_at
    assert Invoice.objects.filter(payment=payment).count() == 1


@pytest.mark.django_db
def test_malformed_signature_format_rejected(api_client, test_setup):
    """Format d'en-tête de signature malformé (pas de t= ou v1=) -> 400."""
    pt, loc, owner = test_setup
    prop = create_test_property(owner, pt, loc)
    payment = create_property_publication_payment(
        property_obj=prop, provider="KOBARA", amount=Decimal("500.00"), currency="HTG"
    )

    _, raw_payload = build_kobara_payload(order_id=payment.order_id)

    response = api_client.post(
        "/api/webhooks/kobara/",
        data=raw_payload,
        content_type="application/json",
        HTTP_KOBARA_SIGNATURE="not-a-valid-header-format",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "invalide" in response.data.get("detail", "").lower()


@pytest.mark.django_db
def test_malformed_json_body_rejected(api_client, test_setup):
    """Corps brut non parseable en JSON -> 400."""
    raw_payload = "{invalid_json: true"
    sig_header = build_signature_header(raw_payload)

    response = api_client.post(
        "/api/webhooks/kobara/",
        data=raw_payload,
        content_type="application/json",
        HTTP_KOBARA_SIGNATURE=sig_header,
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST

