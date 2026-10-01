from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from apps.notifications.models import Notification
from apps.notifications.services import create_notification

import cloudinary
import cloudinary.uploader

from .models import Property, PropertyImage


class InvalidPropertyTransition(Exception):
    """Raised when a property cannot enter a requested workflow state."""


class CloudinaryNotConfigured(Exception):
    pass


def _configure_cloudinary():
    credentials = settings.CLOUDINARY_STORAGE
    if not all(credentials.get(key) for key in ("CLOUD_NAME", "API_KEY", "API_SECRET")):
        raise CloudinaryNotConfigured("Les variables Cloudinary ne sont pas configurées.")
    cloudinary.config(
        cloud_name=credentials["CLOUD_NAME"],
        api_key=credentials["API_KEY"],
        api_secret=credentials["API_SECRET"],
        secure=True,
    )


def upload_property_image(uploaded_file):
    _configure_cloudinary()
    result = cloudinary.uploader.upload(
        uploaded_file,
        folder="immoplatform/properties",
        resource_type="image",
        overwrite=False,
    )
    return {"url": result["secure_url"], "public_id": result["public_id"]}


def delete_cloudinary_image(public_id):
    _configure_cloudinary()
    return cloudinary.uploader.destroy(public_id, resource_type="image")


@transaction.atomic
def create_property_image(property_id, image_data):
    property_obj = Property.objects.select_for_update().get(pk=property_id)
    current_images = list(property_obj.images.select_for_update().order_by("sort_order"))
    is_primary = image_data.pop("is_primary", not current_images)
    if is_primary:
        property_obj.images.filter(is_primary=True).update(is_primary=False)
    return PropertyImage.objects.create(
        property=property_obj,
        sort_order=len(current_images),
        is_primary=is_primary,
        **image_data,
    )


@transaction.atomic
def update_property_image(image_id, property_id, changes):
    property_obj = Property.objects.select_for_update().get(pk=property_id)
    images = list(property_obj.images.select_for_update().order_by("sort_order"))
    image = next((item for item in images if str(item.pk) == str(image_id)), None)
    if image is None:
        return None

    new_primary = changes.pop("is_primary", image.is_primary)
    requested_order = changes.pop("sort_order", image.sort_order)
    if requested_order >= len(images):
        from rest_framework.exceptions import ValidationError

        raise ValidationError({"sort_order": f"La position doit être comprise entre 0 et {len(images) - 1}."})

    if new_primary:
        property_obj.images.filter(is_primary=True).exclude(pk=image.pk).update(is_primary=False)
        for item in images:
            item.is_primary = item.pk == image.pk
    elif image.is_primary and len(images) > 1:
        next(item for item in images if item.pk != image.pk).is_primary = True

    for key, value in changes.items():
        setattr(image, key, value)
    image.is_primary = new_primary

    if requested_order != image.sort_order:
        images.remove(image)
        images.insert(requested_order, image)
        offset = max(item.sort_order for item in images) + len(images) + 1
        property_obj.images.update(sort_order=F("sort_order") + offset)
        for position, item in enumerate(images):
            item.sort_order = position
        PropertyImage.objects.bulk_update(images, ("sort_order",))

    if not any(item.is_primary for item in images) and images:
        images[0].is_primary = True
    PropertyImage.objects.bulk_update(images, ("is_primary",))
    image.save(update_fields=(*changes.keys(), "is_primary", "updated_at"))
    return image


@transaction.atomic
def remove_property_image(image_id, property_id):
    property_obj = Property.objects.select_for_update().get(pk=property_id)
    images = list(property_obj.images.select_for_update().order_by("sort_order"))
    image = next((item for item in images if str(item.pk) == str(image_id)), None)
    if image is None:
        return None
    images.remove(image)
    image.delete()
    if images:
        offset = max(item.sort_order for item in images) + len(images) + 1
        property_obj.images.update(sort_order=F("sort_order") + offset)
        for position, item in enumerate(images):
            item.sort_order = position
            if image.is_primary and position == 0:
                item.is_primary = True
        PropertyImage.objects.bulk_update(images, ("sort_order", "is_primary"))
    return image


def transition_property(property_id, target_status, allowed_from):
    """Apply a status transition atomically and maintain publication timestamps."""
    with transaction.atomic():
        property_obj = Property.objects.select_for_update().get(pk=property_id)
        if property_obj.status not in allowed_from:
            raise InvalidPropertyTransition(
                f"Transition impossible depuis le statut {property_obj.status}."
            )

        if (
            target_status == Property.Status.PUBLISHED
            and property_obj.expires_at is not None
            and property_obj.expires_at <= timezone.now()
        ):
            raise InvalidPropertyTransition("La date d’expiration est déjà dépassée.")

        property_obj.status = target_status
        update_fields = ["status", "updated_at"]
        if target_status == Property.Status.PUBLISHED:
            property_obj.published_at = timezone.now()
            update_fields.append("published_at")
        property_obj.save(update_fields=update_fields)
        notification_type = {
            Property.Status.PUBLISHED: Notification.Type.PROPERTY_APPROVED,
            Property.Status.REJECTED: Notification.Type.PROPERTY_REJECTED,
        }.get(target_status)
        if notification_type:
            approved = target_status == Property.Status.PUBLISHED
            create_notification(
                user=property_obj.owner,
                notification_type=notification_type,
                title="Votre annonce a été approuvée" if approved else "Votre annonce a été rejetée",
                message=(
                    f"L’annonce « {property_obj.title} » est maintenant publiée."
                    if approved else f"L’annonce « {property_obj.title} » n’a pas été approuvée."
                ),
                data={"property_id": str(property_obj.pk), "slug": property_obj.slug},
            )
        return property_obj
