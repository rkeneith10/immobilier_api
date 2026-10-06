import logging
from typing import Any

from django.conf import settings
from kobara import Kobara
from kobara.errors import KobaraAPIError as SDKKobaraAPIError, KobaraError as SDKKobaraError

from .models import Payment
from .services import (
    MonetizationError,
    PaymentService,
    ProviderCheckout,
    ProviderVerification,
)

logger = logging.getLogger(__name__)


class KobaraError(MonetizationError):
    """Base error for Kobara operations."""


class KobaraConfigError(KobaraError):
    """Raised when Kobara API key or configuration is missing."""


class KobaraAuthError(KobaraError):
    """Raised when Kobara authentication fails (401/403)."""


class KobaraAPIError(KobaraError):
    """Raised when Kobara API returns an error or invalid response."""


class KobaraNetworkError(KobaraError):
    """Raised when Kobara network connection fails or times out."""


class KobaraService(PaymentService):
    """Kobara provider adapter implementing unified checkout creation and verification."""

    @property
    def provider_name(self) -> str:
        return "KOBARA"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_provider: str | None = None,
        frontend_url: str | None = None,
    ):
        self.api_key = api_key or getattr(settings, "KOBARA_SECRET_KEY", "")
        self.base_url = (base_url or getattr(settings, "KOBARA_BASE_URL", "https://api.kobara.app/v1")).rstrip("/")
        self.default_provider = default_provider or getattr(settings, "KOBARA_PROVIDER", "kobara")
        self.frontend_url = (frontend_url or getattr(settings, "FRONTEND_URL", "http://localhost:3000")).rstrip("/")
        self._client: Kobara | None = None

    def get_client(self) -> Kobara:
        """Initialize and cache the official Kobara SDK client."""
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise KobaraConfigError(
                "La clé secrète Kobara (KOBARA_SECRET_KEY) n'est pas configurée dans l'environnement."
            )
        try:
            self._client = Kobara(api_key=self.api_key, base_url=self.base_url)
        except Exception as exc:
            logger.error("Erreur lors de l'initialisation du client Kobara: %s", type(exc).__name__)
            raise KobaraConfigError("Impossible d'initialiser le client Kobara.") from exc
        return self._client

    def build_customer_payload(self, user) -> dict[str, Any]:
        """Extract customer details safely without sending unnecessary or sensitive data."""
        if not user:
            return {}
        customer: dict[str, Any] = {}
        first_name = (getattr(user, "first_name", "") or "").strip()
        last_name = (getattr(user, "last_name", "") or "").strip()
        name_parts = [p for p in (first_name, last_name) if p]
        if name_parts:
            customer["name"] = " ".join(name_parts)
            if first_name:
                customer["first_name"] = first_name
            if last_name:
                customer["last_name"] = last_name

        email = getattr(user, "email", None)
        if email:
            customer["email"] = str(email).strip()

        phone = getattr(user, "phone", None)
        if phone:
            customer["phone"] = str(phone).strip()

        return customer

    def build_metadata(self, payment: Payment) -> dict[str, str]:
        """Build metadata allowing future webhooks to find our Payment and Property."""
        metadata = {
            "order_id": str(payment.order_id),
            "payment_id": str(payment.pk),
        }
        if payment.property_id:
            metadata["property_id"] = str(payment.property_id)
        if payment.user_id:
            metadata["user_id"] = str(payment.user_id)
        return metadata

    def build_return_urls(self, payment: Payment) -> tuple[str, str]:
        """Generate success_url and cancel_url pointing to existing frontend routes."""
        property_id = str(payment.property_id) if payment.property_id else ""
        if property_id:
            success_url = f"{self.frontend_url}/owner/properties/{property_id}/payment-return"
            cancel_url = f"{self.frontend_url}/owner/properties/{property_id}"
        else:
            success_url = f"{self.frontend_url}/owner/dashboard"
            cancel_url = f"{self.frontend_url}/owner/dashboard"
        return success_url, cancel_url

    def create_provider_checkout(self, payment: Payment) -> ProviderCheckout:
        """Call Kobara payments.create using the official SDK."""
        client = self.get_client()

        # Enforce server-side calculated amount
        amount_val = int(payment.amount) if payment.amount == int(payment.amount) else float(payment.amount)
        success_url, cancel_url = self.build_return_urls(payment)
        customer = self.build_customer_payload(payment.user)
        metadata = self.build_metadata(payment)

        property_desc = f" #{payment.property_id}" if payment.property_id else ""
        description = f"Publication annonce immobiliere{property_desc} - {payment.order_id}"

        payload: dict[str, Any] = {
            "amount": amount_val,
            "currency": payment.currency or "HTG",
            "provider": self.default_provider,
            "description": description,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": metadata,
        }
        if customer:
            payload["customer"] = customer

        # Stable and unique idempotency key based on internal order_id
        idempotency_key = str(payment.order_id)

        try:
            response = client.payments.create(payload, idempotency_key=idempotency_key)
        except SDKKobaraAPIError as exc:
            logger.error("Erreur API Kobara (HTTP %s): %s", exc.status_code, exc)
            if exc.status_code in (401, 403):
                raise KobaraAuthError("Authentification Kobara refusée. Vérifiez la clé API.") from exc
            raise KobaraAPIError(f"Erreur API Kobara: {exc}") from exc
        except RuntimeError as exc:
            # The SDK raises RuntimeError on network/requests connection exceptions
            logger.error("Erreur de connexion réseau lors de l'appel Kobara: %s", exc)
            raise KobaraNetworkError("Impossible de joindre le serveur Kobara.") from exc
        except Exception as exc:
            logger.error("Erreur inattendue lors de la création du paiement Kobara: %s", type(exc).__name__)
            raise KobaraAPIError("Erreur inattendue lors de la communication avec Kobara.") from exc

        # Extract data from SDK response (handles both {"data": {...}} and flat dict)
        data = response.get("data") if isinstance(response.get("data"), dict) else response
        if not isinstance(data, dict):
            raise KobaraAPIError("Réponse invalide reçue de Kobara.")

        kobara_id = data.get("id") or data.get("payment_id")
        checkout_url = data.get("checkout_url") or data.get("redirect_url")

        if not kobara_id:
            logger.error("Kobara n’a pas inclus d'identifiant dans sa réponse: %s", list(data.keys()))
            raise KobaraAPIError("Kobara n'a pas retourné d'identifiant de paiement valide.")

        if not checkout_url:
            logger.error("Kobara n’a pas inclus de checkout_url dans sa réponse: %s", list(data.keys()))
            raise KobaraAPIError("Kobara n'a pas retourné d'URL de paiement valide.")

        return ProviderCheckout(
            provider_payment_id=str(kobara_id),
            checkout_url=str(checkout_url),
            metadata={"order_id": payment.order_id, "provider": self.provider_name},
        )

    def verify_with_provider(self, payment: Payment) -> ProviderVerification | None:
        """In Kobara v1, individual GET is not exposed in public API; status is webhook-driven.

        Returning None keeps the payment in PENDING until confirmation by the webhook.
        """
        logger.info(
            "Vérification Kobara appelée pour le paiement %s (statut actuel: %s). En attente du webhook.",
            payment.order_id,
            payment.status,
        )
        return None
