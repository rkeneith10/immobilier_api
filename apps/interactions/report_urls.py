from django.urls import path

from .report_views import ReportCreateView

app_name = "reports"
urlpatterns = [path("", ReportCreateView.as_view(), name="create")]
