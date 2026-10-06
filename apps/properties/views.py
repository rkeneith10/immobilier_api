from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend

from apps.monetization.kobara_service import (
    KobaraAuthError,
    KobaraConfigError,
    KobaraError,
    KobaraService,
)
from apps.monetization.moncash_service import (
    MonCashAuthError,
    MonCashConfigError,
    MonCashError,
    MonCashService,
)
from apps.monetization.models import Payment
from apps.monetization.serializers import (
    PublicationEligibilitySerializer,
    PublicationPaymentInitiateResponseSerializer,
    PublicationPaymentVerifyResponseSerializer,
)
from apps.monetization.services import (
    MonetizationError,
    check_publication_eligibility,
    create_property_publication_payment,
    get_payment_service,
    get_publication_price,
)
from .filters import AmenityFilter, PropertyFilter, PropertyTypeFilter
from .models import Amenity, Property, PropertyType, PropertyView
from .pagination import PropertyPagination, PropertyTypePagination
from .permissions import PropertyPermission
from apps.common.permissions import IsAdminOrReadOnly
from apps.interactions.serializers import (
    FavoriteSerializer,
    InquiryCreateSerializer,
    InquirySerializer,
    VisitRequestCreateSerializer,
    VisitRequestSerializer,
)
from apps.interactions.services import (
    PropertyNotPublished,
    add_favorite,
    create_inquiry,
    create_visit_request,
    remove_favorite,
)
from apps.interactions.report_serializers import PropertyReportCreateSerializer, PropertyReportSerializer
from apps.interactions.property_report_services import (
    DuplicateActivePropertyReport,
    PropertyNotPublished as ReportPropertyNotPublished,
    create_property_report,
)
from .serializers import AmenitySerializer, PropertySerializer, PropertyTypeSerializer
from .services import InvalidPropertyTransition, transition_property


@extend_schema_view(
    list=extend_schema(tags=["Amenities"], summary="Lister les équipements disponibles"),
    retrieve=extend_schema(tags=["Amenities"], summary="Consulter un équipement"),
    create=extend_schema(tags=["Amenities"], summary="Créer un équipement"),
    update=extend_schema(tags=["Amenities"], summary="Modifier un équipement"),
    partial_update=extend_schema(tags=["Amenities"], summary="Modifier un équipement"),
    destroy=extend_schema(tags=["Amenities"], summary="Supprimer un équipement"),
)
class AmenityViewSet(viewsets.ModelViewSet):
    serializer_class = AmenitySerializer
    permission_classes = (IsAdminOrReadOnly,)
    pagination_class = PropertyTypePagination
    filterset_class = AmenityFilter
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    search_fields = ("name", "slug")
    ordering_fields = ("name", "created_at", "updated_at")
    ordering = ("name",)

    def get_queryset(self):
        queryset = Amenity.objects.all()
        user = self.request.user
        if not (
            user.is_authenticated
            and user.is_active
            and getattr(user, "role", None) == "ADMIN"
        ):
            queryset = queryset.filter(is_active=True)
        return queryset


@extend_schema_view(
    list=extend_schema(tags=["Property types"], summary="Lister les types de propriétés"),
    retrieve=extend_schema(tags=["Property types"], summary="Consulter un type de propriété"),
    create=extend_schema(tags=["Property types"], summary="Créer un type de propriété"),
    update=extend_schema(tags=["Property types"], summary="Modifier un type de propriété"),
    partial_update=extend_schema(tags=["Property types"], summary="Modifier un type de propriété"),
    destroy=extend_schema(tags=["Property types"], summary="Supprimer un type de propriété"),
)
class PropertyTypeViewSet(viewsets.ModelViewSet):
    serializer_class = PropertyTypeSerializer
    permission_classes = (IsAdminOrReadOnly,)
    pagination_class = PropertyTypePagination
    filterset_class = PropertyTypeFilter
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    search_fields = ("name", "slug")
    ordering_fields = ("name", "created_at", "updated_at")
    ordering = ("name",)

    def get_queryset(self):
        queryset = PropertyType.objects.all()
        user = self.request.user
        if not (
            user.is_authenticated
            and user.is_active
            and getattr(user, "role", None) == "ADMIN"
        ):
            queryset = queryset.filter(is_active=True)
        return queryset


