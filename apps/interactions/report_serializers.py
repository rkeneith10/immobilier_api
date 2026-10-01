from rest_framework import serializers

from apps.properties.models import Property
from apps.users.models import User
from apps.notifications.models import Notification
from apps.notifications.services import notify_admins
from .models import PropertyReport, Report


class ReportCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Report
        fields = ("id", "property", "reported_user", "reason", "description", "status", "created_at")
        read_only_fields = ("id", "status", "created_at")

    def validate(self, attrs):
        has_property = attrs.get("property") is not None
        has_user = attrs.get("reported_user") is not None
        if has_property == has_user:
            raise serializers.ValidationError("Signalez exactement une propriété ou un utilisateur.")
        request_user = self.context["request"].user
        if has_user and attrs["reported_user"].pk == request_user.pk:
            raise serializers.ValidationError({"reported_user": "Vous ne pouvez pas vous signaler vous-même."})
        if has_user and not User.objects.filter(pk=attrs["reported_user"].pk).exists():
            raise serializers.ValidationError({"reported_user": "Cet utilisateur n'est pas disponible."})
        return attrs

    def create(self, validated_data):
        report = Report.objects.create(reporter=self.context["request"].user, **validated_data)
        if report.property_id:
            notify_admins(
                notification_type=Notification.Type.PROPERTY_REPORTED,
                title="Nouvelle annonce signalée",
                message=f"L’annonce « {report.property.title} » a été signalée.",
                data={"property_id": str(report.property_id), "report_id": str(report.pk)},
            )
        return report


class ReportSerializer(serializers.ModelSerializer):
    reporter_email = serializers.EmailField(source="reporter.email", read_only=True)
    reported_user_email = serializers.EmailField(source="reported_user.email", read_only=True)
    property_title = serializers.CharField(source="property.title", read_only=True)

    class Meta:
        model = Report
        fields = (
            "id", "reporter", "reporter_email", "property", "property_title", "reported_user",
            "reported_user_email", "reason", "description", "status", "reviewed_by", "reviewed_at",
            "resolution_note", "created_at", "updated_at",
        )
        read_only_fields = fields


class AdminReportUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Report
        fields = ("status", "resolution_note")


class PropertyReportCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PropertyReport
        fields = ("id", "reason", "description", "status", "created_at")
        read_only_fields = ("id", "status", "created_at")


class PropertyReportSerializer(serializers.ModelSerializer):
    property_title = serializers.CharField(source="property.title", read_only=True)
    reported_by_email = serializers.EmailField(source="reported_by.email", read_only=True)
    reviewed_by_email = serializers.EmailField(source="reviewed_by.email", read_only=True)

    class Meta:
        model = PropertyReport
        fields = (
            "id", "property", "property_title", "reported_by", "reported_by_email",
            "reason", "description", "status", "reviewed_by", "reviewed_by_email",
            "reviewed_at", "created_at", "updated_at",
        )
        read_only_fields = fields


class PropertyReportReviewSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=(
        PropertyReport.Status.REVIEWED,
        PropertyReport.Status.DISMISSED,
        PropertyReport.Status.ACTION_TAKEN,
    ))

    def validate(self, attrs):
        if set(self.initial_data) != {"status"}:
            raise serializers.ValidationError("Seul le statut de traitement peut être modifié.")
        return attrs
