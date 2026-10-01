import django_filters

from .models import Amenity, Property, PropertyType


class AmenityFilter(django_filters.FilterSet):
    is_active = django_filters.BooleanFilter()

    class Meta:
        model = Amenity
        fields = ("is_active",)


class PropertyTypeFilter(django_filters.FilterSet):
    is_active = django_filters.BooleanFilter()

    class Meta:
        model = PropertyType
        fields = ("is_active",)


class PropertyFilter(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(
        choices=Property.Status.choices, label="Statut de l’annonce"
    )
    owner = django_filters.UUIDFilter(field_name="owner_id", label="Identifiant UUID du propriétaire")
    listing_type = django_filters.ChoiceFilter(
        choices=Property.ListingType.choices, label="Type d’annonce (RENT ou SALE)"
    )
    property_type = django_filters.UUIDFilter(
        field_name="property_type_id", label="Identifiant UUID du type de propriété"
    )
    location = django_filters.UUIDFilter(field_name="location_id", label="Identifiant UUID de la localisation")
    min_price = django_filters.NumberFilter(field_name="price", lookup_expr="gte", label="Prix minimum inclusif")
    max_price = django_filters.NumberFilter(field_name="price", lookup_expr="lte", label="Prix maximum inclusif")
    min_bedrooms = django_filters.NumberFilter(
        field_name="bedrooms", lookup_expr="gte", label="Nombre minimal de chambres"
    )
    max_bedrooms = django_filters.NumberFilter(
        field_name="bedrooms", lookup_expr="lte", label="Nombre maximal de chambres"
    )
    min_bathrooms = django_filters.NumberFilter(
        field_name="bathrooms", lookup_expr="gte", label="Nombre minimal de salles de bain"
    )
    max_bathrooms = django_filters.NumberFilter(
        field_name="bathrooms", lookup_expr="lte", label="Nombre maximal de salles de bain"
    )
    furnished = django_filters.BooleanFilter(label="Propriété meublée")
    is_featured = django_filters.BooleanFilter(label="Annonce mise en avant")

    class Meta:
        model = Property
        fields = (
            "status",
            "owner",
            "listing_type",
            "property_type",
            "location",
            "min_price",
            "max_price",
            "min_bedrooms",
            "max_bedrooms",
            "min_bathrooms",
            "max_bathrooms",
            "furnished",
            "is_featured",
        )
