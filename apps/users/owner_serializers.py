from rest_framework import serializers

from .models import OwnerProfile, OwnerVerification


class OwnerProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = OwnerProfile
        fields = (
            "id", "user", "display_name", "business_name", "description",
            "verification_status", "verified_at", "created_at", "updated_at",
        )
        read_only_fields = ("id", "user", "verification_status", "verified_at", "created_at", "updated_at")

    def validate(self, attrs):
        if {"verification_status", "verified_at", "user"}.intersection(self.initial_data):
            raise serializers.ValidationError(
                "Le compte, le statut de vérification et la date de vérification sont gérés par le serveur."
            )
        return attrs


class OwnerVerificationSerializer(serializers.ModelSerializer):
    owner_profile_id = serializers.UUIDField(read_only=True)
    owner_email = serializers.EmailField(source="owner_profile.user.email", read_only=True)

    class Meta:
        model = OwnerVerification
        fields = (
            "id", "owner_profile_id", "owner_email", "status", "reviewed_by",
            "reviewed_at", "rejection_reason", "created_at", "updated_at",
        )
        read_only_fields = fields


class OwnerVerificationReviewSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=(OwnerVerification.Status.VERIFIED, OwnerVerification.Status.REJECTED))
    rejection_reason = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if set(self.initial_data) - {"status", "rejection_reason"}:
            raise serializers.ValidationError("Seuls le statut et le motif de rejet sont modifiables.")
        if attrs["status"] == OwnerVerification.Status.REJECTED and not attrs.get("rejection_reason", "").strip():
            raise serializers.ValidationError({"rejection_reason": "Un motif est requis pour un rejet."})
        if attrs["status"] == OwnerVerification.Status.VERIFIED and attrs.get("rejection_reason"):
            raise serializers.ValidationError({"rejection_reason": "Un motif n'est accepté que pour un rejet."})
        return attrs
