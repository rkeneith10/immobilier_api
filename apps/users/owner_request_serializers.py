from rest_framework import serializers

from .models import OwnerRequest, User


class OwnerRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = OwnerRequest
        fields = (
            "id",
            "status",
            "request_type",
            "full_name",
            "phone",
            "address",
            "message",
            "rejection_reason",
            "reviewed_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class OwnerRequestCreateSerializer(serializers.ModelSerializer):
    request_type = serializers.ChoiceField(
        choices=OwnerRequest.RequestType.choices,
        default=OwnerRequest.RequestType.OWNER,
        required=False,
    )
    full_name = serializers.CharField(max_length=200, required=True, allow_blank=False)
    phone = serializers.CharField(max_length=32, required=True, allow_blank=False)
    address = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    message = serializers.CharField(required=False, allow_blank=True, default="")

    class Meta:
        model = OwnerRequest
        fields = ("id", "request_type", "full_name", "phone", "address", "message")
        read_only_fields = ("id",)

    def validate(self, attrs):
        user = self.context["request"].user

        # Prevent tampering with server-controlled fields
        forbidden_fields = {"status", "reviewed_by", "reviewed_at", "rejection_reason", "user"}
        if forbidden_fields.intersection(self.initial_data):
            raise serializers.ValidationError(
                "Le statut et les données de modération sont gérés exclusivement par le serveur."
            )

        if getattr(user, "role", None) != User.Role.USER:
            raise serializers.ValidationError(
                "Seuls les utilisateurs avec le rôle USER peuvent soumettre une demande."
            )

        if OwnerRequest.objects.filter(user=user, status=OwnerRequest.Status.PENDING).exists():
            raise serializers.ValidationError(
                "Une demande pour devenir propriétaire est déjà en cours de traitement."
            )

        return attrs


class AdminOwnerRequestSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source="user.id", read_only=True)
    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_phone = serializers.CharField(source="user.phone", read_only=True)
    reviewed_by_id = serializers.UUIDField(source="reviewed_by.id", read_only=True, default=None)
    reviewer_email = serializers.EmailField(source="reviewed_by.email", read_only=True, default=None)

    class Meta:
        model = OwnerRequest
        fields = (
            "id",
            "user_id",
            "user_email",
            "user_phone",
            "full_name",
            "phone",
            "address",
            "message",
            "request_type",
            "status",
            "rejection_reason",
            "reviewed_by_id",
            "reviewer_email",
            "reviewed_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class AdminOwnerRequestRejectSerializer(serializers.Serializer):
    rejection_reason = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=2000,
        error_messages={
            "required": "Le motif de refus est obligatoire.",
            "blank": "Le motif de refus ne peut pas être vide.",
        },
    )
