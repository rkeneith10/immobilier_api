from django.urls import path
from .views import MonCashWebhookView

app_name = "monetization"

urlpatterns = [
    path("webhook/", MonCashWebhookView.as_view(), name="moncash-webhook"),
    path("alert/", MonCashWebhookView.as_view(), name="moncash-alert"),
]
