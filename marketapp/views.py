from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.contrib import messages
from django.db.models import Avg, Count, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from SWAPMALL.secure_params import InvalidToken, decrypt_int, decrypt_value, encrypt_int, encrypt_value
from vendorapp.access import (
    audit_log,
    get_theme_values,
    get_vendor_for_request,
    has_module_permission,
    sync_vendor_license_status,
    user_vendor_queryset,
    vendor_staff_required,
)
from vendorapp.pricing import apply_best_offer_to_products, product_effective_price
from vendorapp.models import HeroSlide, Product, Vendor, VendorCategory, VendorSettings

from .forms import OrderStatusForm, ProductReviewForm
from .models import Cart, CartItem, DigitalDownload, Order, OrderItem, ProductReview, WishlistItem


def _redirect_with_vendor(request, route_name, **kwargs):
    vendor_id = request.GET.get("vendor") or request.POST.get("selected_vendor_id") or request.POST.get("vendor")
    url = reverse(route_name, kwargs=kwargs or None)
    if vendor_id:
        return redirect(f"{url}?vendor={vendor_id}")
    return redirect(url)


def _decode_pk_token(token):
    try:
        return decrypt_int(token)
    except InvalidToken as exc:
        raise Http404("Invalid token") from exc


def _decode_text_token(value):
    if not value:
        return ""
    try:
        return decrypt_value(value).strip()
    except InvalidToken:
        return str(value).strip()


def _decode_int_token(value, default=None):
    if not value:
        return default
    try:
        return decrypt_int(value)
    except InvalidToken:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default


def _decode_decimal_token(value):
    raw = _decode_text_token(value)
    if not raw:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _get_or_create_cart(request):
    if request.user.is_authenticated:
        cart, _ = Cart.objects.get_or_create(user=request.user, defaults={"session_key": ""})
        return cart
    if not request.session.session_key:
        request.session.create()
    cart, _ = Cart.objects.get_or_create(session_key=request.session.session_key, user__isnull=True)
    return cart


def _cart_summary(cart):
    items = list(cart.items.select_related("product", "product__vendor"))
    apply_best_offer_to_products([item.product for item in items])
    subtotal = Decimal("0.00")
    for item in items:
        effective_price = getattr(item.product, "display_price", item.product.price)
        item.effective_price = effective_price
        item.original_price = getattr(item.product, "original_price", item.product.price)
        item.has_discount = getattr(item.product, "has_discount", False)
        item.discount_amount = getattr(item.product, "discount_amount", Decimal("0.00"))
        item.line_total_display = effective_price * item.quantity
        subtotal += item.line_total_display
    vendor_ids = {item.product.vendor_id for item in items}
    subtotal_vendor = items[0].product.vendor if len(vendor_ids) == 1 and items else None
    mixed_currencies = len(vendor_ids) > 1
    return items, subtotal, subtotal_vendor, mixed_currencies


def _recently_viewed_ids(request):
    ids = request.session.get("recent_products") or []
    if not isinstance(ids, list):
        return []
    cleaned = []
    for item in ids:
        try:
            cleaned.append(int(item))
        except (TypeError, ValueError):
            continue
    return cleaned


def _push_recently_viewed(request, product_id):
    ids = _recently_viewed_ids(request)
    ids = [pid for pid in ids if pid != product_id]
    ids.insert(0, product_id)
    request.session["recent_products"] = ids[:20]


