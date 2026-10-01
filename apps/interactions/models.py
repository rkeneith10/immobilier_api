from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.common.models import UUIDModel, UUIDTimestampedModel


class Favorite(UUIDModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favorites")
    property = models.ForeignKey(
        "properties.Property", on_delete=models.CASCADE, related_name="favorites"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(fields=("user", "property"), name="favorite_user_property_unique"),
        ]
        indexes = [models.Index(fields=("user", "-created_at"), name="favorite_user_created_idx")]

    def __str__(self):
        return f"{self.user_id} favorited {self.property_id}"


class Inquiry(UUIDTimestampedModel):
    class Status(models.TextChoices):
        NEW = "NEW", "New"
        READ = "READ", "Read"
        RESPONDED = "RESPONDED", "Responded"
        CLOSED = "CLOSED", "Closed"

    property = models.ForeignKey(
        "properties.Property", on_delete=models.CASCADE, related_name="inquiries"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_inquiries"
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="received_inquiries"
    )
    message = models.TextField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.NEW, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("user", "-created_at"), name="inquiry_user_created_idx"),
            models.Index(fields=("owner", "-created_at"), name="inquiry_owner_created_idx"),
        ]

    def __str__(self):
        return f"Inquiry {self.pk} for property {self.property_id}"


class VisitRequest(UUIDTimestampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACCEPTED = "ACCEPTED", "Accepted"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"
        COMPLETED = "COMPLETED", "Completed"

    property = models.ForeignKey(
        "properties.Property", on_delete=models.CASCADE, related_name="visit_requests"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="visit_requests"
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="received_visit_requests"
    )
    requested_date = models.DateField()
    requested_time = models.TimeField()
    message = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("user", "status", "-created_at"), name="visit_user_status_created_idx"),
            models.Index(fields=("owner", "status", "-created_at"), name="visit_owner_status_created_idx"),
            models.Index(fields=("property", "requested_date", "requested_time"), name="visit_property_datetime_idx"),
        ]

    def __str__(self):
        return f"Visit request {self.pk} for property {self.property_id}"


class Report(UUIDTimestampedModel):
    class Reason(models.TextChoices):
        SPAM = "SPAM", "Spam"
        FRAUD = "FRAUD", "Fraud"
        INACCURATE = "INACCURATE", "Inaccurate information"
        HARASSMENT = "HARASSMENT", "Harassment"
        OTHER = "OTHER", "Other"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        REVIEWING = "REVIEWING", "Reviewing"
        RESOLVED = "RESOLVED", "Resolved"
        DISMISSED = "DISMISSED", "Dismissed"

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="submitted_reports"
    )
    property = models.ForeignKey(
        "properties.Property", null=True, blank=True, on_delete=models.CASCADE, related_name="reports"
    )
    reported_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE,
        related_name="received_reports",
    )
    reason = models.CharField(max_length=16, choices=Reason.choices)
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="reviewed_reports",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=(Q(property__isnull=False, reported_user__isnull=True)
                           | Q(property__isnull=True, reported_user__isnull=False)),
                name="report_exactly_one_target",
            ),
            models.CheckConstraint(
                condition=Q(reported_user__isnull=True) | ~Q(reporter=F("reported_user")),
                name="reporter_cannot_report_self",
            ),
        ]
        indexes = [
            models.Index(fields=("status", "-created_at"), name="report_status_created_idx"),
            models.Index(fields=("property", "status"), name="report_property_status_idx"),
            models.Index(fields=("reported_user", "status"), name="report_user_status_idx"),
        ]

    def __str__(self):
        return f"Report {self.pk} ({self.status})"


class PropertyReport(UUIDTimestampedModel):
    class Reason(models.TextChoices):
        FAKE_LISTING = "FAKE_LISTING", "Fake listing"
        WRONG_PRICE = "WRONG_PRICE", "Wrong price"
        PROPERTY_RENTED = "PROPERTY_RENTED", "Property already rented"
        MISLEADING_PHOTOS = "MISLEADING_PHOTOS", "Misleading photos"
        SCAM = "SCAM", "Scam"
        OTHER = "OTHER", "Other"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        REVIEWED = "REVIEWED", "Reviewed"
        DISMISSED = "DISMISSED", "Dismissed"
        ACTION_TAKEN = "ACTION_TAKEN", "Action taken"

    property = models.ForeignKey(
        "properties.Property", on_delete=models.CASCADE, related_name="property_reports"
    )
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="property_reports_submitted"
    )
    reason = models.CharField(max_length=24, choices=Reason.choices)
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="property_reports_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("property", "reported_by", "reason"),
                condition=Q(status__in=("PENDING", "REVIEWED")),
                name="active_property_report_unique",
            ),
        ]
        indexes = [
            models.Index(fields=("status", "-created_at"), name="prop_report_status_created_idx"),
            models.Index(fields=("property", "status"), name="pr_property_status_idx"),
            models.Index(fields=("reported_by", "-created_at"), name="pr_user_created_idx"),
        ]

    def __str__(self):
        return f"Property report {self.pk} ({self.status})"
