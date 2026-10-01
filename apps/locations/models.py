from django.db import models
from django.db.models import F, Q

from apps.common.models import UUIDTimestampedModel


class Location(UUIDTimestampedModel):
    class Type(models.TextChoices):
        COUNTRY = "COUNTRY", "Country"
        DEPARTMENT = "DEPARTMENT", "Department"
        CITY = "CITY", "City"
        COMMUNE = "COMMUNE", "Commune"
        NEIGHBORHOOD = "NEIGHBORHOOD", "Neighborhood"
        AREA = "AREA", "Area"

    name = models.CharField(max_length=160, db_index=True)
    slug = models.SlugField(max_length=180, db_index=True)
    type = models.CharField(max_length=16, choices=Type.choices, db_index=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="children",
        null=True,
        blank=True,
    )
    description = models.TextField(blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("type", "name")
        constraints = [
            models.UniqueConstraint(
                models.functions.Lower("slug"),
                "parent",
                name="location_sibling_slug_ci_uniq",
            ),
            models.UniqueConstraint(
                models.functions.Lower("slug"),
                condition=Q(parent__isnull=True),
                name="location_root_slug_ci_uniq",
            ),
            models.CheckConstraint(
                condition=~Q(id=F("parent_id")),
                name="location_not_own_parent",
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
                name="location_coordinates_valid",
            ),
        ]
        indexes = [
            models.Index(fields=("type", "is_active"), name="location_type_active_idx"),
            models.Index(fields=("parent", "is_active"), name="location_parent_active_idx"),
        ]

    def __str__(self):
        return self.name
