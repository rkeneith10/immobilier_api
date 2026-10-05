import logging
from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Payment
from .moncash_service import MonCashError, MonCashService
from .serializers import (
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
