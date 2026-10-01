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
        payment = Payment.objects.select_related("subscription", "promotion").get(pk=payment_id)
        if payment.status != Payment.Status.PENDING:
            return payment
        result = self.verify_with_provider(payment)
        if result is None:
            return payment
        self._validate_provider_result(payment, result)

        with transaction.atomic():
            payment = Payment.objects.select_for_update().get(pk=payment_id)
            if payment.status != Payment.Status.PENDING:
                return payment
            payment.status = Payment.Status.PAID if result.is_paid else Payment.Status.FAILED
            payment.paid_at = (result.paid_at or timezone.now()) if result.is_paid else None
            payment.save(update_fields=("status", "paid_at", "updated_at"))

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
                invoice = Invoice.objects.select_for_update().get(payment=payment)
                invoice.status = Invoice.Status.PAID
                invoice.paid_at = payment.paid_at
                invoice.save(update_fields=("status", "paid_at", "updated_at"))
            else:
                invoice = Invoice.objects.select_for_update().get(payment=payment)
                invoice.status = Invoice.Status.VOID
                invoice.save(update_fields=("status", "updated_at"))
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
