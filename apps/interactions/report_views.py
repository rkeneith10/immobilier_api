from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, extend_schema_view

from apps.common.permissions import AdminOnly
from .models import PropertyReport, Report
from .property_report_services import InvalidPropertyReportTransition, review_property_report
from .report_serializers import (
    PropertyReportReviewSerializer, PropertyReportSerializer,
    ReportCreateSerializer, ReportSerializer,
)


class ReportAdminPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


class ReportCreateView(generics.CreateAPIView):
    serializer_class = ReportCreateSerializer
    permission_classes = (IsAuthenticated,)


class AdminReportListView(generics.ListAPIView):
    serializer_class = ReportSerializer
    permission_classes = (AdminOnly,)
    pagination_class = ReportAdminPagination
    queryset = Report.objects.select_related("reporter", "reported_user", "property", "reviewed_by")
    filterset_fields = ("status", "reason", "property", "reported_user")
    ordering_fields = ("created_at", "updated_at", "status")
    ordering = ("-created_at",)


class AdminReportDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = ReportSerializer
    permission_classes = (AdminOnly,)
    queryset = Report.objects.select_related("reporter", "reported_user", "property", "reviewed_by")
    http_method_names = ("get", "patch", "head", "options")

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            from .report_serializers import AdminReportUpdateSerializer
            return AdminReportUpdateSerializer
        return self.serializer_class

    def perform_update(self, serializer):
        new_status = serializer.validated_data.get("status", serializer.instance.status)
        if new_status == Report.Status.OPEN:
            serializer.save(reviewed_by=None, reviewed_at=None)
        else:
            serializer.save(reviewed_by=self.request.user, reviewed_at=timezone.now())


class AdminPropertyReportListView(generics.ListAPIView):
    serializer_class = PropertyReportSerializer
    permission_classes = (AdminOnly,)
    pagination_class = ReportAdminPagination
    queryset = PropertyReport.objects.select_related("property", "reported_by", "reviewed_by")
    filterset_fields = ("status", "reason", "property", "reported_by")
    ordering_fields = ("created_at", "updated_at", "status")
    ordering = ("-created_at",)


@extend_schema_view(
    get=extend_schema(tags=["Admin property reports"], summary="Consulter un signalement immobilier"),
    patch=extend_schema(
        tags=["Admin property reports"], summary="Traiter un signalement immobilier",
        request=PropertyReportReviewSerializer, responses=PropertyReportSerializer,
    ),
)
class AdminPropertyReportDetailView(generics.GenericAPIView):
    serializer_class = PropertyReportReviewSerializer
    permission_classes = (AdminOnly,)

    def get_queryset(self):
        return PropertyReport.objects.select_related("property", "reported_by", "reviewed_by")

    def get(self, request, pk):
        report = get_object_or_404(self.get_queryset(), pk=pk)
        return Response(PropertyReportSerializer(report).data)

    def patch(self, request, pk):
        report = get_object_or_404(self.get_queryset(), pk=pk)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            report = review_property_report(
                report_id=report.pk,
                reviewer=request.user,
                target_status=serializer.validated_data["status"],
            )
        except InvalidPropertyReportTransition as exc:
            raise ValidationError({"status": "Cette transition de traitement n'est pas autorisée."}) from exc
        return Response(PropertyReportSerializer(report).data, status=status.HTTP_200_OK)
