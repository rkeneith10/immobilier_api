import uuid

from django.conf import settings
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q

from apps.common.models import UUIDTimestampedModel


class Plan(UUIDTimestampedModel):
    class Code(models.TextChoices):
        FREE = "FREE", "Free"
        PRO = "PRO", "Pro"
        BUSINESS = "BUSINESS", "Business"

    code = models.CharField(max_length=12, choices=Code.choices, unique=True)
    name = models.CharField(max_length=80)
    description = models.TextField(blank=True, default="")
    price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(
        max_length=3,
        default="USD",
        validators=[RegexValidator(r"^[A-Z]{3}$", "Utilisez un code devise ISO en trois lettres majuscules.")],
    )
    is_active = models.BooleanField(default=True, db_index=True)
    max_active_properties = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Nombre maximal d’annonces actives. Laisser vide pour une limite illimitée.",
    )
    can_promote_properties = models.BooleanField(
        default=False,
        help_text="Autorise les propriétaires abonnés à créer des promotions de propriétés.",
    )

    class Meta:
        ordering = ("price", "code")
        constraints = [
            models.CheckConstraint(condition=Q(price__gte=0), name="plan_price_nonnegative"),
            models.CheckConstraint(condition=~Q(code="FREE") | Q(price=0), name="free_plan_price_zero"),
            models.CheckConstraint(
                condition=Q(max_active_properties__isnull=True) | Q(max_active_properties__gte=0),
                name="plan_property_limit_nonnegative",
            ),
        ]

    def __str__(self):
        return self.name


class Subscription(UUIDTimestampedModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        CANCELLED = "CANCELLED", "Cancelled"
        EXPIRED = "EXPIRED", "Expired"
        PAST_DUE = "PAST_DUE", "Past due"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="subscriptions")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PAST_DUE, db_index=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True) | Q(ends_at__gt=F("starts_at")),
                name="subscription_end_after_start",
            ),
            models.UniqueConstraint(
                fields=("user",), condition=Q(status="ACTIVE"), name="one_active_subscription_per_user"
            ),
        ]
        indexes = [
            models.Index(fields=("user", "status", "-created_at"), name="subscription_user_status_idx"),
            models.Index(fields=("status", "ends_at"), name="subscription_status_end_idx"),
        ]

    def __str__(self):
        return f"{self.plan.code} subscription for {self.user_id}"


class PropertyPromotion(UUIDTimestampedModel):
    class Type(models.TextChoices):
        FEATURED = "FEATURED", "Featured"
        TOP_SEARCH = "TOP_SEARCH", "Top search"
        HOMEPAGE = "HOMEPAGE", "Homepage"
        BOOST = "BOOST", "Boost"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACTIVE = "ACTIVE", "Active"
        EXPIRED = "EXPIRED", "Expired"
        CANCELLED = "CANCELLED", "Cancelled"

    property = models.ForeignKey(
        "properties.Property", on_delete=models.PROTECT, related_name="promotions"
    )
    promotion_type = models.CharField(max_length=16, choices=Type.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="promotion_end_after_start")
        ]
        indexes = [
            models.Index(fields=("property", "status"), name="promotion_property_status_idx"),
            models.Index(fields=("status", "starts_at", "ends_at"), name="promotion_status_dates_idx"),
        ]

    def __str__(self):
        return f"{self.promotion_type} promotion for {self.property_id}"


def generate_order_id():
    return f"ORD-{uuid.uuid4().hex[:20].upper()}"


class Payment(UUIDTimestampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PAID = "PAID", "Paid"
        FAILED = "FAILED", "Failed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments")
    subscription = models.ForeignKey(
        Subscription, null=True, blank=True, on_delete=models.PROTECT, related_name="payments"
    )
    promotion = models.ForeignKey(
        PropertyPromotion, null=True, blank=True, on_delete=models.PROTECT, related_name="payments"
    )
    property = models.ForeignKey(
        "properties.Property", null=True, blank=True, on_delete=models.PROTECT, related_name="payments"
    )
    order_id = models.CharField(
        max_length=64, unique=True, null=True, blank=True, editable=False
    )
    provider = models.CharField(max_length=40)
    provider_payment_id = models.CharField(max_length=1024, null=True, blank=True)
    provider_transaction_id = models.CharField(max_length=512, null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(
        max_length=3,
        validators=[RegexValidator(r"^[A-Z]{3}$", "Utilisez un code devise ISO en trois lettres majuscules.")],
    )
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING, db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(condition=Q(amount__gte=0), name="payment_amount_nonnegative"),
            models.CheckConstraint(
                condition=(
                    Q(subscription__isnull=False, promotion__isnull=True, property__isnull=True)
                    | Q(subscription__isnull=True, promotion__isnull=False, property__isnull=True)
                    | Q(subscription__isnull=True, promotion__isnull=True, property__isnull=False)
                ),
                name="payment_exactly_one_purchase",
            ),
            models.UniqueConstraint(
                fields=("provider", "provider_payment_id"),
                condition=Q(provider_payment_id__isnull=False) & ~Q(provider_payment_id=""),
                name="provider_payment_reference_unique",
            ),
            models.UniqueConstraint(
                fields=("property",),
                condition=Q(status="PAID", property__isnull=False),
                name="unique_paid_payment_per_property",
            ),
        ]
        indexes = [
            models.Index(fields=("status", "-created_at"), name="payment_status_created_idx"),
            models.Index(fields=("user", "-created_at"), name="payment_user_created_idx"),
            models.Index(fields=("property", "-created_at"), name="payment_property_created_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self.order_id:
            self.order_id = generate_order_id()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.provider} payment {self.order_id} ({self.status})"


def generate_invoice_number():
    return f"INV-{uuid.uuid4().hex[:20].upper()}"


class Invoice(UUIDTimestampedModel):
    class Status(models.TextChoices):
        ISSUED = "ISSUED", "Issued"
        PAID = "PAID", "Paid"
        VOID = "VOID", "Void"

    invoice_number = models.CharField(max_length=24, unique=True, default=generate_invoice_number, editable=False)
    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="invoice")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="invoices")
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(
        max_length=3,
        validators=[RegexValidator(r"^[A-Z]{3}$", "Utilisez un code devise ISO en trois lettres majuscules.")],
    )
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ISSUED, db_index=True)
    issued_at = models.DateTimeField()
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-issued_at",)
        constraints = [models.CheckConstraint(condition=Q(amount__gte=0), name="invoice_amount_nonnegative")]
        indexes = [models.Index(fields=("user", "-issued_at"), name="invoice_user_issued_idx")]

    def __str__(self):
        return self.invoice_number