def _collect_filters(request):
    sync_vendor_license_status()
    vendors = Vendor.objects.filter(is_active=True).order_by("name")

    vendor_key = request.GET.get("vendor")
    selected_vendor = None
    vendor_id = _decode_int_token(vendor_key)
    if vendor_id:
        selected_vendor = vendors.filter(id=vendor_id).first()

    category_id = _decode_int_token(request.GET.get("category"))
    search_query = _decode_text_token(request.GET.get("q"))
    sort_key = _decode_text_token(request.GET.get("sort")) or "newest"
    if sort_key not in {"newest", "price_asc", "price_desc", "best_selling"}:
        sort_key = "newest"
    segment = _decode_text_token(request.GET.get("segment")) or "all"
    if segment not in {"all", "new", "trending", "digital", "deals"}:
        segment = "all"
    digital_only = _decode_text_token(request.GET.get("digital_only")) in {"1", "true", "yes"}

    min_price = _decode_decimal_token(request.GET.get("min_price"))
    max_price = _decode_decimal_token(request.GET.get("max_price"))

    limit = _decode_int_token(request.GET.get("limit"), default=100) or 100
    limit = max(20, min(limit, 1000))

    product_qs = (
        Product.objects.select_related("vendor", "vendor__settings", "category_ref")
        .prefetch_related("gallery")
        .filter(is_active=True, vendor__is_active=True)
        .annotate(
            avg_rating=Avg("reviews__rating"),
            reviews_count=Count("reviews", distinct=True),
            sold_count=Sum("orderitem__quantity"),
        )
    )

    if selected_vendor:
        product_qs = product_qs.filter(vendor=selected_vendor)
    if category_id:
        product_qs = product_qs.filter(category_ref_id=category_id)
    if min_price is not None:
        product_qs = product_qs.filter(price__gte=min_price)
    if max_price is not None:
        product_qs = product_qs.filter(price__lte=max_price)
    if digital_only:
        product_qs = product_qs.filter(is_digital=True)
    if search_query:
        product_qs = product_qs.filter(
            Q(name__icontains=search_query)
            | Q(category_ref__name__icontains=search_query)
            | Q(category__icontains=search_query)
            | Q(vendor__name__icontains=search_query)
        )
    if segment == "digital":
        product_qs = product_qs.filter(is_digital=True)
    elif segment == "deals":
        now = timezone.now()
        product_qs = product_qs.filter(
            offers__is_active=True,
            offers__starts_at__lte=now,
            offers__ends_at__gte=now,
        ).distinct()
    elif segment == "trending":
        sort_key = "best_selling"
    elif segment == "new":
        sort_key = "newest"

    if sort_key == "price_asc":
        product_qs = product_qs.order_by("price", "-created_at")
    elif sort_key == "price_desc":
        product_qs = product_qs.order_by("-price", "-created_at")
    elif sort_key == "best_selling":
        product_qs = product_qs.annotate(sold_count=Sum("orderitem__quantity")).order_by("-sold_count", "-created_at")
    else:
        product_qs = product_qs.order_by("-created_at")

    total_count = product_qs.count()
    products = list(product_qs[:limit])
    apply_best_offer_to_products(products)
    for product in products:
        product.viewing_count = (product.id % 14) + 2
    has_next = total_count > limit

    vendor_logo_url = None
    social_links = {}
    if selected_vendor:
        vendor_settings = VendorSettings.objects.filter(vendor=selected_vendor).first()
        if vendor_settings and vendor_settings.vendor_logo:
            vendor_logo_url = vendor_settings.vendor_logo.url
        if vendor_settings:
            social_links = {
                "instagram": vendor_settings.instagram_url,
                "facebook": vendor_settings.facebook_url,
                "tiktok": vendor_settings.tiktok_url,
                "x": vendor_settings.x_url,
                "youtube": vendor_settings.youtube_url,
            }

    categories_qs = VendorCategory.objects.filter(is_active=True, products__is_active=True, vendor__is_active=True).distinct()
    if selected_vendor:
        categories_qs = categories_qs.filter(vendor=selected_vendor)

    wishlisted_ids = set()
    if request.user.is_authenticated:
        wishlisted_ids = set(
            WishlistItem.objects.filter(user=request.user, product_id__in=[p.id for p in products]).values_list(
                "product_id", flat=True
            )
        )

    active_filters = []
    if selected_vendor:
        active_filters.append({"label": f"Vendor: {selected_vendor.name}", "key": "vendor"})
    if category_id:
        category_obj = categories_qs.filter(id=category_id).first() or VendorCategory.objects.filter(id=category_id).first()
        if category_obj:
            active_filters.append({"label": f"Category: {category_obj.name}", "key": "category"})
    if search_query:
        active_filters.append({"label": f"Search: {search_query}", "key": "q"})
    if min_price is not None:
        active_filters.append({"label": f"Min: {min_price}", "key": "min_price"})
    if max_price is not None:
        active_filters.append({"label": f"Max: {max_price}", "key": "max_price"})
    if digital_only:
        active_filters.append({"label": "Digital only", "key": "digital_only"})
    if segment != "all":
        segment_labels = {
            "new": "Segment: New",
            "trending": "Segment: Trending",
            "digital": "Segment: Digital",
            "deals": "Segment: Deals",
        }
        active_filters.append({"label": segment_labels.get(segment, segment), "key": "segment"})
    if sort_key and sort_key != "newest":
        labels = {
            "price_asc": "Sort: Price low-high",
            "price_desc": "Sort: Price high-low",
            "best_selling": "Sort: Best selling",
        }
        active_filters.append({"label": labels.get(sort_key, sort_key), "key": "sort"})

    recent_ids = _recently_viewed_ids(request)
    recent_map = {
        p.id: p
        for p in Product.objects.select_related("vendor", "vendor__settings", "category_ref").prefetch_related("gallery").filter(
            id__in=recent_ids, is_active=True, vendor__is_active=True
        )
    }
    recently_viewed = [recent_map[pid] for pid in recent_ids if pid in recent_map][:8]
    apply_best_offer_to_products(recently_viewed)

    may_like = []
    if recent_ids:
        base_cats = list(
            Product.objects.filter(id__in=recent_ids, category_ref__isnull=False).values_list("category_ref_id", flat=True)
        )
        if base_cats:
            may_like = list(
                Product.objects.select_related("vendor", "vendor__settings", "category_ref")
                .prefetch_related("gallery")
                .filter(is_active=True, vendor__is_active=True, category_ref_id__in=base_cats)
                .exclude(id__in=recent_ids)
                .order_by("-created_at")[:8]
            )
    apply_best_offer_to_products(may_like)

    popular_products = list(
        Product.objects.select_related("vendor", "vendor__settings", "category_ref")
        .prefetch_related("gallery")
        .filter(is_active=True, vendor__is_active=True)
        .annotate(sold_count=Sum("orderitem__quantity"))
        .order_by("-sold_count", "-created_at")[:8]
    )
    apply_best_offer_to_products(popular_products)

    vendor_spotlights = []
    spotlight_vendors = Vendor.objects.filter(is_active=True)
    if selected_vendor:
        spotlight_vendors = spotlight_vendors.filter(id=selected_vendor.id)
    spotlight_vendors = list(spotlight_vendors.order_by("name")[:6])
    settings_map = {s.vendor_id: s for s in VendorSettings.objects.filter(vendor_id__in=[v.id for v in spotlight_vendors])}
    for vendor in spotlight_vendors:
        vendor_top = list(
            Product.objects.select_related("vendor", "vendor__settings")
            .prefetch_related("gallery")
            .filter(vendor=vendor, is_active=True)
            .annotate(sold_count=Sum("orderitem__quantity"))
            .order_by("-sold_count", "-created_at")[:3]
        )
        apply_best_offer_to_products(vendor_top)
        logo_url = ""
        vendor_settings = settings_map.get(vendor.id)
        if vendor_settings and vendor_settings.vendor_logo:
            logo_url = vendor_settings.vendor_logo.url
        vendor_spotlights.append(
            {
                "vendor": vendor,
                "logo_url": logo_url,
                "top_products": vendor_top,
            }
        )

    promo_title = f"Featured from {selected_vendor.name}" if selected_vendor else "Discover New Arrivals"
    promo_subtitle = (
        "Curated products, refreshed daily. Shop with one-tap cart and fast checkout."
        if not selected_vendor
        else f"Explore top picks and latest items from {selected_vendor.name}."
    )
    hero_slides = [
        {"title": promo_title, "subtitle": promo_subtitle},
        {"title": "Trending picks this week", "subtitle": "Updated by sales and ratings to help you discover what is hot."},
        {
            "title": "Digital and instant products",
            "subtitle": "Buy and download instantly with secure checkout and verified vendors.",
        },
    ]
    if selected_vendor:
        vendor_slides = list(
            HeroSlide.objects.filter(vendor=selected_vendor, is_active=True)
            .order_by("sort_order", "id")
            .values("title", "subtitle")[:8]
        )
        if vendor_slides:
            hero_slides = vendor_slides

    return {
        "products": products,
        "loaded_count": len(products),
        "has_next": has_next,
        "next_limit": limit + 100 if has_next else limit,
        "total_count": total_count,
        "vendors": vendors,
        "selected_vendor": selected_vendor,
        "vendor_logo_url": vendor_logo_url,
        "social_links": social_links,
        "theme": get_theme_values(selected_vendor),
        "search_query": search_query,
        "sort_key": sort_key,
        "segment": segment,
        "digital_only": digital_only,
        "selected_category_id": category_id,
        "min_price": min_price,
        "max_price": max_price,
        "categories": categories_qs.order_by("name"),
        "active_filters": active_filters,
        "wishlisted_ids": wishlisted_ids,
        "promo_title": promo_title,
        "promo_subtitle": promo_subtitle,
        "hero_slides": hero_slides,
        "recently_viewed": recently_viewed,
        "may_like": may_like,
        "popular_products": popular_products,
        "vendor_spotlights": vendor_spotlights,
    }


