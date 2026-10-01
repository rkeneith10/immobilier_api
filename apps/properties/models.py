from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower

from apps.common.models import SoftDeleteModel, UUIDModel, UUIDTimestampedModel


class Amenity(UUIDTimestampedModel):
    """Reusable, administrator-managed property amenity."""

    name = models.CharField(max_length=100, db_index=True)
    slug = models.SlugField(max_length=120)
    icon = models.CharField(max_length=100, null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(Lower("name"), name="amenity_name_ci_unique"),
            models.UniqueConstraint(Lower("slug"), name="amenity_slug_ci_unique"),
        ]

    def __str__(self):
        return self.name


class PropertyType(UUIDTimestampedModel):
    """Reusable, database-backed category for property listings."""

    name = models.CharField(max_length=100, db_index=True)
    slug = models.SlugField(max_length=120)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(Lower("name"), name="property_type_name_ci_uniq"),
            models.UniqueConstraint(Lower("slug"), name="property_type_slug_ci_uniq"),
        ]

    def __str__(self):
        return self.name


class Property(SoftDeleteModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PENDING_REVIEW = "PENDING_REVIEW", "Pending review"
        PUBLISHED = "PUBLISHED", "Published"
        REJECTED = "REJECTED", "Rejected"
        RENTED = "RENTED", "Rented"
        ARCHIVED = "ARCHIVED", "Archived"
        SUSPENDED = "SUSPENDED", "Suspended"

    class ListingType(models.TextChoices):
        RENT = "RENT", "Rent"
        SALE = "SALE", "Sale"

    class RentalPeriod(models.TextChoices):
        MONTH = "MONTH", "Month"
        YEAR = "YEAR", "Year"

    class AreaUnit(models.TextChoices):
        SQM = "SQM", "Square metres"
        SQFT = "SQFT", "Square feet"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="properties",
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220)
    description = models.TextField(blank=True)
    property_type = models.ForeignKey(
        "properties.PropertyType",
        on_delete=models.PROTECT,
        related_name="properties",
    )
    amenities = models.ManyToManyField(
        "properties.Amenity",
        through="properties.PropertyAmenity",
        related_name="properties",
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    listing_type = models.CharField(max_length=8, choices=ListingType.choices, db_index=True)
    rental_period = models.CharField(
        max_length=8,
        choices=RentalPeriod.choices,
        default=RentalPeriod.MONTH,
        null=True,
        blank=True,
        db_index=True,
    )
    price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    currency = models.CharField(
        max_length=3,
        validators=[RegexValidator(r"^[A-Z]{3}$", "Utilisez un code devise ISO en trois lettres majuscules.")],
    )
    bedrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    bathrooms = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.0"))],
    )
    parking_spaces = models.PositiveSmallIntegerField(null=True, blank=True)
    area = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    area_unit = models.CharField(max_length=8, choices=AreaUnit.choices, default=AreaUnit.SQM)
    furnished = models.BooleanField(default=False)
    location = models.ForeignKey(
        "locations.Location",
        on_delete=models.PROTECT,
        related_name="properties",
    )
    address = models.CharField(max_length=255, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    is_featured = models.BooleanField(default=False, db_index=True)
    featured_until = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(Lower("slug"), name="property_slug_ci_unique"),
            models.CheckConstraint(condition=Q(price__gte=0), name="property_price_nonnegative"),
            models.CheckConstraint(
                condition=Q(bathrooms__isnull=True) | Q(bathrooms__gte=0),
                name="property_bathrooms_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(area__isnull=True) | Q(area__gte=0),
                name="property_area_nonnegative",
            ),
            models.CheckConstraint(
                condition=(
                    Q(latitude__isnull=True, longitude__isnull=True)
                    | Q(
                        latitude__isnull=False,
                        longitude__isnull=False,
                        latitude__gte=-90,
                        latitude__lte=90,
                        longitude__gte=-180,
                        longitude__lte=180,
                    )
                ),
                name="property_coordinates_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(expires_at__isnull=True)
                    | Q(published_at__isnull=True)
                    | Q(expires_at__gt=F("published_at"))
                ),
                name="property_expiry_after_publish",
            ),
        ]
        indexes = [
            models.Index(fields=("owner", "status"), name="property_owner_status_idx"),
            models.Index(fields=("property_type", "status"), name="property_type_status_idx"),
            models.Index(fields=("location", "status"), name="property_location_status_idx"),
            models.Index(fields=("status", "created_at"), name="property_status_created_idx"),
            models.Index(fields=("status", "listing_type"), name="property_status_listing_idx"),
            models.Index(fields=("status", "price"), name="property_status_price_idx"),
            models.Index(fields=("status", "bedrooms"), name="property_status_bedrooms_idx"),
            models.Index(fields=("status", "bathrooms"), name="property_status_bathrooms_idx"),
            models.Index(fields=("status", "area"), name="property_status_area_idx"),
        ]

    def __str__(self):
        return self.title


class PropertyImage(UUIDTimestampedModel):
    """Cloud-hosted image belonging to a property listing."""

    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="images")
    url = models.URLField(max_length=2048)
    public_id = models.CharField(max_length=255, unique=True)
    alt_text = models.CharField(max_length=255, blank=True)
    sort_order = models.PositiveIntegerField()
    is_primary = models.BooleanField(default=False)

    class Meta:
        ordering = ("sort_order", "created_at")
        constraints = [
            models.UniqueConstraint(fields=("property", "sort_order"), name="property_image_order_unique"),
            models.UniqueConstraint(
                fields=("property",), condition=Q(is_primary=True), name="property_single_primary_image"
            ),
        ]
        indexes = [models.Index(fields=("property", "sort_order"), name="property_image_sort_idx")]

    def __str__(self):
        return f"Image {self.sort_order} for {self.property_id}"


class PropertyView(UUIDModel):
    """One counted visit to a published property detail page."""

    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="view_events")
    viewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="property_view_events",
    )
    viewed_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=("property", "viewed_at"), name="property_viewed_at_idx")]

    def __str__(self):
        return f"View of property {self.property_id}"


class PropertyAmenity(UUIDTimestampedModel):
    """Join model recording amenities selected for a property."""

    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="property_amenities")
    amenity = models.ForeignKey(Amenity, on_delete=models.CASCADE, related_name="property_amenities")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("property", "amenity"), name="property_amenity_unique"),
        ]
        indexes = [
            models.Index(fields=("property", "amenity"), name="property_amenity_pair_idx"),
            models.Index(fields=("amenity", "property"), name="amenity_property_pair_idx"),
        ]

    def __str__(self):
        return f"{self.amenity} on {self.property}"
