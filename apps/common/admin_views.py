from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, serializers, status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend

from apps.interactions.models import PropertyReport, Report
from apps.locations.models import Location
from apps.locations.views import LocationViewSet
from apps.properties.models import Amenity, Property, PropertyType
from apps.properties.serializers import PropertySerializer
from apps.properties.services import InvalidPropertyTransition, transition_property
from apps.properties.views import AmenityViewSet, PropertyTypeViewSet
from apps.users.models import OwnerVerification
from apps.users.owner_views import OwnerVerificationListView
from .permissions import AdminOnly

User = get_user_model()


class AdminPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


class AdminUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id", "email", "phone", "first_name", "last_name", "role", "status",
            "email_verified", "phone_verified", "created_at", "updated_at",
        )
        read_only_fields = (
            "id", "email", "phone", "first_name", "last_name", "email_verified",
            "phone_verified", "created_at", "updated_at",
        )


class AdminDashboardSerializer(serializers.Serializer):
    total_users = serializers.IntegerField()
    active_users = serializers.IntegerField()
    total_owners = serializers.IntegerField()
    total_properties = serializers.IntegerField()
    published_properties = serializers.IntegerField()
    pending_properties = serializers.IntegerField()
    rented_properties = serializers.IntegerField()
    pending_verifications = serializers.IntegerField()
    reports_count = serializers.IntegerField()


class AdminUserListView(generics.ListAPIView):
    serializer_class = AdminUserSerializer
    permission_classes = (AdminOnly,)
    pagination_class = AdminPagination
    queryset = User.objects.all()
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ("role", "status")
    search_fields = ("email", "first_name", "last_name", "phone")
    ordering_fields = ("created_at", "email", "status", "role")
    ordering = ("-created_at",)


class AdminUserDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = AdminUserSerializer
    permission_classes = (AdminOnly,)
    queryset = User.objects.all()
    http_method_names = ("get", "patch", "head", "options")

    def perform_update(self, serializer):
        if serializer.instance.pk == self.request.user.pk and any(
            key in serializer.validated_data for key in ("role", "status")
        ):
            raise ValidationError({"detail": "Vous ne pouvez pas modifier votre propre rôle ou statut."})
        serializer.save()


class AdminDashboardView(APIView):
    permission_classes = (AdminOnly,)

    @extend_schema(tags=["Admin"], summary="Indicateurs de modération", responses=AdminDashboardSerializer)
    def get(self, request):
        user_counts = User.objects.aggregate(
            total_users=Count("id"),
            active_users=Count("id", filter=Q(status=User.Status.ACTIVE)),
            total_owners=Count("id", filter=Q(role=User.Role.OWNER)),
        )
        property_counts = Property.objects.aggregate(
            total_properties=Count("id"),
            published_properties=Count("id", filter=Q(status=Property.Status.PUBLISHED)),
            pending_properties=Count("id", filter=Q(status=Property.Status.PENDING_REVIEW)),
            rented_properties=Count("id", filter=Q(status=Property.Status.RENTED)),
        )
        data = {
            **user_counts,
            **property_counts,
            "pending_verifications": OwnerVerification.objects.filter(status=OwnerVerification.Status.PENDING).count(),
            "reports_count": Report.objects.count() + PropertyReport.objects.count(),
        }
        return Response(data)


class AdminLocationViewSet(LocationViewSet):
    permission_classes = (AdminOnly,)


class AdminPropertyTypeViewSet(PropertyTypeViewSet):
    permission_classes = (AdminOnly,)


class AdminAmenityViewSet(AmenityViewSet):
    permission_classes = (AdminOnly,)


class AdminOwnerVerificationListView(OwnerVerificationListView):
    pagination_class = AdminPagination


class AdminPropertyViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PropertySerializer
    permission_classes = (AdminOnly,)
    pagination_class = AdminPagination
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ("status", "listing_type", "property_type", "location", "owner")
    search_fields = ("title", "description", "slug")
    ordering_fields = ("created_at", "updated_at", "price", "status")
    ordering = ("-created_at",)

    def get_queryset(self):
        return Property.objects.select_related("owner", "property_type", "location").prefetch_related(
            "amenities", "images"
        )


class AdminPropertyTransitionView(APIView):
    permission_classes = (AdminOnly,)

    TARGETS = {
        "approve": (Property.Status.PUBLISHED, {Property.Status.PENDING_REVIEW}),
        "publish": (Property.Status.PUBLISHED, {Property.Status.PENDING_REVIEW}),
        "reject": (Property.Status.REJECTED, {Property.Status.PENDING_REVIEW}),
        "suspend": (Property.Status.SUSPENDED, {
            Property.Status.DRAFT, Property.Status.PENDING_REVIEW, Property.Status.PUBLISHED,
            Property.Status.REJECTED,
        }),
        "archive": (Property.Status.ARCHIVED, set(Property.Status.values) - {Property.Status.ARCHIVED}),
        "mark-rented": (Property.Status.RENTED, {Property.Status.PUBLISHED}),
    }

    @extend_schema(tags=["Admin"], summary="Effectuer une transition de modération sur une propriété", request=None, responses=PropertySerializer)
    def post(self, request, pk, action):
        if action not in self.TARGETS:
            raise ValidationError({"action": "Action de modération inconnue."})
        target, allowed_from = self.TARGETS[action]
        property_obj = get_object_or_404(Property.objects.all(), pk=pk)
        try:
            updated = transition_property(property_obj.pk, target, allowed_from)
        except InvalidPropertyTransition as exc:
            raise ValidationError({"status": str(exc)}) from exc
        return Response(PropertySerializer(updated, context={"request": request}).data)