def _redirect_home_from_post(request):
    q_value = (request.POST.get("q") or "").strip()
    query_parts = []

    vendor_value = request.POST.get("vendor") or ""
    category_value = request.POST.get("category") or ""
    limit_value = request.POST.get("limit") or ""

    if vendor_value:
        query_parts.append(f"vendor={vendor_value}")
    if category_value:
        query_parts.append(f"category={category_value}")
    if q_value:
        query_parts.append(f"q={encrypt_value(q_value)}")
    if limit_value:
        query_parts.append(f"limit={limit_value}")
    segment_value = (request.POST.get("segment") or "").strip()
    if segment_value:
        query_parts.append(f"segment={encrypt_value(segment_value)}")
    digital_only_value = (request.POST.get("digital_only") or "").strip()
    if digital_only_value:
        query_parts.append(f"digital_only={encrypt_value(digital_only_value)}")

    sort_value = (request.POST.get("sort") or "").strip()
    if sort_value:
        query_parts.append(f"sort={encrypt_value(sort_value)}")

    min_price_value = (request.POST.get("min_price") or "").strip()
    max_price_value = (request.POST.get("max_price") or "").strip()
    if min_price_value:
        query_parts.append(f"min_price={encrypt_value(min_price_value)}")
    if max_price_value:
        query_parts.append(f"max_price={encrypt_value(max_price_value)}")

    redirect_url = reverse("marketapp:home")
    if query_parts:
        redirect_url = f"{redirect_url}?{'&'.join(query_parts)}"
    return redirect(redirect_url)


