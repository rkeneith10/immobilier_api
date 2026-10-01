from django.contrib import admin
from django.urls import include, path
from django.http import JsonResponse
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from apps.properties.analytics_views import OwnerPropertyAnalyticsView


def health_check(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health_check, name="health-check"),
    path("api/auth/", include("apps.users.urls")),
    path("api/locations/", include("apps.locations.urls")),
    path("api/properties/", include("apps.properties.urls")),
    path(
        "api/owner/properties/<uuid:property_id>/analytics/",
        OwnerPropertyAnalyticsView.as_view(),
        name="owner-property-analytics",
    ),
    path("api/favorites/", include("apps.interactions.urls")),
    path("api/inquiries/", include("apps.interactions.inquiries_urls")),
    path("api/visit-requests/", include("apps.interactions.visit_urls")),
    path("api/notifications/", include("apps.notifications.urls")),
    path("api/reports/", include("apps.interactions.report_urls")),
    path("api/admin/", include("apps.common.admin_urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]
