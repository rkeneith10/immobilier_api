from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import Location
from .services import validate_parent_assignment


class LocationSerializer(serializers.ModelSerializer):
    parent = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
        allow_null=True,
        required=False,
    )

    class Meta:
        model = Location
        fields = (
            "id",
            "name",
            "slug",
            "type",
            "parent",
            "description",
            "latitude",
            "longitude",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate(self, attrs):
        location = self.instance
        parent = attrs.get("parent", getattr(location, "parent", None))
        slug = attrs.get("slug", getattr(location, "slug", None))

        try:
            validate_parent_assignment(location, parent)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"parent": exc.messages}) from exc

        latitude = attrs.get("latitude", getattr(location, "latitude", None))
        longitude = attrs.get("longitude", getattr(location, "longitude", None))
        if (latitude is None) != (longitude is None):
            raise serializers.ValidationError({
                "latitude": "La latitude et la longitude doivent être renseignées ensemble."
            })
        if latitude is not None and not -90 <= latitude <= 90:
            raise serializers.ValidationError({"latitude": "La latitude doit être comprise entre -90 et 90."})
        if longitude is not None and not -180 <= longitude <= 180:
            raise serializers.ValidationError({"longitude": "La longitude doit être comprise entre -180 et 180."})

        if slug:
            siblings = Location.objects.filter(parent=parent, slug__iexact=slug)
            if location is not None:
                siblings = siblings.exclude(pk=location.pk)
            if siblings.exists():
                raise serializers.ValidationError({"slug": "Ce slug est déjà utilisé à ce niveau."})

        return attrs
