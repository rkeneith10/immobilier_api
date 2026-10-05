from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.properties.models import Property
from .models import Invoice, Payment, Plan, PropertyPromotion, Subscription


class MonetizationError(Exception):
    """Base error for invalid subscription, promotion, or payment operations."""


class PaymentVerificationError(MonetizationError):
    """Raised when a provider's server-verified result does not match a payment."""


class SubscriptionLimitReached(MonetizationError):
    """Raised when a plan entitlement does not allow a requested operation."""


class SubscriptionService:
    """Resolve plan entitlements and enforce them at business-operation boundaries."""

    # Draft and review states occupy inventory slots too, preventing unlimited unpublished listings.
    ACTIVE_PROPERTY_STATUSES = (
        Property.Status.DRAFT,
        Property.Status.PENDING_REVIEW,
        Property.Status.PUBLISHED,
    )

    @classmethod
    def get_effective_plan(cls, user):
        if getattr(user, "role", None) == "ADMIN":
            return None
        now = timezone.now()
        subscription = (
            Subscription.objects.filter(
                user=user,
                status=Subscription.Status.ACTIVE,
                starts_at__lte=now,
            )
            .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
            .select_related("plan")
            .first()
        )
        if subscription and subscription.plan.is_active:
            return subscription.plan
        try:
            return Plan.objects.get(code=Plan.Code.FREE, is_active=True)
        except Plan.DoesNotExist as exc:
            raise MonetizationError("Aucun plan FREE actif n’est configuré.") from exc

    @classmethod
    def ensure_property_capacity(cls, user):
        if getattr(user, "role", None) == "ADMIN":
            return
        plan = cls.get_effective_plan(user)
        limit = plan.max_active_properties
        if limit is None:
            return
        active_count = Property.objects.filter(
            owner=user,
            status__in=cls.ACTIVE_PROPERTY_STATUSES,
        ).count()
        if active_count >= limit:
            raise SubscriptionLimitReached(
                f"La limite de {limit} annonces actives du plan {plan.code} est atteinte."
            )

    @classmethod
    def ensure_promotion_access(cls, user):
        if getattr(user, "role", None) == "ADMIN":
            return
        plan = cls.get_effective_plan(user)
        if not plan.can_promote_properties:
            raise SubscriptionLimitReached(
                f"Le plan {plan.code} n’autorise pas la promotion d’annonces."
            )


@dataclass(frozen=True)
class ProviderCheckout:
    provider_payment_id: str
    checkout_url: str | None = None
    metadata: dict | None = None


@dataclass(frozen=True)
class ProviderVerification:
    """Server-to-server verification result returned by a concrete provider adapter."""

    provider_payment_id: str
    amount: Decimal
    currency: str
    is_paid: bool
    paid_at: datetime | None = None
    provider_transaction_id: str | None = None


