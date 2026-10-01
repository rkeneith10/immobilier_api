from django.contrib import admin

from .models import OwnerProfile, OwnerRequest, OwnerVerification, User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("email", "role", "status", "is_staff", "created_at")
    list_filter = ("role", "status", "is_staff")
    search_fields = ("email", "first_name", "last_name", "phone")
    ordering = ("-created_at",)


@admin.register(OwnerProfile)
class OwnerProfileAdmin(admin.ModelAdmin):
    list_display = ("display_name", "user", "verification_status", "verified_at", "created_at")
    list_filter = ("verification_status",)
    search_fields = ("display_name", "business_name", "user__email")


@admin.register(OwnerVerification)
class OwnerVerificationAdmin(admin.ModelAdmin):
    list_display = ("owner_profile", "status", "reviewed_by", "reviewed_at", "created_at")
    list_filter = ("status",)
    search_fields = ("owner_profile__display_name", "owner_profile__user__email")


@admin.register(OwnerRequest)
class OwnerRequestAdmin(admin.ModelAdmin):
    list_display = ("full_name", "user", "request_type", "status", "reviewed_by", "reviewed_at", "created_at")
    list_filter = ("status", "request_type")
    search_fields = ("full_name", "phone", "user__email")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-created_at",)
