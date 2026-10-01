from datetime import datetime

from django.utils import timezone
from rest_framework import serializers

from apps.properties.serializers import PropertySerializer
from .models import Favorite, Inquiry, VisitRequest


class FavoriteSerializer(serializers.ModelSerializer):
    property = PropertySerializer(read_only=True)

    class Meta:
        model = Favorite
        fields = ("id", "property", "created_at")
        read_only_fields = fields


class InquiryCreateSerializer(serializers.Serializer):
    message = serializers.CharField(trim_whitespace=True, max_length=10000)

    def validate(self, attrs):
        protected_fields = {"property", "property_id", "user", "user_id", "owner", "owner_id", "status"}
        attempted = protected_fields.intersection(self.initial_data)
        if attempted:
            raise serializers.ValidationError({
                field: "Ce champ est déterminé par le serveur et ne peut pas être envoyé."
                for field in sorted(attempted)
            })
        return attrs


class InquirySerializer(serializers.ModelSerializer):
    property = serializers.PrimaryKeyRelatedField(read_only=True)
    user = serializers.PrimaryKeyRelatedField(read_only=True)
    owner = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Inquiry
        fields = ("id", "property", "user", "owner", "message", "status", "created_at", "updated_at")
        read_only_fields = ("id", "property", "user", "owner", "message", "created_at", "updated_at")

    def validate(self, attrs):
        protected_fields = {"property", "property_id", "user", "user_id", "owner", "owner_id", "message"}
        attempted = protected_fields.intersection(self.initial_data)
        if attempted:
            raise serializers.ValidationError({
                field: "Ce champ ne peut pas être modifié après l’envoi de l’inquiry."
                for field in sorted(attempted)
            })

        status_value = attrs.get("status")
        if status_value is not None:
            request_user = self.context["request"].user
            inquiry = self.instance
            is_admin = getattr(request_user, "role", None) == "ADMIN"
            is_owner = inquiry.owner_id == request_user.pk
            if not (is_admin or is_owner) and status_value != Inquiry.Status.CLOSED:
                raise serializers.ValidationError({"status": "Le client peut uniquement fermer sa propre inquiry."})
        return attrs


class VisitRequestCreateSerializer(serializers.Serializer):
    requested_date = serializers.DateField()
    requested_time = serializers.TimeField()
    message = serializers.CharField(max_length=5000, required=False, allow_blank=True, trim_whitespace=True)

    def validate(self, attrs):
        protected_fields = {"property", "property_id", "user", "user_id", "owner", "owner_id", "status"}
        attempted = protected_fields.intersection(self.initial_data)
        if attempted:
            raise serializers.ValidationError({
                field: "Ce champ est déterminé par le serveur et ne peut pas être envoyé."
                for field in sorted(attempted)
            })

        scheduled_local = datetime.combine(attrs["requested_date"], attrs["requested_time"])
        scheduled = timezone.make_aware(scheduled_local, timezone.get_current_timezone())
        if scheduled <= timezone.now():
            raise serializers.ValidationError({
                "requested_date": "La date et l’heure demandées doivent être dans le futur."
            })
        attrs.setdefault("message", "")
        return attrs


class VisitRequestSerializer(serializers.ModelSerializer):
    property = serializers.PrimaryKeyRelatedField(read_only=True)
    user = serializers.PrimaryKeyRelatedField(read_only=True)
    owner = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = VisitRequest
        fields = (
            "id",
            "property",
            "user",
            "owner",
            "requested_date",
            "requested_time",
            "message",
            "status",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class VisitRequestTransitionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=VisitRequest.Status.choices)

    def validate(self, attrs):
        attempted = set(self.initial_data) - {"status"}
        if attempted:
            raise serializers.ValidationError({
                field: "Seul le statut peut être modifié ici."
                for field in sorted(attempted)
            })
        return attrs
