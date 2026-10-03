import logging
from dataclasses import dataclass
from decimal import Decimal
import requests
from requests.auth import HTTPBasicAuth

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .models import Payment
from .services import MonetizationError, PaymentService, ProviderCheckout, ProviderVerification

logger = logging.getLogger(__name__)


class MonCashError(MonetizationError):
    """Base error for MonCash gateway operations."""


class MonCashConfigError(MonCashError):
    """Raised when MonCash configuration (keys or URLs) is missing or invalid."""


class MonCashAuthError(MonCashError):
    """Raised when server-to-server OAuth authentication with MonCash fails."""


class MonCashAPIError(MonCashError):
    """Raised when a MonCash REST API call fails or returns an unexpected response."""


class MonCashService(PaymentService):
    """MonCash provider adapter implementing server-to-server checkout and verification."""

    @property
    def provider_name(self) -> str:
        return "MONCASH"

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        api_url: str | None = None,
        gateway_url: str | None = None,
        mode: str | None = None,
        timeout: int | None = None,
    ):
        self.mode = mode or getattr(settings, "MONCASH_MODE", "sandbox")
        self.client_id = client_id or getattr(settings, "MONCASH_CLIENT_ID", "")
        self.client_secret = client_secret or getattr(settings, "MONCASH_CLIENT_SECRET", "")
        self.api_url = (api_url or getattr(
            settings,
            "MONCASH_API_URL",
            "https://sandbox.moncashbutton.digicelgroup.com/Api",
        )).rstrip("/")
        self.gateway_url = (gateway_url or getattr(
            settings,
            "MONCASH_GATEWAY_URL",
            "https://sandbox.moncashbutton.digicelgroup.com/Moncash-middleware",
        )).rstrip("/")
        self.timeout = timeout or getattr(settings, "MONCASH_TIMEOUT_SECONDS", 15)

    def get_access_token(self) -> str:
        """Retrieve a valid server-to-server OAuth token, caching it until near expiration."""
        cache_key = f"moncash_oauth_token_{self.mode}"
        cached_token = cache.get(cache_key)
        if cached_token:
            return cached_token

        if not self.client_id or not self.client_secret:
            raise MonCashConfigError(
                "Les identifiants MonCash (MONCASH_CLIENT_ID / MONCASH_CLIENT_SECRET) ne sont pas configurés."
            )

        token_url = f"{self.api_url}/oauth/token"
        try:
            response = requests.post(
                token_url,
                auth=HTTPBasicAuth(self.client_id, self.client_secret),
                headers={"Accept": "application/json"},
                data={"grant_type": "client_credentials", "scope": "read,write"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            logger.error("Erreur de connexion réseau lors de l’authentification MonCash: %s", type(exc).__name__)
            raise MonCashAuthError("Impossible de joindre le serveur MonCash pour l’authentification.") from exc

        if response.status_code != 200:
            logger.error("Échec de l’authentification MonCash (HTTP %s)", response.status_code)
            raise MonCashAuthError(f"Authentification MonCash refusée (HTTP {response.status_code}).")

        try:
            payload = response.json()
        except Exception as exc:
            logger.error("Réponse JSON invalide reçue de MonCash /oauth/token")
            raise MonCashAuthError("Réponse d’authentification MonCash non interprétable (JSON invalide).") from exc

        access_token = payload.get("access_token")
        if not access_token:
            raise MonCashAuthError("Le serveur MonCash n’a pas retourné de jeton d’accès valide.")

        expires_in = payload.get("expires_in", 3600)
        try:
            expires_seconds = int(expires_in)
        except (ValueError, TypeError):
            expires_seconds = 3600

        # Cache with a 60-second safety margin before actual token expiration
        cache_timeout = max(60, expires_seconds - 60)
        cache.set(cache_key, access_token, timeout=cache_timeout)

        return access_token

    def create_provider_checkout(self, payment: Payment) -> ProviderCheckout:
        """Call MonCash CreatePayment API and return a ProviderCheckout containing the Gateway URL."""
        token = self.get_access_token()
        create_url = f"{self.api_url}/v1/CreatePayment"

        amount_val = int(payment.amount) if payment.amount == int(payment.amount) else float(payment.amount)
        payload = {
            "amount": amount_val,
            "orderId": str(payment.order_id),
        }

        try:
            response = requests.post(
                create_url,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            logger.error("Erreur de connexion réseau lors de MonCash CreatePayment: %s", type(exc).__name__)
            raise MonCashAPIError("Impossible de joindre le serveur MonCash pour créer le paiement.") from exc

        if response.status_code not in (200, 201, 202):
            logger.error("MonCash CreatePayment a échoué avec le code HTTP %s", response.status_code)
            raise MonCashAPIError(f"MonCash CreatePayment a échoué (HTTP {response.status_code}).")

        try:
            data = response.json()
        except Exception as exc:
            logger.error("Réponse JSON invalide reçue de MonCash /v1/CreatePayment")
            raise MonCashAPIError("Réponse CreatePayment MonCash non interprétable (JSON invalide).") from exc

        payment_token = None
        raw_pt = data.get("payment_token")
        if isinstance(raw_pt, dict):
            payment_token = raw_pt.get("token")
        elif isinstance(raw_pt, str):
            payment_token = raw_pt
        elif "token" in data:
            payment_token = data.get("token")

        if not payment_token:
            logger.error("MonCash n’a pas inclus de token dans sa réponse CreatePayment")
            raise MonCashAPIError("MonCash n’a pas retourné de token de paiement valide.")

        checkout_url = f"{self.gateway_url}/Payment/Redirect?token={payment_token}"

        return ProviderCheckout(
            provider_payment_id=payment_token,
            checkout_url=checkout_url,
            metadata={"order_id": payment.order_id, "mode": self.mode},
        )

    def verify_with_provider(self, payment: Payment) -> ProviderVerification | None:
        """Verify payment status with MonCash via RetrieveOrderPayment using orderId.

        Endpoints and specs:
        - POST {MONCASH_API_URL}/v1/RetrieveOrderPayment
        - Body: {"orderId": payment.order_id}
        - Headers: Authorization: Bearer <token>, Accept: application/json, Content-Type: application/json
        - Returns ProviderVerification with is_paid=True if confirmed,
          or is_paid=False if payment explicitly failed,
          or None if order is still pending or not yet submitted by payer.
        """
        token = self.get_access_token()
        retrieve_url = f"{self.api_url}/v1/RetrieveOrderPayment"

        payload = {"orderId": str(payment.order_id)}

        try:
            response = requests.post(
                retrieve_url,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            logger.error("Erreur de connexion réseau lors de MonCash RetrieveOrderPayment: %s", type(exc).__name__)
            raise MonCashAPIError("Impossible de joindre le serveur MonCash pour vérifier le paiement.") from exc

        # MonCash returns HTTP 404 if order is not completed or not yet registered
        if response.status_code == 404:
            logger.info("MonCash RetrieveOrderPayment HTTP 404: order %s en attente ou non trouvée", payment.order_id)
            return None

        if response.status_code not in (200, 201):
            logger.error("MonCash RetrieveOrderPayment a retourné le code HTTP %s", response.status_code)
            raise MonCashAPIError(f"MonCash RetrieveOrderPayment a échoué (HTTP {response.status_code}).")

        try:
            data = response.json()
        except Exception as exc:
            logger.error("Réponse JSON invalide reçue de MonCash /v1/RetrieveOrderPayment")
            raise MonCashAPIError("Réponse RetrieveOrderPayment MonCash non interprétable (JSON invalide).") from exc

        # Check for status indicator in JSON body
        status_code = data.get("status")
        if status_code in (404, "404"):
            logger.info("MonCash RetrieveOrderPayment payload status 404 pour order %s", payment.order_id)
            return None

        payment_info = data.get("payment")
        if not payment_info or not isinstance(payment_info, dict):
            message = str(data.get("message", "")).lower()
            if "not found" in message or "pending" in message:
                return None
            logger.warning("Réponse MonCash sans données de paiement exploitables pour order %s", payment.order_id)
            return None

        payment_message = str(payment_info.get("message") or data.get("message") or "").lower()
        success_indicators = {"successful", "success", "completed", "paid", "200"}
        failure_indicators = {"failed", "cancelled", "canceled", "rejected", "expired"}

        if payment_message in failure_indicators:
            is_paid = False
        elif payment_message in success_indicators or status_code in (200, "200"):
            is_paid = True
        else:
            logger.warning("Statut MonCash ambigu pour order %s: %s", payment.order_id, payment_message)
            return None

        raw_cost = payment_info.get("cost")
        if raw_cost is None:
            raw_cost = payment_info.get("amount")
        if raw_cost is None:
            amount = payment.amount
        else:
            try:
                amount = Decimal(str(raw_cost))
            except Exception:
                amount = payment.amount

        transaction_id = payment_info.get("transaction_id") or payment_info.get("reference")
        transaction_id_str = str(transaction_id) if transaction_id else None

        paid_at = None
        raw_date = payment_info.get("date")
        if raw_date:
            if isinstance(raw_date, (int, float)):
                ts = raw_date / 1000 if raw_date > 1e11 else raw_date
                paid_at = timezone.datetime.fromtimestamp(ts, tz=timezone.utc)
            elif isinstance(raw_date, str):
                for fmt in (
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%SZ",
                    "%Y-%m-%dT%H:%M:%S%z",
                    "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d",
                ):
                    try:
                        dt = timezone.datetime.strptime(raw_date, fmt)
                        if timezone.is_naive(dt):
                            paid_at = timezone.make_aware(dt, timezone.get_current_timezone())
                        else:
                            paid_at = dt
                        break
                    except (ValueError, TypeError):
                        continue

        if is_paid and paid_at is None:
            paid_at = timezone.now()

        provider_payment_id = payment.provider_payment_id or str(payment_info.get("reference") or payment.order_id)

        return ProviderVerification(
            provider_payment_id=provider_payment_id,
            amount=amount,
            currency="HTG",
            is_paid=is_paid,
            paid_at=paid_at if is_paid else None,
            provider_transaction_id=transaction_id_str,
        )
