import warnings

from PIL import Image
from rest_framework import serializers

from django.conf import settings

from .models import PropertyImage


ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


class PropertyImageUploadSerializer(serializers.Serializer):
    image = serializers.ImageField(write_only=True)
    alt_text = serializers.CharField(max_length=255, required=False, allow_blank=True)
    is_primary = serializers.BooleanField(required=False)

    def validate_image(self, uploaded_file):
        if uploaded_file.size > settings.PROPERTY_IMAGE_MAX_UPLOAD_SIZE:
            limit_mb = settings.PROPERTY_IMAGE_MAX_UPLOAD_SIZE // (1024 * 1024)
            raise serializers.ValidationError(f"L’image dépasse la taille maximale de {limit_mb} Mo.")
        try:
            uploaded_file.seek(0)
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(uploaded_file) as image:
                    if image.format not in ALLOWED_IMAGE_FORMATS:
                        raise serializers.ValidationError("Formats acceptés : JPEG, PNG et WebP.")
                    image.verify()
        except serializers.ValidationError:
            raise
        except Exception as exc:
            raise serializers.ValidationError("Le fichier doit être une image valide.") from exc
        finally:
            uploaded_file.seek(0)
        return uploaded_file


class PropertyImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = PropertyImage
        fields = ("id", "property", "url", "public_id", "alt_text", "sort_order", "is_primary", "created_at")
        read_only_fields = ("id", "property", "url", "public_id", "created_at")

    def validate_sort_order(self, value):
        property_obj = self.context["property"]
        image_count = property_obj.images.count()
        if value >= image_count:
            raise serializers.ValidationError(f"La position doit être comprise entre 0 et {image_count - 1}.")
        return value