@extend_schema_view(
    list=extend_schema(
        tags=["Properties"],
        summary="Rechercher et filtrer les propriétés visibles",
        description=(
            "Les résultats publics contiennent uniquement les annonces PUBLISHED. Un OWNER/AGENT voit aussi "
            "ses propres annonces privées ; ADMIN peut consulter tous les statuts. Les filtres numériques sont "
            "des bornes inclusives. `search` interroge le titre et la description. `ordering` accepte `created_at`, "
            "`price`, `bedrooms` ou `area`, avec un préfixe `-` pour l’ordre décroissant."
        ),
        parameters=[
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Recherche insensible à la casse dans `title` et `description`.",
            ),
            OpenApiParameter(
                name="ordering",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description=(
                    "Champ(s) de tri : `created_at`, `price`, `bedrooms` ou `area`. Préfixez un champ par `-` "
                    "pour le tri décroissant. Plusieurs champs peuvent être séparés par une virgule."
                ),
            ),
        ],
    ),
    retrieve=extend_schema(tags=["Properties"], summary="Consulter une propriété"),
    create=extend_schema(tags=["Properties"], summary="Créer un brouillon de propriété"),
    partial_update=extend_schema(tags=["Properties"], summary="Modifier une propriété"),
    destroy=extend_schema(tags=["Properties"], summary="Supprimer une propriété"),
)
class PropertyViewSet(viewsets.ModelViewSet):
    serializer_class = PropertySerializer
    permission_classes = (PropertyPermission,)
    pagination_class = PropertyPagination
    filterset_class = PropertyFilter
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    search_fields = ("title", "description")
    ordering_fields = ("created_at", "price", "bedrooms", "area")
    ordering = ("-created_at",)
    http_method_names = ("get", "post", "patch", "delete", "head", "options")

    def get_queryset(self):
        queryset = Property.objects.select_related("owner", "property_type", "location").prefetch_related("amenities", "images")
        user = self.request.user
        if not user.is_authenticated or not user.is_active:
            return queryset.filter(status=Property.Status.PUBLISHED)

        role = getattr(user, "role", None)
        if role == "ADMIN":
            return queryset
        if role in PropertyPermission.contributor_roles:
            return queryset.filter(Q(status=Property.Status.PUBLISHED) | Q(owner=user))
        return queryset.filter(status=Property.Status.PUBLISHED)

    def get_object(self):
        queryset = self.filter_queryset(self.get_queryset())
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        lookup_val = self.kwargs.get(lookup_url_kwarg)
        import uuid
        from rest_framework.generics import get_object_or_404
        try:
            uuid.UUID(str(lookup_val))
            filter_kwargs = {self.lookup_field: lookup_val}
        except (ValueError, AttributeError):
            filter_kwargs = {"slug": lookup_val}
        obj = get_object_or_404(queryset, **filter_kwargs)
        self.check_object_permissions(self.request, obj)
        return obj

    def perform_create(self, serializer):
        from apps.monetization.services import SubscriptionLimitReached, SubscriptionService

        # Les utilisateurs du plan standard (FREE) monétisent à la publication via MonCash
        # (1ère publication gratuite, puis paiement requis de 500 HTG à chaque nouvelle annonce).
        # Ils peuvent donc créer et préparer leurs brouillons librement.
        plan = SubscriptionService.get_effective_plan(self.request.user)
        if plan and plan.code != "FREE":
            try:
                SubscriptionService.ensure_property_capacity(self.request.user)
            except SubscriptionLimitReached as exc:
                raise ValidationError({"plan": str(exc)}) from exc
        serializer.save(owner=self.request.user)

    def retrieve(self, request, *args, **kwargs):
        property_obj = self.get_object()
        response = Response(self.get_serializer(property_obj).data)
        if property_obj.status == Property.Status.PUBLISHED and not (
            request.user.is_authenticated
            and (request.user.pk == property_obj.owner_id or getattr(request.user, "role", None) == "ADMIN")
        ):
            PropertyView.objects.create(
                property=property_obj,
                viewer=request.user if request.user.is_authenticated else None,
            )
        return response

    @extend_schema(
        tags=["Favorites"],
        summary="Ajouter ou retirer une propriété des favoris",
        description="POST ajoute un favori uniquement pour une annonce PUBLISHED ; DELETE retire le favori de l’utilisateur courant.",
        request=None,
        responses={200: FavoriteSerializer, 201: FavoriteSerializer, 204: None},
    )
    @action(
        detail=True,
        methods=["post", "delete"],
        url_path="favorite",
        permission_classes=(IsAuthenticated,),
    )
    def favorite(self, request, pk=None):
        if request.method == "POST":
            property_obj = self.get_object()
            try:
                favorite, created = add_favorite(request.user, property_obj.pk)
            except PropertyNotPublished as exc:
                raise ValidationError({"property": "Seules les propriétés publiées peuvent être ajoutées aux favoris."}) from exc
            return Response(
                FavoriteSerializer(favorite).data,
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )

        if not remove_favorite(request.user, pk):
            raise NotFound("Ce favori n’existe pas pour l’utilisateur courant.")
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        tags=["Inquiries"],
        summary="Contacter le propriétaire d’une propriété publiée",
        description="L’auteur et le propriétaire destinataire sont déterminés depuis le contexte authentifié et l’annonce.",
        request=InquiryCreateSerializer,
        responses={201: InquirySerializer},
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="inquiries",
        permission_classes=(IsAuthenticated,),
    )
    def inquiries(self, request, pk=None):
        property_obj = self.get_object()
        serializer = InquiryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            inquiry = create_inquiry(request.user, property_obj.pk, serializer.validated_data["message"])
        except PropertyNotPublished as exc:
            raise ValidationError({"property": "Vous ne pouvez contacter que le propriétaire d’une propriété publiée."}) from exc
        return Response(InquirySerializer(inquiry).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=["Visit requests"],
        summary="Demander une visite pour une propriété publiée",
        description="L’auteur et le propriétaire sont fixés côté serveur. La date et l’heure doivent être futures.",
        request=VisitRequestCreateSerializer,
        responses={201: VisitRequestSerializer},
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="visits",
        permission_classes=(IsAuthenticated,),
    )
    def visits(self, request, pk=None):
        property_obj = self.get_object()
        serializer = VisitRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            visit_request = create_visit_request(
                request.user,
                property_obj.pk,
                serializer.validated_data["requested_date"],
                serializer.validated_data["requested_time"],
                serializer.validated_data.get("message", ""),
            )
        except PropertyNotPublished as exc:
            raise ValidationError({"property": "Une visite ne peut être demandée que pour une propriété publiée."}) from exc
        return Response(VisitRequestSerializer(visit_request).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=["Property reports"],
        summary="Signaler une propriété publiée",
        description="Une même combinaison propriété/utilisateur/motif ne peut avoir qu’un signalement actif à la fois.",
        request=PropertyReportCreateSerializer,
        responses={201: PropertyReportSerializer},
    )
    @action(detail=True, methods=["post"], url_path="reports", permission_classes=(IsAuthenticated,))
    def reports(self, request, pk=None):
        serializer = PropertyReportCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            report = create_property_report(
                property_id=pk,
                reported_by=request.user,
                reason=serializer.validated_data["reason"],
                description=serializer.validated_data.get("description", ""),
            )
        except Property.DoesNotExist as exc:
            raise NotFound("Cette propriété n’existe pas.") from exc
        except ReportPropertyNotPublished as exc:
            raise ValidationError({"property": "Seules les propriétés publiées peuvent être signalées."}) from exc
        except DuplicateActivePropertyReport as exc:
            raise ValidationError({"detail": "Un signalement actif identique existe déjà pour cette propriété."}) from exc
        return Response(PropertyReportSerializer(report).data, status=status.HTTP_201_CREATED)

    def _transition(self, property_obj, target_status, allowed_from):
        try:
            updated = transition_property(property_obj.pk, target_status, allowed_from)
        except InvalidPropertyTransition as exc:
            raise ValidationError({"status": str(exc)}) from exc
        return Response(self.get_serializer(updated).data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Properties"],
        summary="Soumettre une propriété pour examen",
        description=(
            "Vérifie l’éligibilité à la publication (1re publication gratuite par OWNER). "
            "Si un paiement est requis et n’a pas encore été confirmé, renvoie un statut HTTP 402 "
            "avec les détails d’éligibilité. Sinon, bascule l’annonce vers PENDING_REVIEW."
        ),
        request=None,
        responses={
            200: PropertySerializer,
            402: PublicationEligibilitySerializer,
        },
    )
    @action(detail=True, methods=["post"], url_path="submit-for-review")
    def submit_for_review(self, request, pk=None):
        with transaction.atomic():
            property_obj = self.get_object()
            Property.objects.select_for_update().get(pk=property_obj.pk)
            get_user_model().objects.select_for_update().get(pk=property_obj.owner_id)

            eligibility = check_publication_eligibility(property_obj, user=request.user)
            if eligibility.requires_payment and not eligibility.already_paid:
                data = PublicationEligibilitySerializer(eligibility).data
                data["detail"] = "Un paiement est requis pour soumettre cette annonce à la modération."
                return Response(data, status=status.HTTP_402_PAYMENT_REQUIRED)

            return self._transition(
                property_obj,
                Property.Status.PENDING_REVIEW,
                {Property.Status.DRAFT, Property.Status.REJECTED},
            )

    @extend_schema(
        tags=["Properties"],
        summary="Vérifier l’éligibilité à la publication d’une propriété",
        description=(
            "Indique si la publication de la propriété est gratuite ou requiert un paiement. "
            "Accessible uniquement par le propriétaire (OWNER/AGENT) de l’annonce ou un administrateur."
        ),
        request=None,
        responses={
            200: PublicationEligibilitySerializer,
            401: OpenApiTypes.OBJECT,
            403: OpenApiTypes.OBJECT,
            404: OpenApiTypes.OBJECT,
        },
    )
    @action(
        detail=True,
        methods=["get"],
        url_path="publication-eligibility",
        permission_classes=(IsAuthenticated, PropertyPermission),
    )
    def publication_eligibility(self, request, pk=None):
        property_obj = self.get_object()
        eligibility = check_publication_eligibility(property_obj, user=request.user)
        return Response(PublicationEligibilitySerializer(eligibility).data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Properties"],
        summary="Initialiser un paiement de publication d’annonce",
        description=(
            "Vérifie l’éligibilité de la propriété et génère une session de paiement sécurisée (Kobara / MonCash). "
            "Renvoie l’URL de redirection vers laquelle rediriger l’utilisateur. "
            "Si la propriété est déjà payée ou bénéficie d’une publication gratuite, la création est refusée."
        ),
        request=None,
        responses={
            200: PublicationPaymentInitiateResponseSerializer,
            400: OpenApiTypes.OBJECT,
            401: OpenApiTypes.OBJECT,
            403: OpenApiTypes.OBJECT,
            404: OpenApiTypes.OBJECT,
            500: OpenApiTypes.OBJECT,
            502: OpenApiTypes.OBJECT,
        },
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="initiate-publication-payment",
        permission_classes=(IsAuthenticated, PropertyPermission),
    )
    def initiate_publication_payment(self, request, pk=None):
        from django.conf import settings

        requested_provider = (
            request.data.get("provider")
            if isinstance(request.data, dict)
            else None
        ) or request.query_params.get("provider")

        default_provider = (
            requested_provider or getattr(settings, "DEFAULT_PAYMENT_PROVIDER", "KOBARA")
        ).upper()

        # 1. Fetch object and verify eligibility within a short database transaction
        with transaction.atomic():
            property_obj = self.get_object()
            Property.objects.select_for_update().get(pk=property_obj.pk)
            get_user_model().objects.select_for_update().get(pk=property_obj.owner_id)

            eligibility = check_publication_eligibility(property_obj, user=request.user)
            if eligibility.already_paid:
                return Response(
                    {"detail": "Cette propriété a déjà un paiement confirmé.", "already_paid": True},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not eligibility.requires_payment:
                return Response(
                    {
                        "detail": "Cette propriété bénéficie d’une publication gratuite ou ne requiert pas de paiement.",
                        "is_free": eligibility.is_free,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Check for an existing PENDING payment for this property with active provider
            pending_payment = (
                Payment.objects.filter(
                    property=property_obj,
                    provider=default_provider,
                    status=Payment.Status.PENDING,
                )
                .order_by("-created_at")
                .first()
            )

            # Idempotence: if existing pending payment already has provider token, return redirect URL
            if pending_payment and pending_payment.provider_payment_id:
                token_age = (timezone.now() - pending_payment.created_at).total_seconds()
                max_age = 600 if default_provider == "MONCASH" else 3600
                if token_age < max_age:
                    if default_provider == "MONCASH":
                        gateway_url = getattr(settings, "MONCASH_GATEWAY_URL", "").rstrip("/")
                        redirect_url = f"{gateway_url}/Payment/Redirect?token={pending_payment.provider_payment_id}"
                    else:
                        redirect_url = f"https://pay.kobara.app/checkout/{pending_payment.provider_payment_id}"

                    data = {
                        "payment_id": pending_payment.pk,
                        "status": pending_payment.status,
                        "order_id": pending_payment.order_id,
                        "amount": pending_payment.amount,
                        "currency": pending_payment.currency,
                        "provider": pending_payment.provider,
                        "redirect_url": redirect_url,
                    }
                    return Response(
                        PublicationPaymentInitiateResponseSerializer(data).data,
                        status=status.HTTP_200_OK,
                    )
                else:
                    pending_payment.status = Payment.Status.FAILED
                    pending_payment.save(update_fields=("status", "updated_at"))
                    pending_payment = None

            if pending_payment is None:
                amount = get_publication_price()
                currency = "HTG"
                payment = create_property_publication_payment(
                    property_obj=property_obj,
                    provider=default_provider,
                    amount=amount,
                    currency=currency,
                )
            else:
                payment = pending_payment

        # 2. Call provider service OUTSIDE the database transaction
        service = get_payment_service(payment.provider)
        try:
            checkout = service.start_payment(payment.pk)
        except (KobaraConfigError, MonCashConfigError) as exc:
            import logging
            logging.getLogger(__name__).error(
                "Configuration manquante pour le provider %s: %s", payment.provider, exc
            )
            return Response(
                {"detail": "La passerelle de paiement n'est pas correctement configurée. Veuillez contacter le support."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except (KobaraError, MonCashError, MonetizationError) as exc:
            import logging
            logging.getLogger(__name__).error(
                "Échec de l’initialisation %s pour le paiement %s: %s", payment.provider, payment.order_id, exc
            )
            return Response(
                {"detail": f"Impossible d’initialiser le paiement auprès de {payment.provider}. Veuillez réessayer ultérieurement."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        data = {
            "payment_id": payment.pk,
            "status": payment.status,
            "order_id": payment.order_id,
            "amount": payment.amount,
            "currency": payment.currency,
            "provider": payment.provider,
            "redirect_url": checkout.checkout_url,
        }
        return Response(
            PublicationPaymentInitiateResponseSerializer(data).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Properties"],
        summary="Vérifier et confirmer le paiement de publication",
        description=(
            "Vérifie auprès de la passerelle de paiement le statut réel du paiement associé à cette propriété. "
            "Si le paiement est confirmé, son statut passe à PAID de manière idempotente et atomique."
        ),
        request=None,
        responses={
            200: PublicationPaymentVerifyResponseSerializer,
            400: OpenApiTypes.OBJECT,
            401: OpenApiTypes.OBJECT,
            403: OpenApiTypes.OBJECT,
            404: OpenApiTypes.OBJECT,
            502: OpenApiTypes.OBJECT,
        },
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="verify-publication-payment",
        permission_classes=(IsAuthenticated, PropertyPermission),
    )
    def verify_publication_payment(self, request, pk=None):
        property_obj = self.get_object()

        # 1. Check if there is already a confirmed payment for this property (Idempotence - Cas A)
        paid_payment = (
            Payment.objects.filter(
                property=property_obj,
                status=Payment.Status.PAID,
            )
            .order_by("-paid_at", "-created_at")
            .first()
        )
        if paid_payment:
            data = {
                "payment_id": paid_payment.pk,
                "property_id": property_obj.pk,
                "status": paid_payment.status,
                "order_id": paid_payment.order_id,
                "amount": paid_payment.amount,
                "currency": paid_payment.currency,
                "provider": paid_payment.provider,
                "paid_at": paid_payment.paid_at,
                "provider_transaction_id": paid_payment.provider_transaction_id,
            }
            return Response(
                PublicationPaymentVerifyResponseSerializer(data).data,
                status=status.HTTP_200_OK,
            )

        # 2. Look for an active PENDING payment to verify
        pending_payment = (
            Payment.objects.filter(
                property=property_obj,
                status=Payment.Status.PENDING,
            )
            .order_by("-created_at")
            .first()
        )

        if not pending_payment:
            # Check if there is a FAILED payment to report
            latest_payment = (
                Payment.objects.filter(property=property_obj)
                .order_by("-created_at")
                .first()
            )
            if latest_payment:
                data = {
                    "payment_id": latest_payment.pk,
                    "property_id": property_obj.pk,
                    "status": latest_payment.status,
                    "order_id": latest_payment.order_id,
                    "amount": latest_payment.amount,
                    "currency": latest_payment.currency,
                    "provider": latest_payment.provider,
                    "paid_at": latest_payment.paid_at,
                    "provider_transaction_id": latest_payment.provider_transaction_id,
                }
                return Response(
                    PublicationPaymentVerifyResponseSerializer(data).data,
                    status=status.HTTP_200_OK,
                )
            return Response(
                {"detail": "Aucun paiement de publication trouvé pour cette propriété."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # 3. Call provider service outside database transaction
        service = get_payment_service(pending_payment.provider)
        try:
            confirmed_payment = service.confirm_payment(pending_payment.pk)
        except (KobaraError, MonCashError, MonetizationError) as exc:
            import logging
            logging.getLogger(__name__).error(
                "Échec de la vérification %s pour le paiement %s: %s", pending_payment.provider, pending_payment.order_id, exc
            )
            return Response(
                {"detail": f"Impossible de vérifier le paiement auprès de {pending_payment.provider}. Veuillez réessayer ultérieurement."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        data = {
            "payment_id": confirmed_payment.pk,
            "property_id": property_obj.pk,
            "status": confirmed_payment.status,
            "order_id": confirmed_payment.order_id,
            "amount": confirmed_payment.amount,
            "currency": confirmed_payment.currency,
            "provider": confirmed_payment.provider,
            "paid_at": confirmed_payment.paid_at,
            "provider_transaction_id": confirmed_payment.provider_transaction_id,
        }
        return Response(
            PublicationPaymentVerifyResponseSerializer(data).data,
            status=status.HTTP_200_OK,
        )



    @extend_schema(
        tags=["Properties"],
        summary="Approuver et publier une propriété",
        request=None,
        responses=PropertySerializer,
    )
    @action(detail=True, methods=["post"], url_path="publish")
    def publish(self, request, pk=None):
        property_obj = self.get_object()
        return self._transition(
            property_obj,
            Property.Status.PUBLISHED,
            {Property.Status.PENDING_REVIEW},
        )

    @extend_schema(
        tags=["Properties"],
        summary="Rejeter une propriété en attente d’examen",
        request=None,
        responses=PropertySerializer,
    )
    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        property_obj = self.get_object()
        return self._transition(
            property_obj,
            Property.Status.REJECTED,
            {Property.Status.PENDING_REVIEW},
        )

    @extend_schema(
        tags=["Properties"],
        summary="Suspendre une propriété",
        request=None,
        responses=PropertySerializer,
    )
    @action(detail=True, methods=["post"], url_path="suspend")
    def suspend(self, request, pk=None):
        property_obj = self.get_object()
        allowed = {
            Property.Status.DRAFT,
            Property.Status.PENDING_REVIEW,
            Property.Status.PUBLISHED,
            Property.Status.REJECTED,
        }
        return self._transition(property_obj, Property.Status.SUSPENDED, allowed)

    @extend_schema(
        tags=["Properties"],
        summary="Archiver une propriété",
        request=None,
        responses=PropertySerializer,
    )
    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, pk=None):
        property_obj = self.get_object()
        allowed = set(Property.Status.values) - {Property.Status.ARCHIVED}
        return self._transition(property_obj, Property.Status.ARCHIVED, allowed)

    @extend_schema(
        tags=["Properties"],
        summary="Marquer une propriété publiée comme louée",
        request=None,
        responses=PropertySerializer,
    )
    @action(detail=True, methods=["post"], url_path="mark-rented")
    def mark_rented(self, request, pk=None):
        property_obj = self.get_object()
        return self._transition(
            property_obj,
            Property.Status.RENTED,
            {Property.Status.PUBLISHED},
        )
