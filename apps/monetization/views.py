import logging
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Invoice, Payment, PropertyPromotion, Subscription
from .moncash_service import MonCashError, MonCashService
from .serializers import (
    KobaraWebhookPayloadSerializer,
    KobaraWebhookResponseSerializer,
    MonCashWebhookPayloadSerializer,
    MonCashWebhookResponseSerializer,
)
from .services import MonetizationError

logger = logging.getLogger(__name__)


class MonCashWebhookView(APIView):
    """Endpoint serveur-à-serveur pour les alertes et notifications MonCash (Alert URL).

    Reçoit les notifications de Digicel MonCash (POST ou GET) contenant orderId ou transactionId.
    RÈGLE ABSOLUE DE SÉCURITÉ : Ne fait jamais confiance au payload reçu.
    Interroge systématiquement l'API officielle MonCash (RetrieveOrderPayment) côté serveur
    avec les identifiants OAuth avant toute validation de paiement.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=["Monetization"],
        summary="Webhook / Alert URL de notification MonCash (POST)",
        description=(
            "Endpoint public serveur-à-serveur appelé par MonCash pour notifier un changement de statut. "
            "Le serveur vérifie systématiquement auprès de MonCash (RetrieveOrderPayment) de manière "
            "atomique et idempotente."
        ),
        request=MonCashWebhookPayloadSerializer,
        responses={
            200: MonCashWebhookResponseSerializer,
            400: OpenApiTypes.OBJECT,
            404: OpenApiTypes.OBJECT,
            502: OpenApiTypes.OBJECT,
        },
        parameters=[
            OpenApiParameter(name="orderId", type=str, location=OpenApiParameter.QUERY, description="ID de commande (optionnel si dans body)"),
            OpenApiParameter(name="transactionId", type=str, location=OpenApiParameter.QUERY, description="ID de transaction MonCash (optionnel si dans body)"),
        ],
    )
    def post(self, request, *args, **kwargs):
        return self._handle_notification(request)

    @extend_schema(
        tags=["Monetization"],
        summary="Webhook / Alert URL de notification MonCash (GET)",
        description=(
            "Support de notification MonCash via requête GET pour Alert URL / Return URL. "
            "Mêmes garanties de vérification serveur-à-serveur et d'idempotence."
        ),
        responses={
            200: MonCashWebhookResponseSerializer,
            400: OpenApiTypes.OBJECT,
            404: OpenApiTypes.OBJECT,
            502: OpenApiTypes.OBJECT,
        },
        parameters=[
            OpenApiParameter(name="orderId", type=str, location=OpenApiParameter.QUERY, description="ID de commande"),
            OpenApiParameter(name="transactionId", type=str, location=OpenApiParameter.QUERY, description="ID de transaction MonCash"),
        ],
    )
    def get(self, request, *args, **kwargs):
        return self._handle_notification(request)

    def _handle_notification(self, request):
        # 1. Extraire les identifiants depuis le body ou la query string
        data = request.data if isinstance(request.data, dict) else {}
        query = request.query_params

        order_id = (
            data.get("orderId")
            or data.get("order_id")
            or query.get("orderId")
            or query.get("order_id")
        )
        transaction_id = (
            data.get("transactionId")
            or data.get("transaction_id")
            or query.get("transactionId")
            or query.get("transaction_id")
        )

        if not order_id and not transaction_id:
            logger.warning(
                "MonCash webhook: requête reçue sans orderId ni transactionId (IP: %s)",
                request.META.get("REMOTE_ADDR"),
            )
            return Response(
                {"detail": "Identifiant de commande (orderId) ou de transaction (transactionId) manquant."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 2. Retrouver le paiement dans la base de données
        payment = None
        if order_id:
            payment = (
                Payment.objects.select_related("property", "subscription", "promotion")
                .filter(order_id=str(order_id).strip())
                .first()
            )

        if not payment and transaction_id:
            t_id = str(transaction_id).strip()
            payment = (
                Payment.objects.select_related("property", "subscription", "promotion")
                .filter(Q(provider_transaction_id=t_id) | Q(provider_payment_id=t_id))
                .first()
            )

        if not payment:
            logger.warning(
                "MonCash webhook: aucun paiement trouvé pour order_id=%s, transaction_id=%s",
                order_id,
                transaction_id,
            )
            return Response(
                {"detail": "Aucun paiement correspondant trouvé."},
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.info(
            "MonCash webhook reçu: payment_id=%s, order_id=%s, statut_courant=%s",
            payment.pk,
            payment.order_id,
            payment.status,
        )

        # 3. Idempotence : si le paiement est déjà PAID, retourner immédiatement 200 OK
        if payment.status == Payment.Status.PAID:
            logger.info("MonCash webhook: paiement %s déjà PAID (notification idempotente).", payment.order_id)
            return Response(
                {
                    "detail": "Paiement déjà confirmé.",
                    "status": payment.status,
                    "order_id": payment.order_id,
                    "payment_id": payment.pk,
                    "property_id": payment.property_id,
                },
                status=status.HTTP_200_OK,
            )

        # 4. Vérification serveur-à-serveur auprès de MonCash (NE PAS FAIRE CONFIANCE AU PAYLOAD)
        service = MonCashService()
        try:
            confirmed_payment = service.confirm_payment(payment.pk)
        except (MonCashError, MonetizationError) as exc:
            logger.error(
                "MonCash webhook: échec lors de la vérification MonCash pour payment %s: %s",
                payment.order_id,
                exc,
            )
            return Response(
                {"detail": "Impossible de joindre la passerelle MonCash pour vérifier le paiement."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        detail_msg = {
            Payment.Status.PAID: "Paiement validé avec succès par MonCash.",
            Payment.Status.FAILED: "Paiement rejeté ou annulé par MonCash.",
            Payment.Status.PENDING: "Paiement toujours en cours de traitement par MonCash.",
        }.get(confirmed_payment.status, "Statut du paiement mis à jour.")

        return Response(
            {
                "detail": detail_msg,
                "status": confirmed_payment.status,
                "order_id": confirmed_payment.order_id,
                "payment_id": confirmed_payment.pk,
                "property_id": confirmed_payment.property_id,
            },
            status=status.HTTP_200_OK,
        )


class KobaraWebhookView(APIView):
    """Endpoint serveur-à-serveur sécurisé pour les notifications de paiement Kobara.

    Reçoit les webhooks signés de Kobara (ex: payment.succeeded).
    RÈGLES DE SÉCURITÉ :
    1. Vérifie la signature cryptographique HMAC-SHA256 via Kobara SDK construct_event().
    2. Respecte la fenêtre temporelle anti-rejeu (tolérance 300s).
    3. Valide le montant et la devise avant toute mise à jour du statut.
    4. Garantit l'idempotence et la concurrence via select_for_update().
    5. Ne publie JAMAIS automatiquement la propriété (reste DRAFT jusqu'à soumission).
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=["Monetization"],
        summary="Webhook de notification sécurisé Kobara (POST)",
        description=(
            "Endpoint public serveur-à-serveur appelé par Kobara pour notifier un événement de paiement. "
            "La requête doit obligatoirement inclure l'en-tête Kobara-Signature pour la vérification HMAC-SHA256."
        ),
        request=KobaraWebhookPayloadSerializer,
        responses={
            200: KobaraWebhookResponseSerializer,
            400: OpenApiTypes.OBJECT,
            404: OpenApiTypes.OBJECT,
            500: OpenApiTypes.OBJECT,
        },
        parameters=[
            OpenApiParameter(
                name="Kobara-Signature",
                type=str,
                location=OpenApiParameter.HEADER,
                required=True,
                description="Signature HMAC-SHA256 fournie par Kobara (format: t=<timestamp>,v1=<signature>)",
            ),
        ],
    )
    def post(self, request, *args, **kwargs):
        # 1. Vérifier la configuration du secret webhook
        secret = getattr(settings, "KOBARA_WEBHOOK_SECRET", "")
        if not secret:
            logger.error("Kobara webhook: KOBARA_WEBHOOK_SECRET non configuré dans les settings.")
            return Response(
                {"detail": "Configuration serveur incomplète pour les webhooks."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # 2. Récupérer l'en-tête de signature
        signature = request.headers.get("Kobara-Signature") or request.META.get("HTTP_KOBARA_SIGNATURE", "")
        if not signature:
            logger.warning(
                "Kobara webhook: en-tête Kobara-Signature manquant (IP: %s)",
                request.META.get("REMOTE_ADDR"),
            )
            return Response(
                {"detail": "En-tête Kobara-Signature manquant."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 3. Récupérer le corps brut (raw body) de la requête
        try:
            raw_body = request.body.decode("utf-8") if isinstance(request.body, bytes) else str(request.body)
        except Exception as exc:
            logger.error("Kobara webhook: échec de décodage du corps de la requête: %s", exc)
            return Response(
                {"detail": "Impossible de lire le corps brut de la requête."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 4. Vérifier la signature via le SDK officiel Kobara
        try:
            from kobara.resources.webhooks import WebhooksResource
            from kobara.errors import KobaraSignatureVerificationError
            event = WebhooksResource.construct_event(raw_body, signature, secret)
        except KobaraSignatureVerificationError as exc:
            logger.warning("Kobara webhook: échec de vérification de signature: %s", exc)
            return Response(
                {"detail": "Signature de webhook invalide ou expirée."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            logger.error("Kobara webhook: erreur inattendue lors de la vérification: %s", type(exc).__name__)
            return Response(
                {"detail": "Erreur lors de la validation du webhook."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not isinstance(event, dict):
            logger.warning("Kobara webhook: payload non interprétable en dictionnaire JSON.")
            return Response(
                {"detail": "Format d'événement webhook invalide."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        event_type = event.get("event_type") or event.get("type")
        logger.info("Kobara webhook reçu: événement=%s", event_type)

        # 5. Filtrer les événements non concernés
        if event_type != "payment.succeeded":
            logger.info("Kobara webhook: événement ignoré car non concerné: %s", event_type)
            return Response(
                {"detail": f"Événement {event_type} reçu et ignoré.", "event_type": event_type},
                status=status.HTTP_200_OK,
            )

        # 6. Extraire les métadonnées et identifiants
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}

        order_id = metadata.get("order_id") or data.get("order_id")
        payment_id = metadata.get("payment_id") or data.get("payment_id") or data.get("id")

        if not order_id and not payment_id:
            logger.warning("Kobara webhook: aucun identifiant order_id ni payment_id dans le payload.")
            return Response(
                {"detail": "Identifiants de paiement introuvables dans l'événement."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 7. Recherche du Payment local (priorité à order_id)
        payment = None
        if order_id:
            payment = (
                Payment.objects.select_related("property", "subscription", "promotion")
                .filter(order_id=str(order_id).strip())
                .first()
            )

        if not payment and payment_id:
            payment_filter = Q(provider_payment_id=str(payment_id).strip())
            try:
                import uuid
                parsed_uuid = uuid.UUID(str(payment_id).strip())
                payment_filter |= Q(pk=parsed_uuid)
            except (ValueError, AttributeError, TypeError):
                pass
            payment = (
                Payment.objects.select_related("property", "subscription", "promotion")
                .filter(payment_filter)
                .first()
            )

        if not payment:
            logger.warning(
                "Kobara webhook: aucun paiement trouvé pour order_id=%s, payment_id=%s",
                order_id,
                payment_id,
            )
            return Response(
                {"detail": "Aucun paiement correspondant trouvé."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # 8. Vérifier le provider
        if payment.provider != "KOBARA":
            logger.warning(
                "Kobara webhook: provider incohérent pour payment %s (attendu=KOBARA, actuel=%s)",
                payment.order_id,
                payment.provider,
            )
            return Response(
                {"detail": "Fournisseur de paiement incohérent pour ce paiement."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 9. Vérifier le montant et la devise
        raw_amount = data.get("amount")
        if raw_amount is not None:
            try:
                event_amount = Decimal(str(raw_amount))
            except Exception:
                logger.error("Kobara webhook: montant invalide reçu: %s", raw_amount)
                return Response(
                    {"detail": "Montant invalide dans le webhook."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if event_amount != payment.amount:
                logger.error(
                    "Kobara webhook: incohérence de montant pour payment %s: reçu=%s, attendu=%s",
                    payment.order_id,
                    event_amount,
                    payment.amount,
                )
                return Response(
                    {"detail": "Incohérence du montant reçu par rapport au paiement enregistré."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        raw_currency = data.get("currency")
        if raw_currency:
            if str(raw_currency).strip().upper() != payment.currency.upper():
                logger.error(
                    "Kobara webhook: incohérence de devise pour payment %s: reçu=%s, attendu=%s",
                    payment.order_id,
                    raw_currency,
                    payment.currency,
                )
                return Response(
                    {"detail": "Incohérence de la devise reçue par rapport au paiement enregistré."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # 10. Idempotence : si déjà PAID, répondre immédiatement avec succès sans effets de bord
        if payment.status == Payment.Status.PAID:
            logger.info("Kobara webhook: paiement %s déjà PAID (notification idempotente).", payment.order_id)
            return Response(
                {
                    "detail": "Paiement déjà confirmé.",
                    "status": payment.status,
                    "order_id": payment.order_id,
                    "payment_id": payment.pk,
                    "property_id": payment.property_id,
                },
                status=status.HTTP_200_OK,
            )

        # 11. Transition atomique vers PAID avec verrouillage
        with transaction.atomic():
            payment = Payment.objects.select_for_update().get(pk=payment.pk)
            if payment.status == Payment.Status.PAID:
                return Response(
                    {
                        "detail": "Paiement déjà confirmé.",
                        "status": payment.status,
                        "order_id": payment.order_id,
                        "payment_id": payment.pk,
                        "property_id": payment.property_id,
                    },
                    status=status.HTTP_200_OK,
                )

            provider_payment_id = data.get("payment_id") or data.get("id") or payment.provider_payment_id
            provider_transaction_id = (
                data.get("transaction_id")
                or data.get("reference")
                or data.get("provider_transaction_id")
                or payment.provider_transaction_id
            )

            now = timezone.now()
            payment.status = Payment.Status.PAID
            payment.paid_at = now
            update_fields = ["status", "paid_at", "updated_at"]

            if provider_payment_id:
                payment.provider_payment_id = str(provider_payment_id)
                update_fields.append("provider_payment_id")

            if provider_transaction_id:
                payment.provider_transaction_id = str(provider_transaction_id)
                update_fields.append("provider_transaction_id")

            payment.save(update_fields=tuple(update_fields))

            # Mettre à jour l'Invoice correspondante
            try:
                invoice = Invoice.objects.select_for_update().get(payment=payment)
                invoice.status = Invoice.Status.PAID
                invoice.paid_at = payment.paid_at
                invoice.save(update_fields=("status", "paid_at", "updated_at"))
            except Invoice.DoesNotExist:
                pass

            # Mettre à jour Subscription / Promotion si applicable
            if payment.subscription_id:
                subscription = Subscription.objects.select_for_update().get(pk=payment.subscription_id)
                if subscription.status == Subscription.Status.PAST_DUE:
                    subscription.status = Subscription.Status.ACTIVE
                    subscription.save(update_fields=("status", "updated_at"))
            elif payment.promotion_id:
                promotion = PropertyPromotion.objects.select_for_update().get(pk=payment.promotion_id)
                promotion.status = PropertyPromotion.Status.ACTIVE
                promotion.save(update_fields=("status", "updated_at"))

        logger.info(
            "Kobara webhook: paiement %s validé avec succès (statut=PAID, montant=%s %s).",
            payment.order_id,
            payment.amount,
            payment.currency,
        )

        return Response(
            {
                "detail": "Paiement validé avec succès par Kobara.",
                "status": payment.status,
                "order_id": payment.order_id,
                "payment_id": payment.pk,
                "property_id": payment.property_id,
            },
            status=status.HTTP_200_OK,
        )
