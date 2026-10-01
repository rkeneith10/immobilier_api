from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db.models import Q
from django_filters.rest_framework import DjangoFilterBackend

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
        request=None,
        responses=PropertySerializer,
    )
    @action(detail=True, methods=["post"], url_path="submit-for-review")
    def submit_for_review(self, request, pk=None):
        property_obj = self.get_object()
        return self._transition(
            property_obj,
            Property.Status.PENDING_REVIEW,
            {Property.Status.DRAFT, Property.Status.REJECTED},
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
