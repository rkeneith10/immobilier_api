from django.urls import path

from .views import InquiryDetailView, InquiryListView

app_name = "inquiries"
urlpatterns = [
    path("", InquiryListView.as_view(), name="inquiry-list"),
    path("<uuid:pk>/", InquiryDetailView.as_view(), name="inquiry-detail"),
]
