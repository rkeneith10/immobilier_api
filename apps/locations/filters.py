import django_filters

from .models import Location


class LocationFilter(django_filters.FilterSet):
    type = django_filters.ChoiceFilter(choices=Location.Type.choices)
    parent = django_filters.UUIDFilter(field_name="parent_id")
    is_active = django_filters.BooleanFilter()

    class Meta:
        model = Location
        fields = ("type", "parent", "is_active")