def market_home(request):
    if request.method == "POST":
        return _redirect_home_from_post(request)

    context = _collect_filters(request)
    cart = _get_or_create_cart(request)
    _, cart_total, cart_total_vendor, mixed_cart_currency = _cart_summary(cart)
    context["cart_count"] = cart.items.count()
    context["cart_total"] = cart_total
    context["cart_total_vendor"] = cart_total_vendor
    context["mixed_cart_currency"] = mixed_cart_currency
    return render(request, "marketapp/home.html", context)


def market_product_grid(request):
    context = _collect_filters(request)
    html = render_to_string("marketapp/_product_cards.html", context, request=request)
    return JsonResponse(
        {
            "html": html,
            "has_next": context["has_next"],
            "next_limit": encrypt_int(context["next_limit"]) if context["has_next"] else "",
        }
    )


def wishlist_toggle(request, token):
    if request.method != "POST":
        return redirect("marketapp:home")

    next_url = request.POST.get("next") or reverse("marketapp:home")
    if not str(next_url).startswith("/"):
        next_url = reverse("marketapp:home")

    if not request.user.is_authenticated:
        messages.warning(request, "Please login to use wishlist.")
        return redirect(next_url)

    product = get_object_or_404(Product, pk=_decode_pk_token(token), is_active=True, vendor__is_active=True)
    item, created = WishlistItem.objects.get_or_create(user=request.user, product=product)
    if created:
        messages.success(request, f"{product.name} added to wishlist.")
    else:
        item.delete()
        messages.success(request, f"{product.name} removed from wishlist.")
    return redirect(next_url)


