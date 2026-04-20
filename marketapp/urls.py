from django.urls import path

from . import views

app_name = "marketapp"

urlpatterns = [
    path("", views.market_home, name="home"),
    path("products/grid/", views.market_product_grid, name="product-grid"),
    path("products/<str:token>/", views.product_detail, name="product-detail"),
    path("wishlist/toggle/<str:token>/", views.wishlist_toggle, name="wishlist-toggle"),
    path("cart/", views.cart_view, name="cart"),
    path("cart/add/<str:token>/", views.cart_add, name="cart-add"),
    path("cart/item/<str:token>/update/", views.cart_item_update, name="cart-item-update"),
    path("cart/item/<str:token>/remove/", views.cart_item_remove, name="cart-item-remove"),
    path("checkout/", views.checkout, name="checkout"),
    path("checkout/success/", views.checkout_success, name="checkout-success"),
    path("downloads/<str:token>/", views.digital_download, name="digital-download"),
    path("orders/", views.order_list, name="order-list"),
    path("orders/<str:token>/", views.order_detail, name="order-detail"),
]
