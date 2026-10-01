from django.urls import path

from .views import VisitRequestDetailView, VisitRequestListView

app_name = "visit_requests"
urlpatterns = [
    path("", VisitRequestListView.as_view(), name="visit-request-list"),
    path("<uuid:pk>/", VisitRequestDetailView.as_view(), name="visit-request-detail"),
]
