from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.locations.models import Location
from apps.monetization.models import Invoice, Payment, Plan, PropertyPromotion, Subscription
from apps.monetization.services import (
    MonetizationError, PaymentService, PaymentVerificationError, ProviderCheckout,
    ProviderVerification, cancel_subscription, create_property_promotion,
    create_subscription, create_subscription_payment,
)
from apps.properties.models import Property, PropertyType

User = get_user_model()
PASSWORD = "Secure-Password-521!"


class FakePaymentService(PaymentService):
    provider_name = "fake-provider"

    def __init__(self, result=None):
        self.result = result
        self.verify_calls = 0

    def create_provider_checkout(self, payment):
        return ProviderCheckout(provider_payment_id=f"external-{payment.pk}", checkout_url="https://provider.test/checkout")

    def verify_with_provider(self, payment):
        self.verify_calls += 1
        return self.result


@pytest.fixture
def money_data(db):
    user = User.objects.create_user(email="money-user@example.com", password=PASSWORD, role=User.Role.OWNER)
    other = User.objects.create_user(email="money-other@example.com", password=PASSWORD, role=User.Role.OWNER)
    admin = User.objects.create_user(email="money-admin@example.com", password=PASSWORD, role=User.Role.ADMIN)
    free = Plan.objects.get(code=Plan.Code.FREE)
    pro = Plan.objects.create(
        code=Plan.Code.PRO, name="Pro", price="19.99", currency="USD", can_promote_properties=True
    )
    business = Plan.objects.create(code=Plan.Code.BUSINESS, name="Business", price="49.00", currency="USD")
    property_type = PropertyType.objects.create(name="Money house", slug="money-house")
    location = Location.objects.create(name="Money city", slug="money-city", type="CITY")
    property_obj = Property.objects.create(
        owner=user, title="Sponsored home", slug="sponsored-home", property_type=property_type,
        listing_type=Property.ListingType.RENT, price="900", currency="USD", location=location,
        status=Property.Status.PUBLISHED,
    )
    return {"user": user, "other": other, "admin": admin, "free": free, "pro": pro,
            "business": business, "property": property_obj}


@pytest.mark.django_db
def test_plan_types_and_free_paid_subscription_lifecycle(money_data):
    data = money_data
    assert set(Plan.Code.values) == {"FREE", "PRO", "BUSINESS"}
    free = create_subscription(user=data["user"], plan=data["free"])
    assert free.status == Subscription.Status.ACTIVE
    with pytest.raises(MonetizationError):
        create_subscription(user=data["user"], plan=data["business"])

    free = cancel_subscription(subscription=free, actor=data["user"])
    assert free.status == Subscription.Status.CANCELLED
    paid = create_subscription(
        user=data["user"], plan=data["pro"], ends_at=timezone.now() + timedelta(days=30)
    )
    assert paid.status == Subscription.Status.PAST_DUE
    assert paid.starts_at < paid.ends_at
    with pytest.raises(MonetizationError):
        create_subscription(user=data["other"], plan=data["pro"], ends_at=timezone.now())


@pytest.mark.django_db
def test_payment_is_pending_until_provider_verifies_and_then_activates_subscription(money_data):
    data = money_data
    subscription = create_subscription(user=data["user"], plan=data["pro"])
    payment = create_subscription_payment(subscription=subscription, provider="fake-provider")
    assert payment.status == Payment.Status.PENDING
    assert payment.amount == Decimal("19.99")
    assert payment.currency == "USD"
    assert payment.paid_at is None
    assert payment.provider_payment_id is None
    assert payment.invoice.status == Invoice.Status.ISSUED
    with pytest.raises(TypeError):
        create_subscription_payment(subscription=subscription, provider="fake-provider", status="PAID")

    service = FakePaymentService()
    checkout = service.start_payment(payment.pk)
    assert checkout.provider_payment_id == f"external-{payment.pk}"
    payment.refresh_from_db()
    assert payment.provider_payment_id == f"external-{payment.pk}"

    service.result = ProviderVerification(
        provider_payment_id=payment.provider_payment_id,
        amount=payment.amount,
        currency=payment.currency,
        is_paid=True,
        paid_at=timezone.now(),
    )
    verified = service.confirm_payment(payment.pk)
    assert verified.status == Payment.Status.PAID
    assert verified.paid_at is not None
    subscription.refresh_from_db()
    assert subscription.status == Subscription.Status.ACTIVE
    payment.invoice.refresh_from_db()
    assert payment.invoice.status == Invoice.Status.PAID
    assert payment.invoice.paid_at == verified.paid_at

    service.confirm_payment(payment.pk)
    assert service.verify_calls == 1


