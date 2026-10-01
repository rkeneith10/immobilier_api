from django.conf import settings
from django.db import models

from apps.common.models import UUIDModel


class Notification(UUIDModel):
    class Type(models.TextChoices):
        PROPERTY_APPROVED = "PROPERTY_APPROVED", "Property approved"
        PROPERTY_REJECTED = "PROPERTY_REJECTED", "Property rejected"
        NEW_INQUIRY = "NEW_INQUIRY", "New inquiry"
        NEW_VISIT_REQUEST = "NEW_VISIT_REQUEST", "New visit request"
        VISIT_ACCEPTED = "VISIT_ACCEPTED", "Visit accepted"
        VISIT_REJECTED = "VISIT_REJECTED", "Visit rejected"
        NEW_VERIFICATION = "NEW_VERIFICATION", "New owner verification"
        PROPERTY_REPORTED = "PROPERTY_REPORTED", "Property reported"
        NEW_OWNER_REQUEST = "NEW_OWNER_REQUEST", "New owner request"
        OWNER_REQUEST_APPROVED = "OWNER_REQUEST_APPROVED", "Owner request approved"
        OWNER_REQUEST_REJECTED = "OWNER_REQUEST_REJECTED", "Owner request rejected"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    type = models.CharField(max_length=32, choices=Type.choices, db_index=True)
    title = models.CharField(max_length=180)
    message = models.TextField()
    data = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("user", "is_read", "-created_at"), name="notif_user_read_created_idx"),
            models.Index(fields=("user", "-created_at"), name="notification_user_created_idx"),
        ]

    def __str__(self):
        return f"{self.type} for {self.user_id}"
