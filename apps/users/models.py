from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from apps.common.models import SoftDeleteModel, UUIDTimestampedModel
from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin, SoftDeleteModel):
    class Role(models.TextChoices):
        USER = "USER", "User"
        OWNER = "OWNER", "Owner"
        AGENT = "AGENT", "Agent"
        ADMIN = "ADMIN", "Admin"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        SUSPENDED = "SUSPENDED", "Suspended"
        PENDING = "PENDING", "Pending"

    email = models.EmailField(max_length=254, unique=True)
    phone = models.CharField(max_length=32, null=True, blank=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.USER)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    email_verified = models.BooleanField(default=False)
    phone_verified = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                name="users_user_email_ci_unique",
            ),
            models.UniqueConstraint(
                fields=("phone",),
                condition=Q(phone__isnull=False) & ~Q(phone=""),
                name="users_user_phone_unique_set",
            ),
        ]

    @property
    def is_active(self):
        """Integrate account status and soft deletion with Django auth."""
        return self.status == self.Status.ACTIVE and not self.is_deleted

    def __str__(self):
        return self.email


class OwnerProfile(UUIDTimestampedModel):
    class VerificationStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        VERIFIED = "VERIFIED", "Verified"
        REJECTED = "REJECTED", "Rejected"

    user = models.OneToOneField(
        "users.User", on_delete=models.CASCADE, related_name="owner_profile"
    )
    display_name = models.CharField(max_length=160)
    business_name = models.CharField(max_length=200, null=True, blank=True)
    description = models.TextField(blank=True, default="")
    verification_status = models.CharField(
        max_length=10, choices=VerificationStatus.choices, default=VerificationStatus.PENDING
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=("verification_status", "-created_at"), name="ownerprof_status_created_idx")]

    def __str__(self):
        return self.display_name


class OwnerVerification(UUIDTimestampedModel):
    """Immutable submission history with administrator review metadata."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        VERIFIED = "VERIFIED", "Verified"
        REJECTED = "REJECTED", "Rejected"

    owner_profile = models.ForeignKey(
        OwnerProfile, on_delete=models.CASCADE, related_name="verification_requests"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    reviewed_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="reviewed_owner_verifications",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("status", "-created_at"), name="ownerverify_status_created_idx")]

    def __str__(self):
        return f"Verification {self.pk} ({self.status})"


class OwnerRequest(UUIDTimestampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    class RequestType(models.TextChoices):
        OWNER = "OWNER", "Owner"
        AGENT = "AGENT", "Agent"

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="owner_requests",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    request_type = models.CharField(
        max_length=16,
        choices=RequestType.choices,
        default=RequestType.OWNER,
        db_index=True,
    )
    full_name = models.CharField(max_length=200)
    phone = models.CharField(max_length=32)
    address = models.CharField(max_length=255, blank=True, default="")
    message = models.TextField(blank=True, default="")
    rejection_reason = models.TextField(blank=True, default="")
    reviewed_by = models.ForeignKey(
        "users.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_owner_requests",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("status", "-created_at"), name="ownerreq_status_created_idx"),
            models.Index(fields=("user", "-created_at"), name="ownerreq_user_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("user",),
                condition=Q(status="PENDING"),
                name="unique_pending_owner_request_per_user",
            ),
        ]

    def __str__(self):
        return f"OwnerRequest {self.pk} by {self.user.email} ({self.status})"

