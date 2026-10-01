from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.notifications.models import Notification
from apps.notifications.services import notify_admins
from .models import OwnerProfile, OwnerVerification


@transaction.atomic
def submit_owner_verification(*, user):
    profile = OwnerProfile.objects.select_for_update().filter(user=user).first()
    if profile is None:
        raise ValidationError({"detail": "Créez d'abord votre profil propriétaire."})
    if profile.verification_status == OwnerProfile.VerificationStatus.VERIFIED:
        raise ValidationError({"detail": "Ce profil est déjà vérifié."})
    if profile.verification_requests.filter(status=OwnerVerification.Status.PENDING).exists():
        raise ValidationError({"detail": "Une demande de vérification est déjà en attente."})
    profile.verification_status = OwnerProfile.VerificationStatus.PENDING
    profile.verified_at = None
    profile.save(update_fields=("verification_status", "verified_at", "updated_at"))
    verification = OwnerVerification.objects.create(owner_profile=profile)
    notify_admins(
        notification_type=Notification.Type.NEW_VERIFICATION,
        title="Nouvelle demande de vérification",
        message=f"{profile.display_name} a soumis une demande de vérification.",
        data={"owner_profile_id": str(profile.pk), "verification_id": str(verification.pk)},
    )
    return verification


@transaction.atomic
def review_owner_verification(*, verification_id, reviewer, status, rejection_reason=""):
    verification = (
        OwnerVerification.objects.select_for_update()
        .select_related("owner_profile")
        .get(pk=verification_id)
    )
    if verification.status != OwnerVerification.Status.PENDING:
        raise ValidationError({"status": "Seules les demandes en attente peuvent être examinées."})
    now = timezone.now()
    verification.status = status
    verification.reviewed_by = reviewer
    verification.reviewed_at = now
    verification.rejection_reason = rejection_reason.strip() if status == OwnerVerification.Status.REJECTED else ""
    verification.save(update_fields=("status", "reviewed_by", "reviewed_at", "rejection_reason", "updated_at"))

    profile = OwnerProfile.objects.select_for_update().get(pk=verification.owner_profile_id)
    profile.verification_status = status
    profile.verified_at = now if status == OwnerVerification.Status.VERIFIED else None
    profile.save(update_fields=("verification_status", "verified_at", "updated_at"))
    return verification


@transaction.atomic
def submit_owner_request(
    *,
    user,
    full_name,
    phone,
    address="",
    message="",
    request_type="OWNER",
):
    from .models import User, OwnerRequest

    if user.role != User.Role.USER:
        raise ValidationError({"detail": "Seuls les utilisateurs standards peuvent soumettre une demande."})

    if OwnerRequest.objects.filter(user=user, status=OwnerRequest.Status.PENDING).exists():
        raise ValidationError({"detail": "Une demande est déjà en cours de traitement."})

    req = OwnerRequest.objects.create(
        user=user,
        full_name=full_name.strip(),
        phone=phone.strip(),
        address=(address or "").strip(),
        message=(message or "").strip(),
        request_type=request_type,
        status=OwnerRequest.Status.PENDING,
    )

    notify_admins(
        notification_type=Notification.Type.NEW_OWNER_REQUEST,
        title="Nouvelle demande pour devenir propriétaire",
        message=f"{req.full_name} ({user.email}) a soumis une demande pour devenir propriétaire.",
        data={"owner_request_id": str(req.pk), "user_id": str(user.pk)},
    )
    return req


@transaction.atomic
def approve_owner_request(*, request_id, reviewer):
    from apps.notifications.services import create_notification
    from .models import OwnerRequest, OwnerProfile

    owner_request = (
        OwnerRequest.objects.select_for_update()
        .select_related("user")
        .filter(pk=request_id)
        .first()
    )
    if not owner_request:
        raise ValidationError({"detail": "Demande introuvable."})

    if owner_request.status != OwnerRequest.Status.PENDING:
        raise ValidationError({"detail": "Seules les demandes en attente peuvent être approuvées."})

    target_user = owner_request.user
    target_user.role = owner_request.request_type
    target_user.save(update_fields=("role", "updated_at"))

    OwnerProfile.objects.get_or_create(
        user=target_user,
        defaults={"display_name": owner_request.full_name or target_user.get_full_name() or target_user.email},
    )

    now = timezone.now()
    owner_request.status = OwnerRequest.Status.APPROVED
    owner_request.reviewed_by = reviewer
    owner_request.reviewed_at = now
    owner_request.save(update_fields=("status", "reviewed_by", "reviewed_at", "updated_at"))

    create_notification(
        user=target_user,
        notification_type=Notification.Type.OWNER_REQUEST_APPROVED,
        title="Demande acceptée",
        message="Votre demande pour devenir propriétaire a été acceptée.",
        data={"owner_request_id": str(owner_request.pk)},
    )
    return owner_request


@transaction.atomic
def reject_owner_request(*, request_id, reviewer, rejection_reason):
    from apps.notifications.services import create_notification
    from .models import OwnerRequest

    reason = (rejection_reason or "").strip()
    if not reason:
        raise ValidationError({"rejection_reason": "Le motif de refus est obligatoire."})

    owner_request = (
        OwnerRequest.objects.select_for_update()
        .select_related("user")
        .filter(pk=request_id)
        .first()
    )
    if not owner_request:
        raise ValidationError({"detail": "Demande introuvable."})

    if owner_request.status != OwnerRequest.Status.PENDING:
        raise ValidationError({"detail": "Seules les demandes en attente peuvent être refusées."})

    now = timezone.now()
    owner_request.status = OwnerRequest.Status.REJECTED
    owner_request.rejection_reason = reason
    owner_request.reviewed_by = reviewer
    owner_request.reviewed_at = now
    owner_request.save(update_fields=("status", "rejection_reason", "reviewed_by", "reviewed_at", "updated_at"))

    create_notification(
        user=owner_request.user,
        notification_type=Notification.Type.OWNER_REQUEST_REJECTED,
        title="Demande refusée",
        message=f"Votre demande pour devenir propriétaire a été refusée. Motif : {reason}",
        data={"owner_request_id": str(owner_request.pk), "rejection_reason": reason},
    )
    return owner_request