def product_detail(request, token):
    product = get_object_or_404(
        Product.objects.select_related("vendor", "category_ref").prefetch_related("gallery"),
        pk=_decode_pk_token(token),
        is_active=True,
        vendor__is_active=True,
    )
    _push_recently_viewed(request, product.id)

    if request.method == "POST":
        form = ProductReviewForm(request.POST)
        if form.is_valid():
            review = form.save(commit=False)
            review.product = product
            if request.user.is_authenticated:
                review.user = request.user
                if not review.reviewer_name:
                    review.reviewer_name = request.user.get_username()
            review.save()
            messages.success(request, "Thank you, your review was submitted.")
            return redirect("marketapp:product-detail", token=token)
    else:
        form = ProductReviewForm()

    related_products = (
        Product.objects.select_related("vendor", "category_ref")
        .filter(vendor=product.vendor, is_active=True)
        .exclude(pk=product.pk)
        .annotate(avg_rating=Avg("reviews__rating"), reviews_count=Count("reviews", distinct=True))
        .order_by("-created_at")[:8]
    )
    related_products = list(related_products)
    apply_best_offer_to_products(related_products)
    apply_best_offer_to_products([product])

    reviews = product.reviews.select_related("user")[:20]
    rating_stats = product.reviews.aggregate(avg=Avg("rating"), count=Count("id"))
    is_wishlisted = False
    if request.user.is_authenticated:
        is_wishlisted = WishlistItem.objects.filter(user=request.user, product=product).exists()

    context = {
        "product": product,
        "related_products": related_products,
        "theme": get_theme_values(product.vendor),
        "cart_count": _get_or_create_cart(request).items.count(),
        "form": form,
        "reviews": reviews,
        "avg_rating": rating_stats["avg"] or Decimal("0"),
        "review_count": rating_stats["count"] or 0,
        "is_wishlisted": is_wishlisted,
    }
    return render(request, "marketapp/product_detail.html", context)


def cart_add(request, token):
    if request.method != "POST":
        return redirect("marketapp:home")

    product = get_object_or_404(Product, pk=_decode_pk_token(token), is_active=True, vendor__is_active=True)
    cart = _get_or_create_cart(request)
    try:
        quantity = int(request.POST.get("quantity", 1))
    except (TypeError, ValueError):
        quantity = 1
    quantity = max(1, min(quantity, 99))

    item, created = CartItem.objects.get_or_create(cart=cart, product=product, defaults={"quantity": quantity})
    if not created:
        item.quantity = min(99, item.quantity + quantity)
        item.save(update_fields=["quantity"])

    messages.success(request, f"{product.name} added to cart.")
    next_url = request.POST.get("next") or reverse("marketapp:home")
    if not str(next_url).startswith("/"):
        next_url = reverse("marketapp:home")
    return redirect(next_url)


