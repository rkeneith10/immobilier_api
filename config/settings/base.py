from datetime import timedelta
from pathlib import Path
import os
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = False
ALLOWED_HOSTS = [host.strip() for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "drf_spectacular",
    "cloudinary_storage",
    "cloudinary",
    "apps.common.apps.CommonConfig",
    "apps.locations.apps.LocationsConfig",
    "apps.users.apps.UsersConfig",
    "apps.properties.apps.PropertiesConfig",
    "apps.interactions.apps.InteractionsConfig",
    "apps.notifications.apps.NotificationsConfig",
    "apps.monetization.apps.MonetizationConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": os.environ["DATABASE_NAME"],
    "USER": os.environ["DATABASE_USER"],
    "PASSWORD": os.environ["DATABASE_PASSWORD"],
    "HOST": os.environ.get("DATABASE_HOST", "localhost"),
    "PORT": os.environ.get("DATABASE_PORT", "5432"),
}}

AUTH_USER_MODEL = "users.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "America/Port-au-Prince"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
        "rest_framework.filters.SearchFilter",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Forwarded client IPs are ignored unless the deployment declares trusted proxies.
    "NUM_PROXIES": int(os.getenv("API_NUM_PROXIES", "0")),
    "DEFAULT_THROTTLE_RATES": {
        "auth_login": "5/minute",
        "auth_register": "5/hour",
        "auth_refresh": "30/minute",
        "auth_logout": "10/minute",
    },
}
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}
SPECTACULAR_SETTINGS = {
    "TITLE": "ImmoPlatform API",
    "DESCRIPTION": "API de la plateforme immobilière ImmoPlatform.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "ENUM_NAME_OVERRIDES": {
        "PropertyStatusEnum": "apps.properties.models.Property.Status",
        "InquiryStatusEnum": "apps.interactions.models.Inquiry.Status",
        "VisitRequestStatusEnum": "apps.interactions.models.VisitRequest.Status",
        "OwnerVerificationStatusEnum": "apps.users.models.OwnerVerification.Status",
        "UserStatusEnum": "apps.users.models.User.Status",
        "ReportStatusEnum": "apps.interactions.models.Report.Status",
        "ReportReasonEnum": "apps.interactions.models.Report.Reason",
        "PropertyReportStatusEnum": "apps.interactions.models.PropertyReport.Status",
        "PropertyReportReasonEnum": "apps.interactions.models.PropertyReport.Reason",
        "OwnerRequestStatusEnum": "apps.users.models.OwnerRequest.Status",
        "OwnerRequestTypeEnum": "apps.users.models.OwnerRequest.RequestType",
    },
}
CORS_ALLOWED_ORIGINS = [origin.strip() for origin in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if origin.strip()]
# API authentication uses bearer JWT headers, not cross-origin cookies.
CORS_ALLOW_CREDENTIALS = False

CLOUDINARY_STORAGE = {
    "CLOUD_NAME": os.getenv("CLOUDINARY_CLOUD_NAME", ""),
    "API_KEY": os.getenv("CLOUDINARY_API_KEY", ""),
    "API_SECRET": os.getenv("CLOUDINARY_API_SECRET", ""),
}
PROPERTY_IMAGE_MAX_UPLOAD_SIZE = int(os.getenv("PROPERTY_IMAGE_MAX_UPLOAD_SIZE", str(10 * 1024 * 1024)))
PROFILE_IMAGE_MAX_UPLOAD_SIZE = int(os.getenv("PROFILE_IMAGE_MAX_UPLOAD_SIZE", str(5 * 1024 * 1024)))
if all(CLOUDINARY_STORAGE.values()):
    STORAGES = {
        "default": {"BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
else:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }

# Publication monetization pricing (HTG)
from decimal import Decimal
PUBLICATION_PRICE_HTG = Decimal(os.getenv("PUBLICATION_PRICE_HTG", "500.00"))
DEFAULT_PUBLICATION_CURRENCY = "HTG"

# MonCash Payment Gateway configuration
MONCASH_MODE = os.getenv("MONCASH_MODE", "sandbox").lower()
MONCASH_CLIENT_ID = os.getenv("MONCASH_CLIENT_ID", "")
MONCASH_CLIENT_SECRET = os.getenv("MONCASH_CLIENT_SECRET", "")
MONCASH_API_URL = os.getenv(
    "MONCASH_API_URL",
    "https://sandbox.moncashbutton.digicelgroup.com/Api"
    if MONCASH_MODE == "sandbox"
    else "https://moncashbutton.digicelgroup.com/Api",
)
MONCASH_GATEWAY_URL = os.getenv(
    "MONCASH_GATEWAY_URL",
    "https://sandbox.moncashbutton.digicelgroup.com/Moncash-middleware"
    if MONCASH_MODE == "sandbox"
    else "https://moncashbutton.digicelgroup.com/Moncash-middleware",
)
MONCASH_TIMEOUT_SECONDS = int(os.getenv("MONCASH_TIMEOUT_SECONDS", "15"))