class PaymentService(ABC):
    """Provider adapter contract. Implementations must verify status with the provider server-side."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable provider key stored on payment records."""

    @abstractmethod
    def create_provider_checkout(self, payment: Payment) -> ProviderCheckout:
        """Create a provider checkout and return its server-issued reference."""

    @abstractmethod
    def verify_with_provider(self, payment: Payment) -> ProviderVerification | None:
        """Fetch/verify provider state; return None while still pending."""

    def start_payment(self, payment_id):
        payment = Payment.objects.get(pk=payment_id)
        if payment.status != Payment.Status.PENDING:
            raise MonetizationError("Seul un paiement en attente peut être initialisé.")
        if payment.provider_payment_id:
            raise MonetizationError("Ce paiement a déjà été initialisé auprès du provider.")
        if payment.provider != self.provider_name:
            raise MonetizationError("Ce service n’est pas configuré pour le provider du paiement.")
        checkout = self.create_provider_checkout(payment)
        if not checkout.provider_payment_id:
            raise PaymentVerificationError("Le provider n’a pas retourné de référence de paiement.")
        with transaction.atomic():
            payment = Payment.objects.select_for_update().get(pk=payment_id)
            if payment.status != Payment.Status.PENDING:
                raise MonetizationError("Le paiement a changé d’état pendant l’initialisation.")
            if payment.provider_payment_id and payment.provider_payment_id != checkout.provider_payment_id:
                raise PaymentVerificationError("Une référence provider différente est déjà associée à ce paiement.")
            payment.provider_payment_id = checkout.provider_payment_id
            payment.save(update_fields=("provider_payment_id", "updated_at"))
            return checkout

    def confirm_payment(self, payment_id):
        # Network verification is deliberately outside the database transaction.
        payment = Payment.objects.select_related("subscription", "promotion", "property").get(pk=payment_id)
        if payment.status != Payment.Status.PENDING:
            return payment
        result = self.verify_with_provider(payment)
        if result is None:
            return payment
        if payment.provider_payment_id is None and result.provider_payment_id:
            payment.provider_payment_id = result.provider_payment_id
            payment.save(update_fields=("provider_payment_id", "updated_at"))
        self._validate_provider_result(payment, result)

        with transaction.atomic():
            payment = Payment.objects.select_for_update().get(pk=payment_id)
            if payment.status != Payment.Status.PENDING:
                return payment
            payment.status = Payment.Status.PAID if result.is_paid else Payment.Status.FAILED
            payment.paid_at = (result.paid_at or timezone.now()) if result.is_paid else None
            update_fields = ["status", "paid_at", "updated_at"]
            if getattr(result, "provider_transaction_id", None):
                payment.provider_transaction_id = result.provider_transaction_id
                update_fields.append("provider_transaction_id")
            payment.save(update_fields=tuple(update_fields))

            if result.is_paid:
                if payment.subscription_id:
                    subscription = Subscription.objects.select_for_update().get(pk=payment.subscription_id)
                    if subscription.status == Subscription.Status.PAST_DUE:
                        subscription.status = Subscription.Status.ACTIVE
                        subscription.save(update_fields=("status", "updated_at"))
                elif payment.promotion_id:
                    promotion = PropertyPromotion.objects.select_for_update().get(pk=payment.promotion_id)
                    promotion.status = PropertyPromotion.Status.ACTIVE
                    promotion.save(update_fields=("status", "updated_at"))
                try:
                    invoice = Invoice.objects.select_for_update().get(payment=payment)
                    invoice.status = Invoice.Status.PAID
                    invoice.paid_at = payment.paid_at
                    invoice.save(update_fields=("status", "paid_at", "updated_at"))
                except Invoice.DoesNotExist:
                    pass
            else:
                try:
                    invoice = Invoice.objects.select_for_update().get(payment=payment)
                    invoice.status = Invoice.Status.VOID
                    invoice.save(update_fields=("status", "updated_at"))
                except Invoice.DoesNotExist:
                    pass
            return payment

    @staticmethod
    def _validate_provider_result(payment, result):
        if not result.provider_payment_id or payment.provider_payment_id is None or result.provider_payment_id != payment.provider_payment_id:
            raise PaymentVerificationError("La référence retournée ne correspond pas au paiement.")
        try:
            amount = Decimal(result.amount)
            currency = result.currency.upper()
        except (TypeError, ValueError, AttributeError) as exc:
            raise PaymentVerificationError("Le résultat provider est incomplet ou invalide.") from exc
        if amount != payment.amount or currency != payment.currency or not isinstance(result.is_paid, bool):
            raise PaymentVerificationError("Le montant ou la devise vérifiés ne correspondent pas au paiement.")
        if result.is_paid and result.paid_at is not None and timezone.is_naive(result.paid_at):
            raise PaymentVerificationError("La date de confirmation provider doit être timezone-aware.")


@transaction.atomic
def create_subscription(*, user, plan, starts_at=None, ends_at=None):
    plan = Plan.objects.select_for_update().get(pk=plan.pk)
    if not plan.is_active:
        raise MonetizationError("Ce plan n’est pas actif.")
    if plan.code == Plan.Code.FREE and plan.price != 0:
        raise MonetizationError("Un plan FREE doit avoir un prix nul.")
    start = starts_at or timezone.now()
    if ends_at is not None and ends_at <= start:
        raise MonetizationError("La date de fin doit être postérieure à la date de début.")
    if Subscription.objects.filter(user=user, status=Subscription.Status.ACTIVE).exists():
        raise MonetizationError("Un abonnement actif existe déjà pour ce compte.")
    status = Subscription.Status.ACTIVE if plan.code == Plan.Code.FREE else Subscription.Status.PAST_DUE
    return Subscription.objects.create(
        user=user, plan=plan, status=status, starts_at=start, ends_at=ends_at
    )


@transaction.atomic
def cancel_subscription(*, subscription, actor):
    subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
    if actor.pk != subscription.user_id and actor.role != "ADMIN":
        raise MonetizationError("Seul le titulaire ou un administrateur peut annuler cet abonnement.")
    if subscription.status not in (Subscription.Status.ACTIVE, Subscription.Status.PAST_DUE):
        raise MonetizationError("Cet abonnement ne peut plus être annulé.")
    subscription.status = Subscription.Status.CANCELLED
    subscription.cancelled_at = timezone.now()
    subscription.save(update_fields=("status", "cancelled_at", "updated_at"))
    return subscription