def cart_view(request):
    cart = _get_or_create_cart(request)
    items, subtotal, subtotal_vendor, mixed_currencies = _cart_summary(cart)
    context = {
        "cart": cart,
        "items": items,
        "subtotal": subtotal,
        "subtotal_vendor": subtotal_vendor,
        "mixed_currencies": mixed_currencies,
        "cart_count": len(items),
        "theme": get_theme_values(subtotal_vendor),
    }
    return render(request, "marketapp/cart.html", context)


def cart_item_update(request, token):
    if request.method != "POST":
        return redirect("marketapp:cart")

    cart = _get_or_create_cart(request)
    item = get_object_or_404(CartItem.objects.select_related("product"), pk=_decode_pk_token(token), cart=cart)
    try:
        quantity = int(request.POST.get("quantity", item.quantity))
    except (TypeError, ValueError):
        quantity = item.quantity

    if quantity <= 0:
        item.delete()
        messages.success(request, "Item removed from cart.")
    else:
        item.quantity = min(quantity, 99)
        item.save(update_fields=["quantity"])
        messages.success(request, "Cart item updated.")
    return redirect("marketapp:cart")


def cart_item_remove(request, token):
    if request.method != "POST":
        return redirect("marketapp:cart")
    cart = _get_or_create_cart(request)
    item = get_object_or_404(CartItem, pk=_decode_pk_token(token), cart=cart)
    item.delete()
    messages.success(request, "Item removed from cart.")
    return redirect("marketapp:cart")


def checkout(request):
    cart = _get_or_create_cart(request)
    items, subtotal, subtotal_vendor, mixed_currencies = _cart_summary(cart)
    if not items:
        messages.warning(request, "Your cart is empty.")
        return redirect("marketapp:home")

    if request.method == "POST":
        customer_name = (request.POST.get("customer_name") or "").strip()
        customer_email = (request.POST.get("customer_email") or "").strip()
        if not customer_name:
            messages.error(request, "Customer name is required.")
            return redirect("marketapp:checkout")

        by_vendor = {}
        for item in items:
            by_vendor.setdefault(item.product.vendor_id, []).append(item)

        created_orders = []
        created_order_ids = []
        created_download_ids = []
        for _, vendor_items in by_vendor.items():
            vendor = vendor_items[0].product.vendor
            order_number = f"MKT-{timezone.now().strftime('%Y%m%d%H%M%S')}-{vendor.id}-{uuid4().hex[:6].upper()}"
            order = Order.objects.create(
                vendor=vendor,
                order_number=order_number,
                customer_name=customer_name,
                customer_email=customer_email,
                status=Order.STATUS_PENDING,
            )
            total = Decimal("0.00")
            for item in vendor_items:
                unit_price = product_effective_price(item.product)
                line_total = unit_price * item.quantity
                total += line_total
                OrderItem.objects.create(
                    order=order,
                    product=item.product,
                    product_name=item.product.name,
                    unit_price=unit_price,
                    quantity=item.quantity,
                    line_total=line_total,
                )
                if item.product and not item.product.is_digital:
                    remaining = max(0, item.product.stock_quantity - item.quantity)
                    item.product.stock_quantity = remaining
                    item.product.save(update_fields=["stock_quantity", "updated_at"])
            order.total_amount = total
            order.save(update_fields=["total_amount", "updated_at"])
            created_orders.append(order.order_number)
            created_order_ids.append(order.id)

            for order_item in order.items.select_related("product"):
                product = order_item.product
                if product and product.is_digital and product.digital_file:
                    download = DigitalDownload.objects.create(order_item=order_item)
                    created_download_ids.append(download.id)

        cart.items.all().delete()
        request.session["last_order_ids"] = created_order_ids
        request.session["last_download_ids"] = created_download_ids
        request.session["last_order_numbers"] = created_orders
        return redirect("marketapp:checkout-success")

    context = {
        "items": items,
        "subtotal": subtotal,
        "subtotal_vendor": subtotal_vendor,
        "mixed_currencies": mixed_currencies,
        "cart_count": len(items),
        "theme": get_theme_values(subtotal_vendor),
    }
    return render(request, "marketapp/checkout.html", context)


