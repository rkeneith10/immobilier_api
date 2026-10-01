from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.users.owner_request_views import (
    AdminOwnerRequestApproveView,
    AdminOwnerRequestDetailView,
    AdminOwnerRequestListView,
    AdminOwnerRequestRejectView,
)
from apps.users.owner_views import OwnerVerificationReviewView
from .admin_views import (
    AdminAmenityViewSet, AdminDashboardView, AdminLocationViewSet,
    AdminPropertyTransitionView, AdminPropertyTypeViewSet, AdminPropertyViewSet,
    AdminUserDetailView, AdminUserListView, AdminOwnerVerificationListView,
)
from apps.interactions.report_views import (
    AdminPropertyReportDetailView, AdminPropertyReportListView,
    AdminReportDetailView, AdminReportListView,
)
from apps.properties.analytics_views import AdminPropertyAnalyticsView

app_name = "admin_api"
router = DefaultRouter()
router.register("locations", AdminLocationViewSet, basename="admin-location")
router.register("property-types", AdminPropertyTypeViewSet, basename="admin-property-type")
router.register("amenities", AdminAmenityViewSet, basename="admin-amenity")
router.register("properties", AdminPropertyViewSet, basename="admin-property")

urlpatterns = [
    path("dashboard/", AdminDashboardView.as_view(), name="dashboard"),
    path("analytics/", AdminPropertyAnalyticsView.as_view(), name="analytics"),
    path("users/", AdminUserListView.as_view(), name="user-list"),
    path("users/<uuid:pk>/", AdminUserDetailView.as_view(), name="user-detail"),
    path("properties/<uuid:pk>/<str:action>/", AdminPropertyTransitionView.as_view(), name="property-transition"),
    path("owner-verifications/", AdminOwnerVerificationListView.as_view(), name="owner-verification-list"),
    path("owner-verifications/<uuid:pk>/", OwnerVerificationReviewView.as_view(), name="owner-verification-detail"),
    path("owner-requests/", AdminOwnerRequestListView.as_view(), name="owner-request-list"),
    path("owner-requests/<uuid:pk>/", AdminOwnerRequestDetailView.as_view(), name="owner-request-detail"),
    path("owner-requests/<uuid:pk>/approve/", AdminOwnerRequestApproveView.as_view(), name="owner-request-approve"),
    path("owner-requests/<uuid:pk>/reject/", AdminOwnerRequestRejectView.as_view(), name="owner-request-reject"),
    path("reports/", AdminReportListView.as_view(), name="report-list"),
    path("reports/<uuid:pk>/", AdminReportDetailView.as_view(), name="report-detail"),
    path("property-reports/", AdminPropertyReportListView.as_view(), name="property-report-list"),
    path("property-reports/<uuid:pk>/", AdminPropertyReportDetailView.as_view(), name="property-report-detail"),
    path("", include(router.urls)),
]

