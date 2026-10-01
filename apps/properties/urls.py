from django.urls import path
from rest_framework.routers import DefaultRouter

from .image_views import PropertyImageCollectionView, PropertyImageDetailView
from .views import AmenityViewSet, PropertyTypeViewSet, PropertyViewSet

app_name = "properties"
router = DefaultRouter()
router.register("amenities", AmenityViewSet, basename="amenity")
router.register("types", PropertyTypeViewSet, basename="property-type")
router.register("", PropertyViewSet, basename="property")
urlpatterns = router.urls
urlpatterns += [
    path("<uuid:property_pk>/images/", PropertyImageCollectionView.as_view(), name="property-image-list"),
    path(
        "<uuid:property_pk>/images/<uuid:image_id>/",
        PropertyImageDetailView.as_view(),
        name="property-image-detail",
    ),
]
