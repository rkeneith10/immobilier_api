from django.urls import path

from .owner_request_views import OwnerRequestCreateView, OwnerRequestMeView
from .owner_views import (
    OwnerProfileView, OwnerVerificationListView, OwnerVerificationReviewView,
    OwnerVerificationSubmitView,
)
from .views import LoginView, LogoutView, ProfileView, RefreshView, RegistrationView

app_name = "users"
urlpatterns = [
    path("register/", RegistrationView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("refresh/", RefreshView.as_view(), name="refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", ProfileView.as_view(), name="me"),
    path("owner-requests/", OwnerRequestCreateView.as_view(), name="owner-request-create"),
    path("owner-requests/me/", OwnerRequestMeView.as_view(), name="owner-request-me"),
    path("owner-profile/me/", OwnerProfileView.as_view(), name="owner-profile-me"),
    path("owner-profile/me/verification/", OwnerVerificationSubmitView.as_view(), name="owner-verification-submit"),
    path("owner-verifications/", OwnerVerificationListView.as_view(), name="owner-verification-list"),
    path("owner-verifications/<uuid:pk>/", OwnerVerificationReviewView.as_view(), name="owner-verification-review"),
]