def checkout_success(request):
    order_ids = request.session.get("last_order_ids") or []
    download_ids = request.session.get("last_download_ids") or []
    order_numbers = request.session.get("last_order_numbers") or []

    orders = Order.objects.filter(id__in=order_ids).select_related("vendor")
    downloads = (
        DigitalDownload.objects.filter(id__in=download_ids)
        .select_related("order_item", "order_item__product", "order_item__order", "order_item__order__vendor")
        .order_by("-created_at")
    )
    downloads = list(downloads)
    for d in downloads:
        d.remaining_downloads = max(0, d.max_downloads - d.downloaded_count)
    context = {
        "orders": orders,
        "order_numbers": order_numbers,
        "downloads": downloads,
        "theme": get_theme_values(None),
    }
    return render(request, "marketapp/checkout_success.html", context)


def digital_download(request, token):
    download = get_object_or_404(
        DigitalDownload.objects.select_related("order_item", "order_item__product", "order_item__order"),
        pk=_decode_pk_token(token),
    )
    product = download.order_item.product
    if not product or not product.is_digital or not product.digital_file:
        messages.error(request, "Digital file is not available.")
        return redirect("marketapp:home")
    if not download.is_active:
        messages.error(request, "Download is not active.")
        return redirect("marketapp:home")
    if download.downloaded_count >= download.max_downloads:
        messages.error(request, "Download limit reached for this file.")
        return redirect("marketapp:home")

    download.downloaded_count += 1
    download.last_downloaded_at = timezone.now()
    download.save(update_fields=["downloaded_count", "last_downloaded_at"])
    return redirect(product.digital_file.url)


@vendor_staff_required
def order_list(request):
    selected_vendor = get_vendor_for_request(request)
    vendors = user_vendor_queryset(request.user)
    if selected_vendor and not has_module_permission(request.user, selected_vendor, "orders", "view"):
        messages.error(request, "You do not have permission to view orders for this vendor.")
        return redirect("vendorapp:dashboard")

    orders = Order.objects.select_related("vendor").prefetch_related("items").filter(vendor__in=vendors)
    if selected_vendor:
        orders = orders.filter(vendor=selected_vendor)

    context = {
        "vendors": vendors,
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "orders": orders,
    }
    return render(request, "marketapp/order_list.html", context)


@vendor_staff_required
def order_detail(request, token):
    selected_vendor = get_vendor_for_request(request)
    vendors = user_vendor_queryset(request.user)

    order_qs = Order.objects.select_related("vendor").prefetch_related("items").filter(vendor__in=vendors)
    if not request.user.is_superuser and selected_vendor:
        order_qs = order_qs.filter(vendor=selected_vendor)
    order = get_object_or_404(order_qs, pk=_decode_pk_token(token))
    if not has_module_permission(request.user, order.vendor, "orders", "view"):
        messages.error(request, "You do not have permission to view this order.")
        return redirect("vendorapp:dashboard")

    if request.method == "POST":
        if not has_module_permission(request.user, order.vendor, "orders", "update"):
            messages.error(request, "You do not have permission to update this order.")
            return _redirect_with_vendor(request, "marketapp:order-detail", token=encrypt_int(order.pk))
        form = OrderStatusForm(request.POST, instance=order)
        if form.is_valid():
            form.save()
            audit_log(
                request,
                action="order.status_update",
                vendor=order.vendor,
                target_model="Order",
                target_id=order.id,
                details=f"status={order.status}",
            )
            messages.success(request, "Order status updated.")
            return _redirect_with_vendor(request, "marketapp:order-detail", token=encrypt_int(order.pk))
    else:
        form = OrderStatusForm(instance=order)

    context = {
        "vendors": vendors,
        "selected_vendor": selected_vendor or order.vendor,
        "theme": get_theme_values(selected_vendor or order.vendor),
        "order": order,
        "form": form,
    }
    return render(request, "marketapp/order_detail.html", context)
