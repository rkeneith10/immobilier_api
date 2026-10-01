from django.contrib.auth.base_user import BaseUserManager
from django.db.models import Q

from apps.common.managers import SoftDeleteQuerySet


class UserManager(BaseUserManager.from_queryset(SoftDeleteQuerySet)):
    """Create and retrieve users by normalized email address."""

    use_in_migrations = True

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)

    def _normalize_email(self, email):
        if not email:
            raise ValueError("An email address is required.")
        return self.normalize_email(email.strip()).lower()

    def get_by_natural_key(self, email):
        return self.get(email__iexact=self._normalize_email(email))

    def create_user(self, email, password=None, **extra_fields):
        email = self._normalize_email(email)
        extra_fields.setdefault("role", "USER")
        extra_fields.setdefault("status", "ACTIVE")
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields["role"] = "ADMIN"
        extra_fields["status"] = "ACTIVE"

        if extra_fields.get("is_staff") is not True:
            raise ValueError("A superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("A superuser must have is_superuser=True.")

        return self.create_user(email, password, **extra_fields)
