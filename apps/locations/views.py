from django.db.models.deletion import ProtectedError
from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, extend_schema_view

from .filters import LocationFilter
from .models import Location
from .pagination import LocationPagination
from .permissions import IsAdminOrReadOnly
from .serializers import LocationSerializer


@extend_schema_view(
    list=extend_schema(tags=["Locations"], summary="Lister les localisations"),
    retrieve=extend_schema(tags=["Locations"], summary="Consulter une localisation"),
    create=extend_schema(tags=["Locations"], summary="Créer une localisation"),
    update=extend_schema(tags=["Locations"], summary="Modifier une localisation"),
    partial_update=extend_schema(tags=["Locations"], summary="Modifier une localisation"),
    destroy=extend_schema(tags=["Locations"], summary="Supprimer une localisation"),
)
class LocationViewSet(viewsets.ModelViewSet):
    serializer_class = LocationSerializer
    permission_classes = (IsAdminOrReadOnly,)
    pagination_class = LocationPagination
    filterset_class = LocationFilter
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    search_fields = ("name", "slug")
    ordering_fields = ("name", "type", "created_at", "updated_at")
    ordering = ("type", "name")

    def get_queryset(self):
        queryset = Location.objects.select_related("parent").all()
        user = self.request.user
        if not (
            user.is_authenticated
            and user.is_active
            and getattr(user, "role", None) == "ADMIN"
        ):
            queryset = queryset.filter(is_active=True)
        return queryset

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError as exc:
            raise ValidationError(
                {"detail": "Cette localisation possède des enfants et ne peut pas être supprimée."}
            ) from exc
