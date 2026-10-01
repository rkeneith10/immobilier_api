import cloudinary.exceptions
from django.db import DatabaseError
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .image_serializers import PropertyImageSerializer, PropertyImageUploadSerializer
from .models import Property, PropertyImage
from .permissions import CanManagePropertyImages
from .services import (
    CloudinaryNotConfigured,
    create_property_image,
    delete_cloudinary_image,
    remove_property_image,
    update_property_image,
    upload_property_image,
)


class ImageStorageUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Le stockage des images est indisponible. Réessayez plus tard."
    default_code = "image_storage_unavailable"


def get_authorized_property(request, property_pk):
    property_obj = get_object_or_404(Property.objects, pk=property_pk)
    permission = CanManagePropertyImages()
    if not permission.has_permission(request, None) or not permission.has_object_permission(
        request, None, property_obj
    ):
        from rest_framework.exceptions import PermissionDenied

        raise PermissionDenied(permission.message)
    return property_obj


class PropertyImageCollectionView(APIView):
    permission_classes = (IsAuthenticated, CanManagePropertyImages)
    parser_classes = (MultiPartParser, FormParser)

    @extend_schema(
        tags=["Property images"],
        summary="Ajouter une image à une propriété",
        request=PropertyImageUploadSerializer,
        responses={201: PropertyImageSerializer},
    )
    def post(self, request, property_pk):
        property_obj = get_authorized_property(request, property_pk)
        serializer = PropertyImageUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        uploaded_file = serializer.validated_data.pop("image")
        try:
            cloud_image = upload_property_image(uploaded_file)
        except (CloudinaryNotConfigured, cloudinary.exceptions.Error) as exc:
            raise ImageStorageUnavailable() from exc

        try:
            image_data = dict(serializer.validated_data)
            if "is_primary" not in serializer.initial_data:
                image_data.pop("is_primary", None)
            image = create_property_image(
                property_obj.pk,
                {**image_data, **cloud_image},
            )
        except DatabaseError:
            try:
                delete_cloudinary_image(cloud_image["public_id"])
            except Exception:
                pass
            raise
        return Response(PropertyImageSerializer(image).data, status=status.HTTP_201_CREATED)


class PropertyImageDetailView(APIView):
    permission_classes = (IsAuthenticated, CanManagePropertyImages)

    def _get_image(self, request, property_pk, image_id):
        property_obj = get_authorized_property(request, property_pk)
        image = get_object_or_404(PropertyImage, pk=image_id, property=property_obj)
        return property_obj, image

    @extend_schema(
        tags=["Property images"],
        summary="Modifier les métadonnées ou l’ordre d’une image",
        request=PropertyImageSerializer,
        responses=PropertyImageSerializer,
    )
    def patch(self, request, property_pk, image_id):
        property_obj, image = self._get_image(request, property_pk, image_id)
        serializer = PropertyImageSerializer(
            image,
            data=request.data,
            partial=True,
            context={"request": request, "property": property_obj},
        )
        serializer.is_valid(raise_exception=True)
        updated = update_property_image(image.pk, property_obj.pk, dict(serializer.validated_data))
        return Response(
            PropertyImageSerializer(updated, context={"request": request, "property": property_obj}).data
        )

    @extend_schema(tags=["Property images"], summary="Supprimer une image", request=None, responses={204: None})
    def delete(self, request, property_pk, image_id):
        property_obj, image = self._get_image(request, property_pk, image_id)
        try:
            delete_cloudinary_image(image.public_id)
        except (CloudinaryNotConfigured, cloudinary.exceptions.Error) as exc:
            raise ImageStorageUnavailable() from exc
        remove_property_image(image.pk, property_obj.pk)
        return Response(status=status.HTTP_204_NO_CONTENT)
