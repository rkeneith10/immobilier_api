from django.db import transaction

from apps.properties.models import Property
from apps.notifications.models import Notification
from apps.notifications.services import create_notification
from .models import Favorite, Inquiry, VisitRequest


class PropertyNotPublished(Exception):
    """Raised when a favorite is attempted for a non-public listing."""


@transaction.atomic
def add_favorite(user, property_id):
    property_obj = Property.objects.select_for_update().get(pk=property_id)
    if property_obj.status != Property.Status.PUBLISHED:
        raise PropertyNotPublished
    favorite, created = Favorite.objects.get_or_create(user=user, property=property_obj)
    return favorite, created


@transaction.atomic
def remove_favorite(user, property_id):
    deleted_count, _ = Favorite.objects.filter(user=user, property_id=property_id).delete()
    return deleted_count > 0


@transaction.atomic
def create_inquiry(user, property_id, message):
    property_obj = Property.objects.select_for_update().select_related("owner").get(pk=property_id)
    if property_obj.status != Property.Status.PUBLISHED:
        raise PropertyNotPublished
    inquiry = Inquiry.objects.create(
        property=property_obj,
        user=user,
        owner=property_obj.owner,
        message=message,
        status=Inquiry.Status.NEW,
    )
    create_notification(
        user=property_obj.owner,
        notification_type=Notification.Type.NEW_INQUIRY,
        title="Nouvelle demande de contact",
        message=f"Vous avez reçu une demande pour l’annonce « {property_obj.title} ».",
        data={"property_id": str(property_obj.pk), "inquiry_id": str(inquiry.pk)},
    )
    return inquiry


class InvalidVisitRequestTransition(Exception):
    pass


class VisitRequestActionDenied(Exception):
    pass


@transaction.atomic
def create_visit_request(user, property_id, requested_date, requested_time, message=""):
    property_obj = Property.objects.select_for_update().select_related("owner").get(pk=property_id)
    if property_obj.status != Property.Status.PUBLISHED:
        raise PropertyNotPublished
    visit_request = VisitRequest.objects.create(
        property=property_obj,
        user=user,
        owner=property_obj.owner,
        requested_date=requested_date,
        requested_time=requested_time,
        message=message,
        status=VisitRequest.Status.PENDING,
    )
    create_notification(
        user=property_obj.owner,
        notification_type=Notification.Type.NEW_VISIT_REQUEST,
        title="Nouvelle demande de visite",
        message=f"Vous avez reçu une demande de visite pour « {property_obj.title} ».",
        data={"property_id": str(property_obj.pk), "visit_request_id": str(visit_request.pk)},
    )
    return visit_request


@transaction.atomic
def transition_visit_request(visit_request_id, actor, target_status):
    visit_request = VisitRequest.objects.select_for_update().get(pk=visit_request_id)
    current_status = visit_request.status

    allowed_actor_targets = set()
    if actor.pk == visit_request.user_id:
        allowed_actor_targets.add(VisitRequest.Status.CANCELLED)
    if actor.pk == visit_request.owner_id:
        allowed_actor_targets.update(
            {VisitRequest.Status.ACCEPTED, VisitRequest.Status.REJECTED, VisitRequest.Status.COMPLETED}
        )
    if target_status not in allowed_actor_targets:
        raise VisitRequestActionDenied("Cette transition n’est pas autorisée pour cet utilisateur.")

    requester_cancel = (
        actor.pk == visit_request.user_id
        and current_status == VisitRequest.Status.PENDING
        and target_status == VisitRequest.Status.CANCELLED
    )
    owner_decision = (
        actor.pk == visit_request.owner_id
        and current_status == VisitRequest.Status.PENDING
        and target_status in {VisitRequest.Status.ACCEPTED, VisitRequest.Status.REJECTED}
    )
    owner_completion = (
        actor.pk == visit_request.owner_id
        and current_status == VisitRequest.Status.ACCEPTED
        and target_status == VisitRequest.Status.COMPLETED
    )

    if not (requester_cancel or owner_decision or owner_completion):
        if actor.pk not in {visit_request.user_id, visit_request.owner_id}:
            raise VisitRequestActionDenied("Seul le demandeur ou le propriétaire peut modifier cette demande.")
        raise InvalidVisitRequestTransition(
            f"Transition impossible depuis {current_status} vers {target_status}."
        )

    visit_request.status = target_status
    visit_request.save(update_fields=("status", "updated_at"))
    notification_type = {
        VisitRequest.Status.ACCEPTED: Notification.Type.VISIT_ACCEPTED,
        VisitRequest.Status.REJECTED: Notification.Type.VISIT_REJECTED,
    }.get(target_status)
    if notification_type:
        accepted = target_status == VisitRequest.Status.ACCEPTED
        create_notification(
            user=visit_request.user,
            notification_type=notification_type,
            title="Votre demande de visite a été acceptée" if accepted else "Votre demande de visite a été refusée",
            message=(
                f"Le propriétaire a accepté votre demande pour « {visit_request.property.title} »."
                if accepted else f"Le propriétaire a refusé votre demande pour « {visit_request.property.title} »."
            ),
            data={"property_id": str(visit_request.property_id), "visit_request_id": str(visit_request.pk)},
        )
    return visit_request
