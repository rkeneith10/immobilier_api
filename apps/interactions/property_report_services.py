from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.properties.models import Property
from apps.notifications.models import Notification
from apps.notifications.services import notify_admins
from .models import PropertyReport


class PropertyNotPublished(Exception):
    pass


class DuplicateActivePropertyReport(Exception):
    pass


class InvalidPropertyReportTransition(Exception):
    pass


@transaction.atomic
def create_property_report(*, property_id, reported_by, reason, description=""):
    property_obj = Property.objects.select_for_update().filter(pk=property_id).first()
    if property_obj is None:
        raise Property.DoesNotExist
    if property_obj.status != Property.Status.PUBLISHED:
        raise PropertyNotPublished
    active_statuses = (PropertyReport.Status.PENDING, PropertyReport.Status.REVIEWED)
    duplicate = PropertyReport.objects.filter(
        property=property_obj,
        reported_by=reported_by,
        reason=reason,
        status__in=active_statuses,
    ).exists()
    if duplicate:
        raise DuplicateActivePropertyReport
    try:
        report = PropertyReport.objects.create(
            property=property_obj,
            reported_by=reported_by,
            reason=reason,
            description=description,
        )
        notify_admins(
            notification_type=Notification.Type.PROPERTY_REPORTED,
            title="Nouvelle annonce signalée",
            message=f"L’annonce « {property_obj.title} » a été signalée.",
            data={"property_id": str(property_obj.pk), "report_id": str(report.pk)},
        )
        return report
    except IntegrityError as exc:
        # The partial unique index protects against concurrent duplicate submissions.
        raise DuplicateActivePropertyReport from exc


@transaction.atomic
def review_property_report(*, report_id, reviewer, target_status):
    report = PropertyReport.objects.select_for_update().get(pk=report_id)
    allowed = {
        PropertyReport.Status.PENDING: {
            PropertyReport.Status.REVIEWED,
            PropertyReport.Status.DISMISSED,
            PropertyReport.Status.ACTION_TAKEN,
        },
        PropertyReport.Status.REVIEWED: {
            PropertyReport.Status.DISMISSED,
            PropertyReport.Status.ACTION_TAKEN,
        },
    }
    if target_status not in allowed.get(report.status, set()):
        raise InvalidPropertyReportTransition
    report.status = target_status
    report.reviewed_by = reviewer
    report.reviewed_at = timezone.now()
    report.save(update_fields=("status", "reviewed_by", "reviewed_at", "updated_at"))
    return report
