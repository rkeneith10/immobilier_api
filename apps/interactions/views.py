from drf_spectacular.utils import extend_schema, extend_schema_view
from django.db.models import Q
from rest_framework import generics
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.properties.models import Property
from apps.properties.pagination import PropertyPagination
from .models import Favorite, Inquiry, VisitRequest
from .serializers import (
    FavoriteSerializer,
    InquirySerializer,
    VisitRequestSerializer,
    VisitRequestTransitionSerializer,
)
from .services import (
    InvalidVisitRequestTransition,
    VisitRequestActionDenied,
    transition_visit_request,
)


def visible_inquiries_for(user):
    queryset = Inquiry.objects.select_related("property", "user", "owner")
    if getattr(user, "role", None) == "ADMIN":
        return queryset
    return queryset.filter(Q(user=user) | Q(owner=user))


def visible_visit_requests_for(user):
    queryset = VisitRequest.objects.select_related("property", "user", "owner")
    if getattr(user, "role", None) == "ADMIN":
        return queryset
    return queryset.filter(Q(user=user) | Q(owner=user))


@extend_schema_view(
    get=extend_schema(
        tags=["Favorites"],
        summary="Lister les favoris de l’utilisateur authentifié",
        description="Ne retourne que les favoris appartenant à l’utilisateur connecté et toujours publiés.",
        responses=FavoriteSerializer(many=True),
    )
)
class FavoriteListView(generics.ListAPIView):
    serializer_class = FavoriteSerializer
    permission_classes = (IsAuthenticated,)
    pagination_class = PropertyPagination

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Favorite.objects.none()
        return (
            Favorite.objects.filter(
                user=self.request.user,
                property__status=Property.Status.PUBLISHED,
                property__is_deleted=False,
            )
            .select_related(
                "property",
                "property__owner",
                "property__property_type",
                "property__location",
            )
            .prefetch_related("property__amenities")
        )


@extend_schema_view(
    get=extend_schema(
        tags=["Inquiries"],
        summary="Lister les inquiries accessibles à l’utilisateur",
        description="Retourne les inquiries envoyées par l’utilisateur ou reçues en tant que propriétaire ; ADMIN voit tout.",
    )
)
class InquiryListView(generics.ListAPIView):
    serializer_class = InquirySerializer
    permission_classes = (IsAuthenticated,)
    pagination_class = PropertyPagination

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Inquiry.objects.none()
        return visible_inquiries_for(self.request.user)


@extend_schema_view(
    get=extend_schema(tags=["Inquiries"], summary="Consulter une inquiry accessible"),
    patch=extend_schema(
        tags=["Inquiries"],
        summary="Modifier le statut d’une inquiry",
        description="Le propriétaire de l’annonce ou ADMIN peut gérer le statut ; l’auteur peut uniquement la fermer.",
    ),
)
class InquiryDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = InquirySerializer
    permission_classes = (IsAuthenticated,)
    http_method_names = ("get", "patch", "head", "options")

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Inquiry.objects.none()
        return visible_inquiries_for(self.request.user)


@extend_schema_view(
    get=extend_schema(
        tags=["Visit requests"],
        summary="Lister les demandes de visite accessibles",
        description="Le demandeur voit ses demandes, le propriétaire celles de ses annonces et ADMIN peut toutes les consulter.",
    )
)
class VisitRequestListView(generics.ListAPIView):
    serializer_class = VisitRequestSerializer
    permission_classes = (IsAuthenticated,)
    pagination_class = PropertyPagination

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return VisitRequest.objects.none()
        return visible_visit_requests_for(self.request.user)


@extend_schema_view(
    get=extend_schema(tags=["Visit requests"], summary="Consulter une demande de visite accessible"),
    patch=extend_schema(
        tags=["Visit requests"],
        summary="Modifier le statut d’une demande de visite",
        description=(
            "Le demandeur peut annuler une demande PENDING. Le propriétaire peut l’accepter ou la refuser "
            "lorsqu’elle est PENDING, puis la marquer COMPLETED si elle a été ACCEPTED."
        ),
        request=VisitRequestTransitionSerializer,
        responses=VisitRequestSerializer,
    ),
)
class VisitRequestDetailView(generics.RetrieveAPIView):
    serializer_class = VisitRequestSerializer
    permission_classes = (IsAuthenticated,)
    http_method_names = ("get", "patch", "head", "options")

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return VisitRequest.objects.none()
        return visible_visit_requests_for(self.request.user)

    def patch(self, request, *args, **kwargs):
        visit_request = self.get_object()
        transition_serializer = VisitRequestTransitionSerializer(data=request.data)
        transition_serializer.is_valid(raise_exception=True)
        try:
            updated = transition_visit_request(
                visit_request.pk,
                request.user,
                transition_serializer.validated_data["status"],
            )
        except VisitRequestActionDenied as exc:
            raise PermissionDenied(str(exc)) from exc
        except InvalidVisitRequestTransition as exc:
            raise ValidationError({"status": str(exc)}) from exc
        return Response(VisitRequestSerializer(updated).data)
