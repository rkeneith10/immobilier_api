from django.db.models import Count
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import AdminOnly
from apps.interactions.models import Favorite, Inquiry, VisitRequest
from .models import Property, PropertyView


class PropertyAnalyticsSerializer(serializers.Serializer):
    views = serializers.IntegerField()
    favorites = serializers.IntegerField()
    inquiries = serializers.IntegerField()
    visit_requests = serializers.IntegerField()


def analytics_for_property(property_id):
    """Fetch fixed-count aggregates, independent of the number of related rows."""
    return {
        "views": PropertyView.objects.filter(property_id=property_id).aggregate(total=Count("id"))["total"],
        "favorites": Favorite.objects.filter(property_id=property_id).aggregate(total=Count("id"))["total"],
        "inquiries": Inquiry.objects.filter(property_id=property_id).aggregate(total=Count("id"))["total"],
        "visit_requests": VisitRequest.objects.filter(property_id=property_id).aggregate(total=Count("id"))["total"],
    }


class OwnerPropertyAnalyticsView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(tags=["Owner analytics"], summary="Statistiques d’une propriété du propriétaire", responses=PropertyAnalyticsSerializer)
    def get(self, request, property_id):
        if getattr(request.user, "role", None) not in {"OWNER", "AGENT"}:
            raise PermissionDenied("Cette ressource est réservée aux propriétaires et agents.")
        property_obj = get_object_or_404(Property.objects.filter(owner=request.user), pk=property_id)
        return Response(analytics_for_property(property_obj.pk))


class AdminPropertyAnalyticsView(APIView):
    permission_classes = (AdminOnly,)

    @extend_schema(tags=["Admin"], summary="Statistiques globales des propriétés", responses=PropertyAnalyticsSerializer)
    def get(self, request):
        property_filter = {"property__is_deleted": False}
        data = {
            "views": PropertyView.objects.filter(**property_filter).aggregate(total=Count("id"))["total"],
            "favorites": Favorite.objects.filter(**property_filter).aggregate(total=Count("id"))["total"],
            "inquiries": Inquiry.objects.filter(**property_filter).aggregate(total=Count("id"))["total"],
            "visit_requests": VisitRequest.objects.filter(**property_filter).aggregate(total=Count("id"))["total"],
        }
        return Response(data)
