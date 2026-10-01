import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.monetization.models import Payment, Plan, Subscription

User = get_user_model()


@pytest.mark.django_db
def test_clients_cannot_mutate_subscription_or_mark_payment_paid(db):
    user = User.objects.create_user(email="security-client@example.com", password="Secure-Password-521!")
    plan = Plan.objects.get(code=Plan.Code.FREE)
    subscription = Subscription.objects.create(
        user=user, plan=plan, starts_at=timezone.now(), status=Subscription.Status.ACTIVE,
    )
    payment = Payment.objects.create(
        user=user, subscription=subscription, provider="not-connected", amount="0.00", currency="USD",
    )
    client = APIClient()
    client.force_authenticate(user)

    subscription_update = client.patch(
        f"/api/subscriptions/{subscription.pk}/", {"plan": str(plan.pk), "status": "ACTIVE"}, format="json"
    )
    payment_update = client.patch(
        f"/api/payments/{payment.pk}/", {"status": "PAID"}, format="json"
    )
    payment_creation = client.post("/api/payments/", {
        "provider": "not-connected", "status": "PAID", "amount": "0.00", "currency": "USD",
    }, format="json")

    assert subscription_update.status_code == 404
    assert payment_update.status_code == 404
    assert payment_creation.status_code == 404
    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING
