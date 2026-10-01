import uuid
from django.utils.text import slugify
from rest_framework import serializers

from apps.locations.models import Location
from .image_serializers import PropertyImageSerializer
from .models import Amenity, Property, PropertyType


class AmenitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Amenity
        fields = ("id", "name", "slug", "icon", "is_active", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_name(self, value):
        duplicates = Amenity.objects.filter(name__iexact=value)
        if self.instance is not None:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError("Cet amenity existe déjà.")
        return value

    def validate_slug(self, value):
        duplicates = Amenity.objects.filter(slug__iexact=value)
        if self.instance is not None:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError("Ce slug d’amenity existe déjà.")
        return value


class PropertyTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = PropertyType
        fields = ("id", "name", "slug", "description", "is_active", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_name(self, value):
        duplicates = PropertyType.objects.filter(name__iexact=value)
        if self.instance is not None:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError("Ce nom de type existe déjà.")
        return value

    def validate_slug(self, value):
        duplicates = PropertyType.objects.filter(slug__iexact=value)
        if self.instance is not None:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError("Ce slug de type existe déjà.")
        return value


class PropertySerializer(serializers.ModelSerializer):
    slug = serializers.SlugField(max_length=220, required=False, allow_blank=True)
    property_type = serializers.PrimaryKeyRelatedField(
        queryset=PropertyType.objects.filter(is_active=True)
    )
    location = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.filter(is_active=True)
    )
    amenities = serializers.PrimaryKeyRelatedField(
        queryset=Amenity.objects.filter(is_active=True), many=True, required=False
    )
    images = PropertyImageSerializer(many=True, read_only=True)

    class Meta:
        model = Property
        fields = (
            "id",
            "owner",
            "title",
            "slug",
            "description",
            "property_type",
            "amenities",
            "status",
            "listing_type",
            "rental_period",
            "price",
            "currency",
            "bedrooms",
            "bathrooms",
            "parking_spaces",
            "area",
            "area_unit",
            "furnished",
            "location",
            "address",
            "latitude",
            "longitude",
            "is_featured",
            "featured_until",
            "published_at",
            "expires_at",
            "created_at",
            "updated_at",
            "deleted_at",
            "images",
        )
        read_only_fields = (
            "id",
            "owner",
            "status",
            "is_featured",
            "featured_until",
            "published_at",
            "created_at",
            "updated_at",
            "deleted_at",
            "images",
        )

    def validate(self, attrs):
        protected_fields = {
            "owner",
            "owner_id",
            "status",
            "is_featured",
            "featured_until",
            "published_at",
            "deleted_at",
            "is_deleted",
        }
        attempted = protected_fields.intersection(self.initial_data)
        if attempted:
            raise serializers.ValidationError({
                field: "Ce champ est géré par le workflow et ne peut pas être modifié ici."
                for field in sorted(attempted)
            })

        listing_type = attrs.get("listing_type", getattr(self.instance, "listing_type", Property.ListingType.RENT))
        rental_period = attrs.get("rental_period", getattr(self.instance, "rental_period", None))
        if listing_type == Property.ListingType.RENT:
            if not rental_period:
                attrs["rental_period"] = Property.RentalPeriod.MONTH
        else:
            attrs["rental_period"] = None

        slug = attrs.get("slug")
        if not slug or not str(slug).strip():
            current_slug = getattr(self.instance, "slug", None)
            if current_slug:
                attrs["slug"] = current_slug
            else:
                title = attrs.get("title", getattr(self.instance, "title", ""))
                base_slug = slugify(title) or f"propriete-{uuid.uuid4().hex[:8]}"
                base_slug = base_slug[:200]
                candidate = base_slug
                counter = 1
                qs = Property.all_objects.all()
                if self.instance is not None:
                    qs = qs.exclude(pk=self.instance.pk)
                while qs.filter(slug__iexact=candidate).exists():
                    suffix = f"-{counter}"
                    candidate = f"{base_slug[:200 - len(suffix)]}{suffix}"
                    counter += 1
                attrs["slug"] = candidate
        else:
            duplicates = Property.all_objects.filter(slug__iexact=slug)
            if self.instance is not None:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                raise serializers.ValidationError({"slug": "Ce slug est déjà utilisé."})

        latitude = attrs.get("latitude", getattr(self.instance, "latitude", None))
        longitude = attrs.get("longitude", getattr(self.instance, "longitude", None))
        if (latitude is None) != (longitude is None):
            raise serializers.ValidationError({
                "latitude": "La latitude et la longitude doivent être renseignées ensemble."
            })
        if latitude is not None and not -90 <= latitude <= 90:
            raise serializers.ValidationError({"latitude": "La latitude doit être comprise entre -90 et 90."})
        if longitude is not None and not -180 <= longitude <= 180:
            raise serializers.ValidationError({"longitude": "La longitude doit être comprise entre -180 et 180."})

        published_at = getattr(self.instance, "published_at", None)
        expires_at = attrs.get("expires_at", getattr(self.instance, "expires_at", None))
        if published_at is not None and expires_at is not None and expires_at <= published_at:
            raise serializers.ValidationError({"expires_at": "La date d’expiration doit suivre la publication."})
        return attrs

    def create(self, validated_data):
        validated_data.update(
            status=Property.Status.DRAFT,
            is_featured=False,
            featured_until=None,
            published_at=None,
        )
        return super().create(validated_data)
