from django.contrib import admin

from .models import Invoice, Payment, Plan, PropertyPromotion, Subscription


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "price", "currency", "max_active_properties", "can_promote_properties", "is_active")
    list_filter = ("is_active", "can_promote_properties", "code")
    search_fields = ("name", "code")


admin.site.register(Subscription)
admin.site.register(Payment)
admin.site.register(PropertyPromotion)
admin.site.register(Invoice)
