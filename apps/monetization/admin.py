from django.contrib import admin

from .models import Invoice, Payment, Plan, PropertyPromotion, Subscription


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "price", "currency", "max_active_properties", "can_promote_properties", "is_active")
    list_filter = ("is_active", "can_promote_properties", "code")
    search_fields = ("name", "code")


admin.site.register(Subscription)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "order_id",
        "user",
        "property",
        "subscription",
        "promotion",
        "provider",
        "amount",
        "currency",
        "status",
        "created_at",
    )
    list_filter = ("status", "provider", "currency")
    search_fields = ("order_id", "provider_payment_id", "provider_transaction_id", "user__email")


admin.site.register(PropertyPromotion)
admin.site.register(Invoice)