@transaction.atomic
def create_subscription_payment(*, subscription, provider):
    subscription = Subscription.objects.select_for_update().select_related("plan", "user").get(pk=subscription.pk)
    if subscription.status != Subscription.Status.PAST_DUE:
        raise MonetizationError("Un paiement de démarrage est requis uniquement pour un abonnement PAST_DUE.")
    if subscription.plan.price <= 0:
        raise MonetizationError("Un plan gratuit ne nécessite pas de paiement.")
    payment = Payment.objects.create(
        user=subscription.user,
        subscription=subscription,
        provider=provider,
        amount=subscription.plan.price,
        currency=subscription.plan.currency,
        status=Payment.Status.PENDING,
    )
    Invoice.objects.create(
        payment=payment,
        user=payment.user,
        amount=payment.amount,
        currency=payment.currency,
        status=Invoice.Status.ISSUED,
        issued_at=timezone.now(),
    )
    return payment


@transaction.atomic
def create_promotion_payment(*, promotion, provider, amount, currency):
    promotion = PropertyPromotion.objects.select_for_update().select_related("property__owner").get(pk=promotion.pk)
    if promotion.status != PropertyPromotion.Status.PENDING:
        raise MonetizationError("Seule une promotion en attente peut être payée.")
    amount = Decimal(amount)
    if amount <= 0:
        raise MonetizationError("Le montant d’une promotion doit être positif.")
    payment = Payment.objects.create(
        user=promotion.property.owner,
        promotion=promotion,
        provider=provider,
        amount=amount,
        currency=currency,
        status=Payment.Status.PENDING,
    )
    Invoice.objects.create(
        payment=payment,
        user=payment.user,
        amount=payment.amount,
        currency=payment.currency,
        status=Invoice.Status.ISSUED,
        issued_at=timezone.now(),
    )
    return payment


@transaction.atomic
def create_property_publication_payment(*, property_obj, provider, amount, currency):
    from apps.properties.models import Property

    property_obj = Property.objects.select_for_update().select_related("owner").get(pk=property_obj.pk)
    amount = Decimal(amount)
    if amount <= 0:
        raise MonetizationError("Le montant d’un paiement de publication doit être positif.")
    if Payment.objects.filter(property=property_obj, status=Payment.Status.PAID).exists():
        raise MonetizationError("Cette propriété a déjà un paiement confirmé.")
    payment = Payment.objects.create(
        user=property_obj.owner,
        property=property_obj,
        provider=provider,
        amount=amount,
        currency=currency,
        status=Payment.Status.PENDING,
    )
    Invoice.objects.create(
        payment=payment,
        user=payment.user,
        amount=payment.amount,
        currency=payment.currency,
        status=Invoice.Status.ISSUED,
        issued_at=timezone.now(),
    )
    return payment


@transaction.atomic
def create_property_promotion(*, property_obj, actor, promotion_type, starts_at, ends_at):
    property_obj = Property.objects.select_for_update().get(pk=property_obj.pk)
    if property_obj.status != Property.Status.PUBLISHED:
        raise MonetizationError("Une promotion ne peut être créée que pour une annonce publiée.")
    if actor.role != "ADMIN" and property_obj.owner_id != actor.pk:
        raise MonetizationError("Seul le propriétaire de l’annonce ou un administrateur peut la promouvoir.")
    SubscriptionService.ensure_promotion_access(actor)
    if ends_at <= starts_at:
        raise MonetizationError("La date de fin doit être postérieure à la date de début.")
    if promotion_type not in PropertyPromotion.Type.values:
        raise MonetizationError("Type de promotion invalide.")
    return PropertyPromotion.objects.create(
        property=property_obj,
        promotion_type=promotion_type,
        status=PropertyPromotion.Status.PENDING,
        starts_at=starts_at,
        ends_at=ends_at,
    )


FREE_PUBLICATIONS_LIMIT = 1
DEFAULT_PUBLICATION_CURRENCY = "HTG"


@dataclass(frozen=True)
class PublicationEligibility:
    property_id: str
    is_free: bool
    requires_payment: bool
    already_paid: bool
    free_publications_limit: int
    free_publications_consumed: int
    free_remaining: int
    amount: Decimal | None
    currency: str


def get_publication_price() -> Decimal:
    """Return the configured publication price in HTG."""
    from django.conf import settings
    return Decimal(str(getattr(settings, "PUBLICATION_PRICE_HTG", "500.00")))


def check_publication_eligibility(property_obj, user=None) -> PublicationEligibility:
    """Evaluate whether a property listing can be published for free or requires payment.

    Official Business Rules:
    1. FREE_PUBLICATIONS_LIMIT = 1 free publication per OWNER.
    2. An owner's first eligible publication is free.
    3. Starting from the 2nd publication, payment is required (via MonCash).
    4. Drafts, rejected, or un-published listings never consume a free publication slot.
    5. A property already published in its lifecycle (`published_at IS NOT NULL`) does not
       trigger a new payment on edits or resubmissions.
    6. A property with a Payment in `PAID` status is considered paid and never billed twice.
    """
    owner = property_obj.owner

    # Check if this specific property has already been paid for
    already_paid = Payment.objects.filter(
        property=property_obj,
        status=Payment.Status.PAID,
    ).exists()

    # Check if this property was already published in its lifecycle
    already_published = property_obj.published_at is not None

    # Count free publications already consumed (published) or currently in review without payment
    # Published properties that were paid do not count towards the free limit.
    consumed_published_qs = Property.objects.filter(
        owner=owner,
        published_at__isnull=False,
    ).exclude(payments__status=Payment.Status.PAID)

    pending_free_qs = Property.objects.filter(
        owner=owner,
        status=Property.Status.PENDING_REVIEW,
        published_at__isnull=True,
    ).exclude(payments__status=Payment.Status.PAID)

    if not already_published:
        consumed_published_qs = consumed_published_qs.exclude(pk=property_obj.pk)
        pending_free_qs = pending_free_qs.exclude(pk=property_obj.pk)

    consumed_count = consumed_published_qs.distinct().count()
    pending_count = pending_free_qs.distinct().count()

    total_used_free = consumed_count + pending_count
    free_consumed = min(FREE_PUBLICATIONS_LIMIT, total_used_free)
    free_remaining = max(0, FREE_PUBLICATIONS_LIMIT - total_used_free)

    # Determine eligibility flags
    if already_paid:
        is_free = False
        requires_payment = False
    elif already_published:
        # Resubmission / update of an already published property
        is_free = True
        requires_payment = False
    elif free_remaining > 0:
        is_free = True
        requires_payment = False
    else:
        is_free = False
        requires_payment = True

    amount = get_publication_price() if requires_payment else None

    return PublicationEligibility(
        property_id=str(property_obj.pk),
        is_free=is_free,
        requires_payment=requires_payment,
        already_paid=already_paid,
        free_publications_limit=FREE_PUBLICATIONS_LIMIT,
        free_publications_consumed=free_consumed,
        free_remaining=free_remaining,
        amount=amount,
        currency=DEFAULT_PUBLICATION_CURRENCY,
    )


def sync_all_pending_moncash_payments(*, max_age_hours=48, min_age_seconds=30) -> dict:
    """Vérifie et synchronise tous les paiements PENDING MonCash auprès de la passerelle.

    Permet de rattraper automatiquement les transactions abandonnées par les utilisateurs
    qui ont fermé leur navigateur après le paiement sans revenir sur le site.
    """
    from datetime import timedelta
    from .moncash_service import MonCashService, MonCashError
    import logging

    now = timezone.now()
    earliest = now - timedelta(hours=max_age_hours)
    latest = now - timedelta(seconds=min_age_seconds)

    pending_payments = (
        Payment.objects.filter(
            provider="MONCASH",
            status=Payment.Status.PENDING,
            created_at__gte=earliest,
            created_at__lte=latest,
        )
        .order_by("created_at")
    )

    stats = {
        "total_checked": 0,
        "paid": 0,
        "failed": 0,
        "still_pending": 0,
        "errors": 0,
    }

    service = MonCashService()
    log = logging.getLogger(__name__)

    for payment in pending_payments:
        stats["total_checked"] += 1
        try:
            confirmed = service.confirm_payment(payment.pk)
            if confirmed.status == Payment.Status.PAID:
                stats["paid"] += 1
                log.info("Paiement %s synchronisé avec succès: PAID", payment.order_id)
            elif confirmed.status == Payment.Status.FAILED:
                stats["failed"] += 1
                log.info("Paiement %s synchronisé: FAILED", payment.order_id)
            else:
                stats["still_pending"] += 1
        except (MonCashError, MonetizationError, Exception) as exc:
            log.error("Erreur de synchronisation MonCash pour le paiement %s: %s", payment.order_id, exc)
            stats["errors"] += 1

    return stats



