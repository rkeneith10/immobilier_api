from io import BytesIO

import pytest
from PIL import Image
from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

User = get_user_model()
PASSWORD = "Strong-random-passphrase-739!"


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture(autouse=True)
def clear_auth_throttle_cache():
    cache.clear()


def registration_data(**overrides):
    data = {
        "email": "person@example.com",
        "password": PASSWORD,
        "first_name": "First",
        "last_name": "Person",
    }
    data.update(overrides)
    return data


def create_user(**overrides):
    data = {"email": "person@example.com", "password": PASSWORD}
    data.update(overrides)
    return User.objects.create_user(**data)


@pytest.mark.django_db
def test_registration_creates_user_and_never_returns_password(api_client):
    response = api_client.post("/api/auth/register/", registration_data(), format="json")

    assert response.status_code == 201
    assert response.data["email"] == "person@example.com"
    assert response.data["role"] == User.Role.USER
    assert response.data["status"] == User.Status.ACTIVE
    assert "password" not in response.data
    user = User.objects.get(email="person@example.com")
    assert user.check_password(PASSWORD)
    assert user.email_verified is False
    assert user.phone_verified is False


@pytest.mark.django_db
def test_registration_rejects_duplicate_email_case_insensitively(api_client):
    create_user(email="person@example.com")

    response = api_client.post(
        "/api/auth/register/",
        registration_data(email="PERSON@EXAMPLE.COM"),
        format="json",
    )

    assert response.status_code == 400
    assert "email" in response.data


@pytest.mark.django_db
def test_registration_rejects_duplicate_phone(api_client):
    create_user(phone="+50900000000")

    response = api_client.post(
        "/api/auth/register/",
        registration_data(phone="+50900000000"),
        format="json",
    )

    assert response.status_code == 400
    assert "phone" in response.data


@pytest.mark.django_db
def test_registration_rejects_invalid_password(api_client):
    response = api_client.post(
        "/api/auth/register/",
        registration_data(password="123"),
        format="json",
    )

    assert response.status_code == 400
    assert "password" in response.data
    assert not User.objects.filter(email="person@example.com").exists()


@pytest.mark.django_db
def test_registration_cannot_assign_privileged_role(api_client):
    response = api_client.post(
        "/api/auth/register/",
        registration_data(role="ADMIN"),
        format="json",
    )

    assert response.status_code == 400
    assert not User.objects.filter(email="person@example.com").exists()


@pytest.mark.django_db
def test_login_returns_jwt_and_safe_user_data(api_client):
    user = create_user()

    response = api_client.post(
        "/api/auth/login/",
        {"email": "PERSON@EXAMPLE.COM", "password": PASSWORD},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["access"]
    assert response.data["refresh"]
    assert response.data["user"]["id"] == str(user.pk)
    assert "password" not in response.data
    assert "password" not in response.data["user"]
    assert AccessToken(response.data["access"])["user_id"] == str(user.pk)
    user.refresh_from_db()
    assert user.last_login is not None


@pytest.mark.django_db
def test_login_rejects_invalid_credentials_and_inactive_accounts(api_client):
    user = create_user()
    active_login = api_client.post(
        "/api/auth/login/",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    wrong_password = api_client.post(
        "/api/auth/login/",
        {"email": user.email, "password": "incorrect"},
        format="json",
    )
    assert wrong_password.status_code == 401

    user.status = User.Status.SUSPENDED
    user.save(update_fields=("status", "updated_at"))
    suspended = api_client.post(
        "/api/auth/login/",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    assert suspended.status_code == 401

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {active_login.data['access']}")
    me = api_client.get("/api/auth/me/")
    assert me.status_code == 401
    refresh = api_client.post(
        "/api/auth/refresh/",
        {"refresh": active_login.data["refresh"]},
        format="json",
    )
    assert refresh.status_code == 401


@pytest.mark.django_db
def test_refresh_endpoint_returns_a_new_access_token(api_client):
    create_user()
    login = api_client.post(
        "/api/auth/login/",
        {"email": "person@example.com", "password": PASSWORD},
        format="json",
    )

    response = api_client.post(
        "/api/auth/refresh/",
        {"refresh": login.data["refresh"]},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["access"]


@pytest.mark.django_db
def test_me_requires_jwt_and_returns_current_user(api_client):
    anonymous = api_client.get("/api/auth/me/")
    assert anonymous.status_code == 401

    user = create_user()
    login = api_client.post(
        "/api/auth/login/",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

    response = api_client.get("/api/auth/me/")

    assert response.status_code == 200
    assert response.data["id"] == str(user.pk)
    assert "password" not in response.data


@pytest.mark.django_db
def test_profile_can_be_updated_but_role_cannot(api_client):
    user = create_user()
    login = api_client.post(
        "/api/auth/login/",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

    profile_update = api_client.patch(
        "/api/auth/me/",
        {"first_name": "Updated", "phone": "+50911111111"},
        format="json",
    )
    assert profile_update.status_code == 200
    assert profile_update.data["first_name"] == "Updated"
    assert profile_update.data["phone"] == "+50911111111"

    role_update = api_client.patch(
        "/api/auth/me/",
        {"role": "ADMIN"},
        format="json",
    )
    assert role_update.status_code == 400
    user.refresh_from_db()
    assert user.role == User.Role.USER
    assert user.is_staff is False


@pytest.mark.django_db
def test_logout_blacklists_refresh_token(api_client):
    create_user()
    login = api_client.post(
        "/api/auth/login/",
        {"email": "person@example.com", "password": PASSWORD},
        format="json",
    )
    refresh_token = login.data["refresh"]

    logout = api_client.post(
        "/api/auth/logout/",
        {"refresh": refresh_token},
        format="json",
    )
    assert logout.status_code == 205

    refresh = api_client.post(
        "/api/auth/refresh/",
        {"refresh": refresh_token},
        format="json",
    )
    assert refresh.status_code == 401


@pytest.mark.django_db
def test_login_is_rate_limited_to_reduce_password_brute_force(api_client):
    create_user()
    responses = [
        api_client.post(
            "/api/auth/login/",
            {"email": "person@example.com", "password": "wrong-password"},
            format="json",
        )
        for _ in range(6)
    ]
    assert [response.status_code for response in responses] == [401, 401, 401, 401, 401, 429]


@pytest.mark.django_db
def test_refresh_rotates_and_revokes_previous_refresh_token(api_client):
    create_user()
    login = api_client.post(
        "/api/auth/login/", {"email": "person@example.com", "password": PASSWORD}, format="json"
    )
    refreshed = api_client.post("/api/auth/refresh/", {"refresh": login.data["refresh"]}, format="json")
    assert refreshed.status_code == 200
    assert refreshed.data["refresh"] != login.data["refresh"]
    reused = api_client.post("/api/auth/refresh/", {"refresh": login.data["refresh"]}, format="json")
    assert reused.status_code == 401


@pytest.mark.django_db
@override_settings(PROFILE_IMAGE_MAX_UPLOAD_SIZE=32)
def test_profile_avatar_upload_obeys_size_limit(api_client):
    user = create_user()
    api_client.force_authenticate(user)
    image_bytes = BytesIO()
    Image.new("RGB", (16, 16), color="red").save(image_bytes, format="PNG")
    assert len(image_bytes.getvalue()) > 32
    avatar = SimpleUploadedFile("avatar.png", image_bytes.getvalue(), content_type="image/png")
    response = api_client.patch("/api/auth/me/", {"avatar": avatar}, format="multipart")
    assert response.status_code == 400
    assert "avatar" in response.data