@pytest.mark.django_db
def test_payment_fails_closed_when_provider_result_is_pending_or_mismatched(money_data):
    subscription = create_subscription(user=money_data["user"], plan=money_data["pro"])
    payment = create_subscription_payment(subscription=subscription, provider="fake-provider")
    service = FakePaymentService()
    service.start_payment(payment.pk)
    payment.refresh_from_db()
    assert service.confirm_payment(payment.pk).status == Payment.Status.PENDING
    assert payment.status == Payment.Status.PENDING

    service.result = ProviderVerification(
        provider_payment_id=payment.provider_payment_id,
        amount=Decimal("1.00"), currency="USD", is_paid=True, paid_at=timezone.now(),
    )
    with pytest.raises(PaymentVerificationError):
        service.confirm_payment(payment.pk)
    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING
    assert payment.paid_at is None


@pytest.mark.django_db
def test_failed_provider_verification_does_not_activate_paid_plan(money_data):
    subscription = create_subscription(user=money_data["user"], plan=money_data["pro"])
    payment = create_subscription_payment(subscription=subscription, provider="fake-provider")
    service = FakePaymentService()
    service.start_payment(payment.pk)
    payment.refresh_from_db()
    service.result = ProviderVerification(
        provider_payment_id=payment.provider_payment_id,
        amount=payment.amount, currency=payment.currency, is_paid=False,
    )
    assert service.confirm_payment(payment.pk).status == Payment.Status.FAILED
    subscription.refresh_from_db()
    payment.invoice.refresh_from_db()
    assert subscription.status == Subscription.Status.PAST_DUE
    assert payment.invoice.status == Invoice.Status.VOID


@pytest.mark.django_db
def test_payment_service_is_abstract_and_rejects_wrong_provider(money_data):
    with pytest.raises(TypeError):
        PaymentService()
    subscription = create_subscription(user=money_data["user"], plan=money_data["pro"])
    payment = create_subscription_payment(subscription=subscription, provider="another-provider")
    with pytest.raises(MonetizationError):
        FakePaymentService().start_payment(payment.pk)


@pytest.mark.django_db
def test_property_promotion_requires_published_property_owner_and_valid_dates(money_data):
    data = money_data
    starts = timezone.now()
    Subscription.objects.create(
        user=data["user"], plan=data["pro"], status=Subscription.Status.ACTIVE, starts_at=starts
    )
    promotion = create_property_promotion(
        property_obj=data["property"], actor=data["user"],
        promotion_type=PropertyPromotion.Type.HOMEPAGE,
        starts_at=starts, ends_at=starts + timedelta(days=5),
    )
    assert promotion.status == PropertyPromotion.Status.PENDING

    with pytest.raises(MonetizationError):
        create_property_promotion(
            property_obj=data["property"], actor=data["other"],
            promotion_type=PropertyPromotion.Type.BOOST,
            starts_at=starts, ends_at=starts + timedelta(days=2),
        )
    with pytest.raises(MonetizationError):
        create_property_promotion(
            property_obj=data["property"], actor=data["user"],
            promotion_type=PropertyPromotion.Type.BOOST,
            starts_at=starts, ends_at=starts,
        )
    data["property"].status = Property.Status.DRAFT
    data["property"].save(update_fields=("status", "updated_at"))
    with pytest.raises(MonetizationError):
        create_property_promotion(
            property_obj=data["property"], actor=data["admin"],
            promotion_type=PropertyPromotion.Type.FEATURED,
            starts_at=starts, ends_at=starts + timedelta(days=1),
        )


@pytest.mark.django_db
def test_promotion_payment_uses_verified_provider_result_to_activate_and_invoice(money_data):
    from apps.monetization.services import create_promotion_payment

    data = money_data
    starts = timezone.now()
    Subscription.objects.create(
        user=data["user"], plan=data["pro"], status=Subscription.Status.ACTIVE, starts_at=starts
    )
    promotion = create_property_promotion(
        property_obj=data["property"], actor=data["user"],
        promotion_type=PropertyPromotion.Type.FEATURED,
        starts_at=starts, ends_at=starts + timedelta(days=3),
    )
    payment = create_promotion_payment(
        promotion=promotion, provider="fake-provider", amount="15.00", currency="USD"
    )
    assert payment.status == Payment.Status.PENDING
    assert payment.invoice.status == Invoice.Status.ISSUED
    service = FakePaymentService()
    service.start_payment(payment.pk)
    payment.refresh_from_db()
    service.result = ProviderVerification(
        provider_payment_id=payment.provider_payment_id,
        amount=payment.amount, currency=payment.currency,
        is_paid=True, paid_at=timezone.now(),
    )
    assert service.confirm_payment(payment.pk).status == Payment.Status.PAID
    promotion.refresh_from_db()
    assert promotion.status == PropertyPromotion.Status.ACTIVE


@pytest.mark.django_db
def test_payment_purchase_constraints_valid_and_invalid(money_data):
    data = money_data
    user = data["user"]
    prop = data["property"]
    sub = Subscription.objects.create(
        user=user, plan=data["pro"], status=Subscription.Status.PAST_DUE, starts_at=timezone.now()
    )
    promo = PropertyPromotion.objects.create(
        property=prop,
        promotion_type=PropertyPromotion.Type.FEATURED,
        status=PropertyPromotion.Status.PENDING,
        starts_at=timezone.now(),
        ends_at=timezone.now() + timedelta(days=1),
    )

    # 1. Valid: Subscription only
    pay_sub = Payment.objects.create(
        user=user, subscription=sub, provider="test", amount="10.00", currency="USD"
    )
    assert pay_sub.subscription == sub
    assert pay_sub.promotion is None
    assert pay_sub.property is None
    assert pay_sub.order_id.startswith("ORD-")

    # 2. Valid: Promotion only
    pay_promo = Payment.objects.create(
        user=user, promotion=promo, provider="test", amount="15.00", currency="USD"
    )
    assert pay_promo.subscription is None
    assert pay_promo.promotion == promo
    assert pay_promo.property is None
    assert pay_promo.order_id.startswith("ORD-")

    # 3. Valid: Property only
    pay_prop = Payment.objects.create(
        user=user, property=prop, provider="test", amount="20.00", currency="USD"
    )
    assert pay_prop.subscription is None
    assert pay_prop.promotion is None
    assert pay_prop.property == prop
    assert pay_prop.order_id.startswith("ORD-")

    # Invalid: No purchase attached
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Payment.objects.create(user=user, provider="test", amount="10.00", currency="USD")

    # Invalid: Subscription + Property
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Payment.objects.create(
                user=user, subscription=sub, property=prop, provider="test", amount="10.00", currency="USD"
            )

    # Invalid: Promotion + Property
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Payment.objects.create(
                user=user, promotion=promo, property=prop, provider="test", amount="10.00", currency="USD"
            )

    # Invalid: All three
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Payment.objects.create(
                user=user, subscription=sub, promotion=promo, property=prop, provider="test", amount="10.00", currency="USD"
            )


@pytest.mark.django_db
def test_property_payment_lifecycle_and_uniqueness(money_data):
    data = money_data
    user = data["user"]
    prop = data["property"]

    # Can have a PENDING payment
    pay1 = Payment.objects.create(
        user=user, property=prop, provider="test", amount="25.00", currency="USD", status=Payment.Status.PENDING
    )
    assert pay1.status == Payment.Status.PENDING

    # If first attempt fails, can have another payment attempt (e.g. FAILED + PENDING)
    pay1.status = Payment.Status.FAILED
    pay1.save(update_fields=["status"])

    pay2 = Payment.objects.create(
        user=user, property=prop, provider="test", amount="25.00", currency="USD", status=Payment.Status.PENDING
    )
    assert pay2.status == Payment.Status.PENDING

    # Mark pay2 as PAID
    pay2.status = Payment.Status.PAID
    pay2.paid_at = timezone.now()
    pay2.save(update_fields=["status", "paid_at"])

    # Cannot have a second PAID payment for the same property
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Payment.objects.create(
                user=user, property=prop, provider="test", amount="25.00", currency="USD", status=Payment.Status.PAID
            )


@pytest.mark.django_db
def test_create_property_publication_payment_service(money_data):
    from apps.monetization.services import create_property_publication_payment

    data = money_data
    prop = data["property"]

    # 1. Create property publication payment via service
    payment = create_property_publication_payment(
        property_obj=prop, provider="fake-provider", amount="25.00", currency="USD"
    )
    assert payment.status == Payment.Status.PENDING
    assert payment.property == prop
    assert payment.user == prop.owner
    assert payment.order_id.startswith("ORD-")
    assert payment.invoice.status == Invoice.Status.ISSUED
    assert payment.invoice.amount == Decimal("25.00")

    # 2. Confirm payment with service
    service = FakePaymentService()
    service.start_payment(payment.pk)
    payment.refresh_from_db()

    service.result = ProviderVerification(
        provider_payment_id=payment.provider_payment_id,
        amount=payment.amount,
        currency=payment.currency,
        is_paid=True,
        paid_at=timezone.now(),
    )
    confirmed = service.confirm_payment(payment.pk)
    assert confirmed.status == Payment.Status.PAID
    payment.invoice.refresh_from_db()
    assert payment.invoice.status == Invoice.Status.PAID

    # 3. Trying to create another publication payment for this property raises MonetizationError
    with pytest.raises(MonetizationError, match="déjà un paiement confirmé"):
        create_property_publication_payment(
            property_obj=prop, provider="fake-provider", amount="25.00", currency="USD"
        )
