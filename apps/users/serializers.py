import warnings

from PIL import Image
from django.conf import settings
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "phone",
            "first_name",
            "last_name",
            "avatar",
            "role",
            "status",
            "email_verified",
            "phone_verified",
            "last_login",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class RegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    phone = serializers.CharField(
        max_length=32,
        required=False,
        allow_null=True,
        allow_blank=True,
    )

    class Meta:
        model = User
        fields = ("id", "email", "phone", "first_name", "last_name", "password")
        read_only_fields = ("id",)

    def validate_email(self, value):
        email = User.objects.normalize_email(value.strip()).lower()
        if User.all_objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError("Un compte utilise déjà cette adresse e-mail.")
        return email

    def validate_phone(self, value):
        phone = value.strip() or None
        if phone and User.all_objects.filter(phone=phone).exists():
            raise serializers.ValidationError("Un compte utilise déjà ce numéro de téléphone.")
        return phone

    def validate(self, attrs):
        if "role" in self.initial_data:
            raise serializers.ValidationError({"role": "Le rôle ne peut pas être défini à l’inscription."})
        candidate = User(
            email=attrs.get("email", ""),
            first_name=attrs.get("first_name", ""),
            last_name=attrs.get("last_name", ""),
        )
        try:
            password_validation.validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        # Public registration can only create ordinary, active accounts.
        validated_data["role"] = User.Role.USER
        validated_data["status"] = User.Status.ACTIVE
        return User.objects.create_user(password=password, **validated_data)


class ProfileSerializer(serializers.ModelSerializer):
    phone = serializers.CharField(
        max_length=32,
        required=False,
        allow_null=True,
        allow_blank=True,
    )

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "phone",
            "first_name",
            "last_name",
            "avatar",
            "role",
            "status",
            "email_verified",
            "phone_verified",
            "last_login",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "email",
            "role",
            "status",
            "email_verified",
            "phone_verified",
            "last_login",
            "created_at",
            "updated_at",
        )

    def validate(self, attrs):
        protected_fields = {
            "role",
            "status",
            "email_verified",
            "phone_verified",
            "is_staff",
            "is_superuser",
            "is_deleted",
            "deleted_at",
        }
        attempted = protected_fields.intersection(self.initial_data)
        if attempted:
            raise serializers.ValidationError({
                field: "Ce champ ne peut pas être modifié via le profil." for field in sorted(attempted)
            })
        return attrs

    def validate_phone(self, value):
        phone = value.strip() or None
        if phone and User.all_objects.filter(phone=phone).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("Un compte utilise déjà ce numéro de téléphone.")
        return phone

    def validate_avatar(self, uploaded_file):
        if uploaded_file.size > settings.PROFILE_IMAGE_MAX_UPLOAD_SIZE:
            raise serializers.ValidationError("L’avatar dépasse la taille maximale autorisée.")
        try:
            uploaded_file.seek(0)
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(uploaded_file) as image:
                    if image.format not in {"JPEG", "PNG", "WEBP"}:
                        raise serializers.ValidationError("Formats acceptés : JPEG, PNG et WebP.")
                    image.verify()
        except serializers.ValidationError:
            raise
        except Exception as exc:
            raise serializers.ValidationError("Le fichier doit être une image valide et de taille raisonnable.") from exc
        finally:
            uploaded_file.seek(0)
        return uploaded_file
