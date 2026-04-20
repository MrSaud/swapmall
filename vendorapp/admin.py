from django.contrib import admin

from .models import (
    AuditLog,
    HeroSlide,
    Offer,
    Package,
    PackageInvoice,
    Product,
    ProductImage,
    SavedFilter,
    StaffInvite,
    StaffPermission,
    StockMovement,
    SupportTicket,
    Vendor,
    VendorCategory,
    VendorSettings,
    VendorStaff,
)


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")


@admin.register(VendorSettings)
class VendorSettingsAdmin(admin.ModelAdmin):
    list_display = ("vendor", "theme_name", "primary_color", "updated_at")
    list_filter = ("theme_name",)
    search_fields = ("vendor__name",)


@admin.register(VendorStaff)
class VendorStaffAdmin(admin.ModelAdmin):
    list_display = ("vendor", "user", "role", "is_active", "created_at")
    list_filter = ("role", "is_active", "vendor")
    search_fields = ("vendor__name", "user__username", "user__email")


@admin.register(VendorCategory)
class VendorCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "vendor", "is_active", "created_at")
    list_filter = ("vendor", "is_active")
    search_fields = ("name", "vendor__name")


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "vendor",
        "category_ref",
        "sku",
        "is_digital",
        "price",
        "stock_quantity",
        "is_active",
        "updated_at",
    )
    list_filter = ("vendor", "is_active", "category_ref")
    search_fields = ("name", "sku", "vendor__name", "category_ref__name")
    inlines = [ProductImageInline]


@admin.register(Package)
class PackageAdmin(admin.ModelAdmin):
    list_display = ("vendor", "name", "max_products", "starts_on", "ends_on", "is_active", "updated_at")
    list_filter = ("is_active", "starts_on", "ends_on")
    search_fields = ("vendor__name", "name")

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display = ("name", "vendor", "offer_type", "discount_type", "discount_value", "starts_at", "ends_at", "is_active")
    list_filter = ("vendor", "offer_type", "discount_type", "is_active")
    search_fields = ("name", "vendor__name")
    filter_horizontal = ("products",)


@admin.register(HeroSlide)
class HeroSlideAdmin(admin.ModelAdmin):
    list_display = ("title", "vendor", "sort_order", "is_active", "updated_at")
    list_filter = ("vendor", "is_active")
    search_fields = ("title", "subtitle", "vendor__name")


@admin.register(PackageInvoice)
class PackageInvoiceAdmin(admin.ModelAdmin):
    list_display = ("vendor", "package", "amount", "currency_code", "due_date", "is_paid", "created_at")
    list_filter = ("is_paid", "currency_code", "due_date")
    search_fields = ("vendor__name", "package__name")


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("title", "vendor", "status", "priority", "assigned_to", "updated_at")
    list_filter = ("status", "priority", "vendor")
    search_fields = ("title", "vendor__name", "description")


@admin.register(StaffPermission)
class StaffPermissionAdmin(admin.ModelAdmin):
    list_display = ("membership", "module", "action", "is_allowed", "updated_at")
    list_filter = ("module", "action", "is_allowed")
    search_fields = ("membership__user__username", "membership__vendor__name")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "vendor", "user", "target_model", "target_id", "ip_address")
    list_filter = ("action", "vendor")
    search_fields = ("action", "target_model", "target_id", "details", "user__username", "vendor__name")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("product", "movement_type", "quantity_delta", "created_by", "created_at")
    list_filter = ("movement_type", "product__vendor")
    search_fields = ("product__name", "product__vendor__name", "note")


@admin.register(SavedFilter)
class SavedFilterAdmin(admin.ModelAdmin):
    list_display = ("name", "page", "vendor", "user", "created_at")
    list_filter = ("page", "vendor")
    search_fields = ("name", "vendor__name", "user__username", "query_string")


@admin.register(StaffInvite)
class StaffInviteAdmin(admin.ModelAdmin):
    list_display = ("email", "vendor", "role", "status", "expires_at", "created_at")
    list_filter = ("status", "role", "vendor")
    search_fields = ("email", "vendor__name")
