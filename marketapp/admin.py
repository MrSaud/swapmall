from django.contrib import admin

from .models import Cart, CartItem, Order, OrderItem, ProductReview, WishlistItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "vendor", "customer_name", "status", "total_amount", "created_at")
    list_filter = ("vendor", "status")
    search_fields = ("order_number", "customer_name", "customer_email")
    inlines = [OrderItemInline]


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("order", "product_name", "quantity", "unit_price", "line_total")
    search_fields = ("order__order_number", "product_name")


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "session_key", "updated_at")
    search_fields = ("user__username", "session_key")


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ("cart", "product", "quantity", "created_at")
    search_fields = ("cart__user__username", "product__name")


@admin.register(ProductReview)
class ProductReviewAdmin(admin.ModelAdmin):
    list_display = ("product", "rating", "reviewer_name", "user", "created_at")
    list_filter = ("rating",)
    search_fields = ("product__name", "reviewer_name", "comment")


@admin.register(WishlistItem)
class WishlistItemAdmin(admin.ModelAdmin):
    list_display = ("user", "product", "created_at")
    search_fields = ("user__username", "product__name")
