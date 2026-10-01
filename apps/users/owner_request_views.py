from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.admin_views import AdminPagination
from apps.common.permissions import AdminOnly
from .models import OwnerRequest
from .owner_request_serializers import (
    AdminOwnerRequestRejectSerializer,
    AdminOwnerRequestSerializer,
    OwnerRequestCreateSerializer,
    OwnerRequestSerializer,
)
from .permissions import HasActiveAccount, IsStandardUser
from .services import approve_owner_request, reject_owner_request, submit_owner_request


@extend_schema_view(
    get=extend_schema(
        tags=["Owner Requests"],
        summary="Lister ses demandes pour devenir propriétaire",
        responses={200: OwnerRequestSerializer(many=True)},
    ),
    post=extend_schema(
        tags=["Owner Requests"],
        summary="Soumettre une demande pour devenir propriétaire",
        request=OwnerRequestCreateSerializer,
        responses={201: OwnerRequestSerializer},
    ),
)
class OwnerRequestCreateView(generics.GenericAPIView):
    serializer_class = OwnerRequestCreateSerializer
    queryset = OwnerRequest.objects.all()

    def get_permissions(self):
        if self.request.method == "POST":
            return [HasActiveAccount(), IsStandardUser()]
        return [HasActiveAccount()]

    def get(self, request):
        requests = OwnerRequest.objects.filter(user=request.user).order_by("-created_at")
        serializer = OwnerRequestSerializer(requests, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        req = submit_owner_request(
            user=request.user,
            full_name=serializer.validated_data["full_name"],
            phone=serializer.validated_data["phone"],
            address=serializer.validated_data.get("address", ""),
            message=serializer.validated_data.get("message", ""),
            request_type=serializer.validated_data.get("request_type", OwnerRequest.RequestType.OWNER),
        )
        return Response(OwnerRequestSerializer(req).data, status=status.HTTP_201_CREATED)


@extend_schema(
    tags=["Owner Requests"],
    summary="Consulter sa demande actuelle / son statut",
    responses={200: OwnerRequestSerializer, 404: dict},
)
class OwnerRequestMeView(APIView):
    permission_classes = (HasActiveAccount,)

    def get(self, request):
        req = OwnerRequest.objects.filter(user=request.user).order_by("-created_at").first()
        if not req:
            raise ValidationError({"detail": "Aucune demande trouvée."})
        return Response(OwnerRequestSerializer(req).data)


@extend_schema(
    tags=["Admin Owner Requests"],
    summary="Lister toutes les demandes pour devenir propriétaire (ADMIN)",
    responses={200: AdminOwnerRequestSerializer(many=True)},
)
class AdminOwnerRequestListView(generics.ListAPIView):
    serializer_class = AdminOwnerRequestSerializer
    permission_classes = (AdminOnly,)
    pagination_class = AdminPagination
    queryset = OwnerRequest.objects.select_related("user", "reviewed_by").all()
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ("status", "request_type")
    search_fields = ("full_name", "phone", "user__email", "user__first_name", "user__last_name")
    ordering_fields = ("created_at", "reviewed_at", "status", "request_type")
    ordering = ("-created_at",)


@extend_schema(
    tags=["Admin Owner Requests"],
    summary="Consulter le détail d'une demande (ADMIN)",
    responses={200: AdminOwnerRequestSerializer},
)
class AdminOwnerRequestDetailView(generics.RetrieveAPIView):
    serializer_class = AdminOwnerRequestSerializer
    permission_classes = (AdminOnly,)
    queryset = OwnerRequest.objects.select_related("user", "reviewed_by").all()


@extend_schema(
    tags=["Admin Owner Requests"],
    summary="Approuver une demande pour devenir propriétaire (ADMIN)",
    request=None,
    responses={200: AdminOwnerRequestSerializer},
)
class AdminOwnerRequestApproveView(APIView):
    permission_classes = (AdminOnly,)

    def post(self, request, pk):
        req = approve_owner_request(request_id=pk, reviewer=request.user)
        return Response(AdminOwnerRequestSerializer(req).data, status=status.HTTP_200_OK)


@extend_schema(
    tags=["Admin Owner Requests"],
    summary="Refuser une demande pour devenir propriétaire avec motif (ADMIN)",
    request=AdminOwnerRequestRejectSerializer,
    responses={200: AdminOwnerRequestSerializer},
)
class AdminOwnerRequestRejectView(APIView):
    permission_classes = (AdminOnly,)

    def post(self, request, pk):
        serializer = AdminOwnerRequestRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        req = reject_owner_request(
            request_id=pk,
            reviewer=request.user,
            rejection_reason=serializer.validated_data["rejection_reason"],
        )
        return Response(AdminOwnerRequestSerializer(req).data, status=status.HTTP_200_OK)
