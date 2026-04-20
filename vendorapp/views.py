import csv
import secrets

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.db.models import Count, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from SWAPMALL.secure_params import InvalidToken, decrypt_int, encrypt_int
from marketapp.models import Order, ProductReview

from .access import (
    audit_log,
    get_theme_values,
    get_vendor_for_request,
    get_vendor_package_alarm,
    get_vendor_package_status,
    has_module_permission,
    user_has_vendor_access,
    user_vendor_queryset,
    vendor_staff_required,
)
from .forms import (
    HeroSlideForm,
    OfferForm,
    PackageForm,
    PackageInvoiceForm,
    ProductForm,
    ProductStockAdjustForm,
    SavedFilterForm,
    StaffInviteForm,
    StaffPermissionForm,
    SuperAdminVendorUserCreateForm,
    SupportTicketForm,
    VendorCategoryForm,
    VendorSettingsForm,
    VendorStaffCreateForm,
)
from .models import (
    AuditLog,
    HeroSlide,
    Offer,
    Package,
    PackageInvoice,
    Product,
    ProductImage,
    SavedFilter,
    StaffPermission,
    StaffInvite,
    StockMovement,
    SupportTicket,
    Vendor,
    VendorCategory,
    VendorSettings,
    VendorStaff,
)
from .qr import qr_image_src


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


def _superadmin_only(request):
    if request.user.is_superuser:
        return None
    messages.error(request, "Only superadmin can access this section.")
    return _redirect_with_vendor(request, "vendorapp:dashboard")


def _require_vendor(request, vendor):
    if vendor:
        return None
    messages.error(request, "Please select a vendor first.")
    return _redirect_with_vendor(request, "vendorapp:dashboard")


def _require_module_perm(request, vendor, module, action):
    if not vendor:
        return _require_vendor(request, vendor)
    if has_module_permission(request.user, vendor, module, action):
        return None
    messages.error(request, "You do not have permission for this action.")
    return _redirect_with_vendor(request, "vendorapp:dashboard")


def _export_csv(filename, headers, rows):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return response


def login_page(request):
    if request.user.is_authenticated:
        return redirect("vendorapp:dashboard")

    next_url = request.GET.get("next") or request.POST.get("next")

    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            if not user_has_vendor_access(user):
                logout(request)
                form.add_error(None, "Your account is not assigned to any vendor.")
                return render(request, "auth/login.html", {"form": form})
            return redirect(next_url or "vendorapp:dashboard")
    else:
        form = AuthenticationForm(request)

    return render(request, "auth/login.html", {"form": form})


def logout_page(request):
    logout(request)
    return redirect("login")


@vendor_staff_required
def dashboard(request):
    selected_vendor = get_vendor_for_request(request)
    if request.user.is_superuser and not selected_vendor:
        selected_vendor = Vendor.objects.filter(is_active=True).first()

    vendor_ids = user_vendor_queryset(request.user).values_list("id", flat=True)

    product_qs = Product.objects.filter(vendor_id__in=vendor_ids)
    order_qs = Order.objects.filter(vendor_id__in=vendor_ids)
    staff_qs = VendorStaff.objects.filter(vendor_id__in=vendor_ids, is_active=True)

    if selected_vendor:
        product_qs = product_qs.filter(vendor=selected_vendor)
        order_qs = order_qs.filter(vendor=selected_vendor)
        staff_qs = staff_qs.filter(vendor=selected_vendor)

    package_alarm = get_vendor_package_alarm(selected_vendor, threshold_days=30)
    total_revenue = order_qs.aggregate(total=Sum("total_amount"))["total"] or 0
    low_stock_count = product_qs.filter(stock_quantity__lte=5).count()

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "package_alarm": package_alarm,
        "product_count": product_qs.count(),
        "order_count": order_qs.count(),
        "staff_count": staff_qs.count(),
        "total_revenue": total_revenue,
        "low_stock_count": low_stock_count,
        "recent_orders": order_qs.select_related("vendor").order_by("-created_at")[:8],
    }
    return render(request, "vendorapp/dashboard.html", context)


@vendor_staff_required
def analytics_dashboard(request):
    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "analytics", "view")
    if blocked:
        return blocked

    vendors = user_vendor_queryset(request.user)
    product_qs = Product.objects.filter(vendor__in=vendors)
    order_qs = Order.objects.filter(vendor__in=vendors)
    stock_qs = StockMovement.objects.select_related("product", "product__vendor").filter(product__vendor__in=vendors)

    if selected_vendor:
        product_qs = product_qs.filter(vendor=selected_vendor)
        order_qs = order_qs.filter(vendor=selected_vendor)
        stock_qs = stock_qs.filter(product__vendor=selected_vendor)

    totals = order_qs.aggregate(total_revenue=Sum("total_amount"), total_orders=Count("id"))
    top_products = (
        product_qs.annotate(sold_qty=Sum("orderitem__quantity"), order_count=Count("orderitem__order", distinct=True))
        .order_by("-sold_qty", "name")[:8]
    )

    context = {
        "vendors": vendors,
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "total_products": product_qs.count(),
        "active_products": product_qs.filter(is_active=True).count(),
        "low_stock_products": product_qs.filter(stock_quantity__lte=5).order_by("stock_quantity", "name")[:10],
        "total_orders": totals["total_orders"] or 0,
        "total_revenue": totals["total_revenue"] or 0,
        "pending_orders": order_qs.filter(status=Order.STATUS_PENDING).count(),
        "processing_orders": order_qs.filter(status=Order.STATUS_PROCESSING).count(),
        "top_products": top_products,
        "recent_movements": stock_qs.order_by("-created_at")[:15],
    }
    return render(request, "vendorapp/analytics.html", context)


@vendor_staff_required
def staff_list(request):
    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "staff", "view")
    if blocked:
        return blocked

    vendors = user_vendor_queryset(request.user)

    staff_qs = VendorStaff.objects.select_related("vendor", "user")
    if request.user.is_superuser:
        if selected_vendor:
            staff_qs = staff_qs.filter(vendor=selected_vendor)
    else:
        staff_qs = staff_qs.filter(vendor__in=vendors)
        if selected_vendor:
            staff_qs = staff_qs.filter(vendor=selected_vendor)

    context = {
        "vendors": vendors,
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "staff_list": staff_qs.order_by("vendor__name", "user__username"),
    }
    return render(request, "vendorapp/staff_list.html", context)


@vendor_staff_required
def staff_create(request):
    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "staff", "create")
    if blocked:
        return blocked

    if request.method == "POST":
        form = VendorStaffCreateForm(request.POST, user=request.user)
        if form.is_valid():
            if not request.user.is_superuser and selected_vendor:
                form.cleaned_data["vendor"] = selected_vendor
            _, membership = form.save()
            audit_log(
                request,
                action="staff.create",
                vendor=membership.vendor,
                target_model="VendorStaff",
                target_id=membership.id,
                details=f"created user={membership.user.username} role={membership.role}",
            )
            messages.success(request, f"Staff user created for {membership.vendor.name}.")
            return _redirect_with_vendor(request, "vendorapp:staff-list")
    else:
        initial = {}
        if selected_vendor:
            initial["vendor"] = selected_vendor
        form = VendorStaffCreateForm(user=request.user, initial=initial)

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "form": form,
    }
    return render(request, "vendorapp/staff_form.html", context)


@vendor_staff_required
def superadmin_vendor_user_create(request):
    if not request.user.is_superuser:
        messages.error(request, "Only superadmin can create vendor + user.")
        return _redirect_with_vendor(request, "vendorapp:staff-list")

    selected_vendor = get_vendor_for_request(request)

    if request.method == "POST":
        form = SuperAdminVendorUserCreateForm(request.POST)
        if form.is_valid():
            vendor, user, _ = form.save()
            audit_log(
                request,
                action="vendor.create_with_user",
                vendor=vendor,
                target_model="Vendor",
                target_id=vendor.id,
                details=f"user={user.username}",
            )
            messages.success(
                request,
                f"Vendor '{vendor.name}' and user '{user.username}' created and linked successfully.",
            )
            return _redirect_with_vendor(request, "vendorapp:staff-list")
    else:
        form = SuperAdminVendorUserCreateForm()

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "form": form,
    }
    return render(request, "vendorapp/superadmin_vendor_user_form.html", context)


@vendor_staff_required
def staff_toggle_active(request, token):
    selected_vendor = get_vendor_for_request(request)
    membership = get_object_or_404(VendorStaff.objects.select_related("vendor"), pk=_decode_pk_token(token))
    allowed_vendors = user_vendor_queryset(request.user)

    if not request.user.is_superuser and not allowed_vendors.filter(id=membership.vendor_id).exists():
        return _redirect_with_vendor(request, "vendorapp:staff-list")
    if not request.user.is_superuser and selected_vendor and membership.vendor_id != selected_vendor.id:
        return _redirect_with_vendor(request, "vendorapp:staff-list")

    blocked = _require_module_perm(request, membership.vendor, "staff", "update")
    if blocked:
        return blocked

    membership.is_active = not membership.is_active
    membership.save(update_fields=["is_active"])
    audit_log(
        request,
        action="staff.toggle_active",
        vendor=membership.vendor,
        target_model="VendorStaff",
        target_id=membership.id,
        details=f"is_active={membership.is_active}",
    )
    messages.success(request, "Staff status updated.")
    return _redirect_with_vendor(request, "vendorapp:staff-list")


@vendor_staff_required
def vendor_theme_settings(request):
    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "settings", "update")
    if blocked:
        return blocked

    settings_obj, _ = VendorSettings.objects.get_or_create(vendor=selected_vendor)

    if request.method == "POST":
        form = VendorSettingsForm(request.POST, request.FILES, instance=settings_obj, vendor=selected_vendor)
        if form.is_valid():
            form.save()
            audit_log(
                request,
                action="settings.update",
                vendor=selected_vendor,
                target_model="VendorSettings",
                target_id=settings_obj.id,
            )
            messages.success(request, "Vendor theme settings updated.")
            return _redirect_with_vendor(request, "vendorapp:settings")
    else:
        form = VendorSettingsForm(instance=settings_obj, vendor=selected_vendor)

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "form": form,
    }
    return render(request, "vendorapp/settings_form.html", context)


@vendor_staff_required
def category_list(request):
    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "products", "view")
    if blocked:
        return blocked

    vendors = user_vendor_queryset(request.user)

    categories = VendorCategory.objects.select_related("vendor").filter(vendor__in=vendors)
    if selected_vendor:
        categories = categories.filter(vendor=selected_vendor)

    context = {
        "vendors": vendors,
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "categories": categories.order_by("name"),
    }
    return render(request, "marketapp/category_list.html", context)


@vendor_staff_required
def category_create(request):
    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "products", "create")
    if blocked:
        return blocked

    if request.method == "POST":
        form = VendorCategoryForm(request.POST, user=request.user, selected_vendor=selected_vendor)
        if form.is_valid():
            category = form.save(commit=False)
            if not request.user.is_superuser and selected_vendor:
                category.vendor = selected_vendor
            category.save()
            audit_log(
                request,
                action="category.create",
                vendor=category.vendor,
                target_model="VendorCategory",
                target_id=category.id,
                details=f"name={category.name}",
            )
            messages.success(request, "Category created.")
            return _redirect_with_vendor(request, "vendorapp:category-list")
    else:
        initial = {}
        if selected_vendor:
            initial["vendor"] = selected_vendor
        form = VendorCategoryForm(user=request.user, selected_vendor=selected_vendor, initial=initial)

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "form": form,
        "title": "Create Category",
    }
    return render(request, "marketapp/category_form.html", context)


@vendor_staff_required
def category_update(request, token):
    selected_vendor = get_vendor_for_request(request)
    vendors = user_vendor_queryset(request.user)
    category_qs = VendorCategory.objects.select_related("vendor").filter(vendor__in=vendors)
    if not request.user.is_superuser and selected_vendor:
        category_qs = category_qs.filter(vendor=selected_vendor)
    category = get_object_or_404(category_qs, pk=_decode_pk_token(token))

    blocked = _require_module_perm(request, category.vendor, "products", "update")
    if blocked:
        return blocked

    if request.method == "POST":
        form = VendorCategoryForm(request.POST, instance=category, user=request.user, selected_vendor=selected_vendor)
        if form.is_valid():
            obj = form.save(commit=False)
            if not request.user.is_superuser and selected_vendor:
                obj.vendor = selected_vendor
            obj.save()
            audit_log(
                request,
                action="category.update",
                vendor=obj.vendor,
                target_model="VendorCategory",
                target_id=obj.id,
                details=f"name={obj.name}",
            )
            messages.success(request, "Category updated.")
            return _redirect_with_vendor(request, "vendorapp:category-list")
    else:
        form = VendorCategoryForm(instance=category, user=request.user, selected_vendor=selected_vendor)

    context = {
        "vendors": vendors,
        "selected_vendor": selected_vendor or category.vendor,
        "theme": get_theme_values(selected_vendor or category.vendor),
        "form": form,
        "title": "Update Category",
    }
    return render(request, "marketapp/category_form.html", context)


@vendor_staff_required
def category_delete(request, token):
    selected_vendor = get_vendor_for_request(request)
    vendors = user_vendor_queryset(request.user)
    category_qs = VendorCategory.objects.filter(vendor__in=vendors)
    if not request.user.is_superuser and selected_vendor:
        category_qs = category_qs.filter(vendor=selected_vendor)
    category = get_object_or_404(category_qs, pk=_decode_pk_token(token))

    blocked = _require_module_perm(request, category.vendor, "products", "delete")
    if blocked:
        return blocked

    vendor = category.vendor
    category_id = category.id
    category.delete()
    audit_log(
        request,
        action="category.delete",
        vendor=vendor,
        target_model="VendorCategory",
        target_id=category_id,
    )
    messages.success(request, "Category deleted.")
    return _redirect_with_vendor(request, "vendorapp:category-list")


@vendor_staff_required
def product_list(request):
    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "products", "view")
    if blocked:
        return blocked

    vendors = user_vendor_queryset(request.user)
    package_status = get_vendor_package_status(selected_vendor)

    products = Product.objects.select_related("vendor", "category_ref").prefetch_related("gallery").filter(vendor__in=vendors)
    if selected_vendor:
        products = products.filter(vendor=selected_vendor)

    products = list(products.order_by("-created_at"))
    for product in products:
        detail_path = reverse("marketapp:product-detail", kwargs={"token": encrypt_int(product.pk)})
        product.share_url = request.build_absolute_uri(detail_path)
        product.share_qr_src = qr_image_src(product.share_url)

    saved_filters = SavedFilter.objects.filter(user=request.user, page="products", vendor__in=vendors).order_by("name")
    if selected_vendor:
        saved_filters = saved_filters.filter(vendor=selected_vendor)

    context = {
        "vendors": vendors,
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "products": products,
        "package_status": package_status,
        "saved_filters": saved_filters,
    }
    return render(request, "marketapp/product_list.html", context)


@vendor_staff_required
def product_bulk_action(request):
    if request.method != "POST":
        return _redirect_with_vendor(request, "vendorapp:product-list")

    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "products", "bulk")
    if blocked:
        return blocked

    action_name = request.POST.get("bulk_action")
    tokens = request.POST.getlist("product_tokens")
    if not tokens:
        messages.warning(request, "Select at least one product.")
        return _redirect_with_vendor(request, "vendorapp:product-list")

    product_ids = []
    for token in tokens:
        try:
            product_ids.append(_decode_pk_token(token))
        except Http404:
            continue

    products_qs = Product.objects.filter(id__in=product_ids, vendor__in=user_vendor_queryset(request.user))
    if selected_vendor:
        products_qs = products_qs.filter(vendor=selected_vendor)

    count = 0
    if action_name == "activate":
        count = products_qs.update(is_active=True)
    elif action_name == "deactivate":
        count = products_qs.update(is_active=False)
    elif action_name == "delete":
        count = products_qs.count()
        products_qs.delete()
    else:
        messages.error(request, "Invalid bulk action.")
        return _redirect_with_vendor(request, "vendorapp:product-list")

    audit_log(
        request,
        action="product.bulk",
        vendor=selected_vendor,
        target_model="Product",
        details=f"action={action_name} count={count}",
    )
    messages.success(request, f"Bulk action applied to {count} product(s).")
    return _redirect_with_vendor(request, "vendorapp:product-list")


@vendor_staff_required
def product_create(request):
    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "products", "create")
    if blocked:
        return blocked

    package_status = get_vendor_package_status(selected_vendor)
    if not package_status["can_add_products"]:
        if package_status["message"]:
            messages.error(request, package_status["message"])
        return _redirect_with_vendor(request, "vendorapp:product-list")

    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES, user=request.user, selected_vendor=selected_vendor)
        if form.is_valid():
            product = form.save(commit=False)
            if not request.user.is_superuser and selected_vendor:
                product.vendor = selected_vendor
            if product.category_ref:
                product.category = product.category_ref.name
            product.save()
            for index, image in enumerate(form.cleaned_data.get("additional_images", [])):
                ProductImage.objects.create(product=product, image=image, sort_order=index)
            audit_log(
                request,
                action="product.create",
                vendor=product.vendor,
                target_model="Product",
                target_id=product.id,
                details=f"sku={product.sku}",
            )
            messages.success(request, "Product created.")
            return _redirect_with_vendor(request, "vendorapp:product-list")
    else:
        initial = {}
        if selected_vendor:
            initial["vendor"] = selected_vendor
        form = ProductForm(user=request.user, selected_vendor=selected_vendor, initial=initial)

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "form": form,
        "title": "Create Product",
        "package_status": package_status,
    }
    return render(request, "marketapp/product_form.html", context)


@vendor_staff_required
def product_update(request, token):
    selected_vendor = get_vendor_for_request(request)
    vendors = user_vendor_queryset(request.user)
    product_qs = Product.objects.select_related("vendor", "category_ref").filter(vendor__in=vendors)
    if not request.user.is_superuser and selected_vendor:
        product_qs = product_qs.filter(vendor=selected_vendor)
    product = get_object_or_404(product_qs, pk=_decode_pk_token(token))

    blocked = _require_module_perm(request, product.vendor, "products", "update")
    if blocked:
        return blocked

    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES, instance=product, user=request.user, selected_vendor=selected_vendor)
        if form.is_valid():
            product = form.save()
            start_order = product.gallery.count()
            for index, image in enumerate(form.cleaned_data.get("additional_images", [])):
                ProductImage.objects.create(product=product, image=image, sort_order=start_order + index)
            audit_log(
                request,
                action="product.update",
                vendor=product.vendor,
                target_model="Product",
                target_id=product.id,
                details=f"sku={product.sku}",
            )
            messages.success(request, "Product updated.")
            return _redirect_with_vendor(request, "vendorapp:product-list")
    else:
        form = ProductForm(instance=product, user=request.user, selected_vendor=selected_vendor)

    context = {
        "vendors": vendors,
        "selected_vendor": selected_vendor or product.vendor,
        "theme": get_theme_values(selected_vendor or product.vendor),
        "form": form,
        "title": "Update Product",
    }
    return render(request, "marketapp/product_form.html", context)


@vendor_staff_required
def product_delete(request, token):
    selected_vendor = get_vendor_for_request(request)
    vendors = user_vendor_queryset(request.user)
    product_qs = Product.objects.filter(vendor__in=vendors)
    if not request.user.is_superuser and selected_vendor:
        product_qs = product_qs.filter(vendor=selected_vendor)
    product = get_object_or_404(product_qs, pk=_decode_pk_token(token))

    blocked = _require_module_perm(request, product.vendor, "products", "delete")
    if blocked:
        return blocked

    vendor = product.vendor
    product_id = product.id
    product.delete()
    audit_log(
        request,
        action="product.delete",
        vendor=vendor,
        target_model="Product",
        target_id=product_id,
    )
    messages.success(request, "Product deleted.")
    return _redirect_with_vendor(request, "vendorapp:product-list")


@vendor_staff_required
def product_stock_adjust(request, token):
    selected_vendor = get_vendor_for_request(request)
    product_qs = Product.objects.select_related("vendor").filter(vendor__in=user_vendor_queryset(request.user))
    if not request.user.is_superuser and selected_vendor:
        product_qs = product_qs.filter(vendor=selected_vendor)
    product = get_object_or_404(product_qs, pk=_decode_pk_token(token))

    blocked = _require_module_perm(request, product.vendor, "products", "update")
    if blocked:
        return blocked

    if request.method == "POST":
        form = ProductStockAdjustForm(request.POST)
        if form.is_valid():
            delta = form.cleaned_data["quantity_delta"]
            note = form.cleaned_data.get("note", "")
            new_qty = product.stock_quantity + delta
            if new_qty < 0:
                form.add_error("quantity_delta", "Stock cannot go below zero.")
            else:
                old_qty = product.stock_quantity
                product.stock_quantity = new_qty
                product.save(update_fields=["stock_quantity", "updated_at"])
                StockMovement.objects.create(
                    product=product,
                    movement_type=StockMovement.TYPE_ADJUSTMENT,
                    quantity_delta=delta,
                    note=note,
                    created_by=request.user,
                )
                audit_log(
                    request,
                    action="product.stock_adjust",
                    vendor=product.vendor,
                    target_model="Product",
                    target_id=product.id,
                    details=f"old={old_qty} delta={delta} new={new_qty}",
                )
                messages.success(request, "Stock updated successfully.")
                return _redirect_with_vendor(request, "vendorapp:product-list")
    else:
        form = ProductStockAdjustForm()

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor or product.vendor,
        "theme": get_theme_values(selected_vendor or product.vendor),
        "product": product,
        "form": form,
    }
    return render(request, "vendorapp/product_stock_adjust.html", context)


@vendor_staff_required
def product_export_csv(request):
    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "products", "view")
    if blocked:
        return blocked

    products = Product.objects.select_related("vendor", "category_ref").filter(vendor__in=user_vendor_queryset(request.user))
    if selected_vendor:
        products = products.filter(vendor=selected_vendor)

    rows = [
        [
            p.id,
            p.vendor.name,
            p.name,
            p.sku,
            p.category_ref.name if p.category_ref else p.category,
            p.price,
            p.stock_quantity,
            "Yes" if p.is_active else "No",
        ]
        for p in products.order_by("vendor__name", "name")
    ]
    audit_log(request, action="product.export_csv", vendor=selected_vendor, target_model="Product", details=f"rows={len(rows)}")
    return _export_csv(
        filename="products.csv",
        headers=["ID", "Vendor", "Name", "SKU", "Category", "Price", "Stock", "Active"],
        rows=rows,
    )


@vendor_staff_required
def offer_list(request):
    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "offers", "view")
    if blocked:
        return blocked

    offers = Offer.objects.select_related("vendor", "created_by").prefetch_related("products")
    offers = offers.filter(vendor__in=user_vendor_queryset(request.user))
    if selected_vendor:
        offers = offers.filter(vendor=selected_vendor)

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "offers": offers,
    }
    return render(request, "vendorapp/offer_list.html", context)


@vendor_staff_required
def offer_create(request):
    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "offers", "create")
    if blocked:
        return blocked

    if request.method == "POST":
        form = OfferForm(request.POST, user=request.user, selected_vendor=selected_vendor)
        if form.is_valid():
            offer = form.save(commit=False)
            offer.vendor = selected_vendor
            offer.created_by = request.user
            offer.save()
            form.save_m2m()
            audit_log(
                request,
                action="offer.create",
                vendor=offer.vendor,
                target_model="Offer",
                target_id=offer.id,
                details=f"name={offer.name}",
            )
            messages.success(request, "Offer created.")
            return _redirect_with_vendor(request, "vendorapp:offer-list")
    else:
        form = OfferForm(user=request.user, selected_vendor=selected_vendor)

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "form": form,
        "title": "Create Offer",
    }
    return render(request, "vendorapp/offer_form.html", context)


@vendor_staff_required
def offer_update(request, token):
    selected_vendor = get_vendor_for_request(request)
    offer_qs = Offer.objects.select_related("vendor").prefetch_related("products")
    offer_qs = offer_qs.filter(vendor__in=user_vendor_queryset(request.user))
    if selected_vendor:
        offer_qs = offer_qs.filter(vendor=selected_vendor)
    offer = get_object_or_404(offer_qs, pk=_decode_pk_token(token))

    blocked = _require_module_perm(request, offer.vendor, "offers", "update")
    if blocked:
        return blocked

    if request.method == "POST":
        form = OfferForm(request.POST, instance=offer, user=request.user, selected_vendor=offer.vendor)
        if form.is_valid():
            offer = form.save()
            audit_log(
                request,
                action="offer.update",
                vendor=offer.vendor,
                target_model="Offer",
                target_id=offer.id,
                details=f"name={offer.name}",
            )
            messages.success(request, "Offer updated.")
            return _redirect_with_vendor(request, "vendorapp:offer-list")
    else:
        form = OfferForm(instance=offer, user=request.user, selected_vendor=offer.vendor)

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor or offer.vendor,
        "theme": get_theme_values(selected_vendor or offer.vendor),
        "form": form,
        "title": "Update Offer",
    }
    return render(request, "vendorapp/offer_form.html", context)


@vendor_staff_required
def offer_toggle_active(request, token):
    selected_vendor = get_vendor_for_request(request)
    offer_qs = Offer.objects.select_related("vendor").filter(vendor__in=user_vendor_queryset(request.user))
    if selected_vendor:
        offer_qs = offer_qs.filter(vendor=selected_vendor)
    offer = get_object_or_404(offer_qs, pk=_decode_pk_token(token))

    blocked = _require_module_perm(request, offer.vendor, "offers", "update")
    if blocked:
        return blocked

    offer.is_active = not offer.is_active
    offer.save(update_fields=["is_active", "updated_at"])
    audit_log(
        request,
        action="offer.toggle_active",
        vendor=offer.vendor,
        target_model="Offer",
        target_id=offer.id,
        details=f"is_active={offer.is_active}",
    )
    messages.success(request, "Offer status updated.")
    return _redirect_with_vendor(request, "vendorapp:offer-list")


@vendor_staff_required
def hero_slide_list(request):
    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "settings", "view")
    if blocked:
        return blocked

    slides = HeroSlide.objects.filter(vendor__in=user_vendor_queryset(request.user)).select_related("vendor")
    if selected_vendor:
        slides = slides.filter(vendor=selected_vendor)

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "slides": slides.order_by("sort_order", "id"),
    }
    return render(request, "vendorapp/hero_slide_list.html", context)


@vendor_staff_required
def hero_slide_create(request):
    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "settings", "update")
    if blocked:
        return blocked
    vendor_block = _require_vendor(request, selected_vendor)
    if vendor_block:
        return vendor_block

    if request.method == "POST":
        form = HeroSlideForm(request.POST)
        if form.is_valid():
            slide = form.save(commit=False)
            slide.vendor = selected_vendor
            slide.save()
            audit_log(
                request,
                action="hero_slide.create",
                vendor=selected_vendor,
                target_model="HeroSlide",
                target_id=slide.id,
                details=f"title={slide.title}",
            )
            messages.success(request, "Hero slide created.")
            return _redirect_with_vendor(request, "vendorapp:hero-slide-list")
    else:
        form = HeroSlideForm()

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "form": form,
        "title": "Create Hero Slide",
    }
    return render(request, "vendorapp/hero_slide_form.html", context)


@vendor_staff_required
def hero_slide_update(request, token):
    selected_vendor = get_vendor_for_request(request)
    slide_qs = HeroSlide.objects.select_related("vendor").filter(vendor__in=user_vendor_queryset(request.user))
    if selected_vendor:
        slide_qs = slide_qs.filter(vendor=selected_vendor)
    slide = get_object_or_404(slide_qs, pk=_decode_pk_token(token))

    blocked = _require_module_perm(request, slide.vendor, "settings", "update")
    if blocked:
        return blocked

    if request.method == "POST":
        form = HeroSlideForm(request.POST, instance=slide)
        if form.is_valid():
            slide = form.save()
            audit_log(
                request,
                action="hero_slide.update",
                vendor=slide.vendor,
                target_model="HeroSlide",
                target_id=slide.id,
                details=f"title={slide.title}",
            )
            messages.success(request, "Hero slide updated.")
            return _redirect_with_vendor(request, "vendorapp:hero-slide-list")
    else:
        form = HeroSlideForm(instance=slide)

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor or slide.vendor,
        "theme": get_theme_values(selected_vendor or slide.vendor),
        "form": form,
        "title": "Update Hero Slide",
    }
    return render(request, "vendorapp/hero_slide_form.html", context)


@vendor_staff_required
def hero_slide_toggle_active(request, token):
    selected_vendor = get_vendor_for_request(request)
    slide_qs = HeroSlide.objects.select_related("vendor").filter(vendor__in=user_vendor_queryset(request.user))
    if selected_vendor:
        slide_qs = slide_qs.filter(vendor=selected_vendor)
    slide = get_object_or_404(slide_qs, pk=_decode_pk_token(token))

    blocked = _require_module_perm(request, slide.vendor, "settings", "update")
    if blocked:
        return blocked

    slide.is_active = not slide.is_active
    slide.save(update_fields=["is_active", "updated_at"])
    audit_log(
        request,
        action="hero_slide.toggle_active",
        vendor=slide.vendor,
        target_model="HeroSlide",
        target_id=slide.id,
        details=f"is_active={slide.is_active}",
    )
    messages.success(request, "Hero slide status updated.")
    return _redirect_with_vendor(request, "vendorapp:hero-slide-list")


@vendor_staff_required
def hero_slide_delete(request, token):
    selected_vendor = get_vendor_for_request(request)
    slide_qs = HeroSlide.objects.select_related("vendor").filter(vendor__in=user_vendor_queryset(request.user))
    if selected_vendor:
        slide_qs = slide_qs.filter(vendor=selected_vendor)
    slide = get_object_or_404(slide_qs, pk=_decode_pk_token(token))

    blocked = _require_module_perm(request, slide.vendor, "settings", "delete")
    if blocked:
        return blocked

    vendor = slide.vendor
    slide_id = slide.id
    slide.delete()
    audit_log(
        request,
        action="hero_slide.delete",
        vendor=vendor,
        target_model="HeroSlide",
        target_id=slide_id,
    )
    messages.success(request, "Hero slide deleted.")
    return _redirect_with_vendor(request, "vendorapp:hero-slide-list")


@vendor_staff_required
def order_export_csv(request):
    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "orders", "view")
    if blocked:
        return blocked

    orders = Order.objects.select_related("vendor").filter(vendor__in=user_vendor_queryset(request.user))
    if selected_vendor:
        orders = orders.filter(vendor=selected_vendor)

    rows = [[o.id, o.order_number, o.vendor.name, o.customer_name, o.status, o.total_amount, o.created_at] for o in orders]
    audit_log(request, action="order.export_csv", vendor=selected_vendor, target_model="Order", details=f"rows={len(rows)}")
    return _export_csv(
        filename="orders.csv",
        headers=["ID", "Order Number", "Vendor", "Customer", "Status", "Total", "Created At"],
        rows=rows,
    )


@vendor_staff_required
def package_list(request):
    blocked = _superadmin_only(request)
    if blocked:
        return blocked

    selected_vendor = get_vendor_for_request(request)
    packages = Package.objects.select_related("vendor").order_by("vendor__name")
    if selected_vendor:
        packages = packages.filter(vendor=selected_vendor)
    packages = list(packages)
    package_alarms = []
    today = timezone.localdate()
    for package in packages:
        if not package.is_active or package.ends_on < today:
            continue
        days_left = (package.ends_on - today).days
        if days_left <= 30:
            package_alarms.append(
                {
                    "vendor_name": package.vendor.name,
                    "days_left": days_left,
                    "ends_on": package.ends_on,
                    "is_today": days_left == 0,
                }
            )

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "packages": packages,
        "package_alarms": package_alarms,
    }
    return render(request, "vendorapp/package_list.html", context)


@vendor_staff_required
def package_create(request):
    blocked = _superadmin_only(request)
    if blocked:
        return blocked

    selected_vendor = get_vendor_for_request(request)
    if request.method == "POST":
        form = PackageForm(request.POST)
        if form.is_valid():
            package = form.save()
            audit_log(
                request,
                action="package.create",
                vendor=package.vendor,
                target_model="Package",
                target_id=package.id,
            )
            messages.success(request, "Package created.")
            return _redirect_with_vendor(request, "vendorapp:package-list")
    else:
        initial = {}
        if selected_vendor:
            initial["vendor"] = selected_vendor
        form = PackageForm(initial=initial)

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "form": form,
        "title": "Create Package",
    }
    return render(request, "vendorapp/package_form.html", context)


@vendor_staff_required
def package_update(request, token):
    blocked = _superadmin_only(request)
    if blocked:
        return blocked

    selected_vendor = get_vendor_for_request(request)
    package = get_object_or_404(Package.objects.select_related("vendor"), pk=_decode_pk_token(token))

    if request.method == "POST":
        form = PackageForm(request.POST, instance=package)
        if form.is_valid():
            package = form.save()
            audit_log(
                request,
                action="package.update",
                vendor=package.vendor,
                target_model="Package",
                target_id=package.id,
            )
            messages.success(request, "Package updated.")
            return _redirect_with_vendor(request, "vendorapp:package-list")
    else:
        form = PackageForm(instance=package)

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor or package.vendor,
        "theme": get_theme_values(selected_vendor or package.vendor),
        "form": form,
        "title": "Update Package",
    }
    return render(request, "vendorapp/package_form.html", context)


@vendor_staff_required
def invoice_list(request):
    blocked = _superadmin_only(request)
    if blocked:
        return blocked

    selected_vendor = get_vendor_for_request(request)
    invoices = PackageInvoice.objects.select_related("vendor", "package")
    if selected_vendor:
        invoices = invoices.filter(vendor=selected_vendor)

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "invoices": invoices.order_by("-created_at"),
    }
    return render(request, "vendorapp/invoice_list.html", context)


@vendor_staff_required
def invoice_create(request):
    blocked = _superadmin_only(request)
    if blocked:
        return blocked

    selected_vendor = get_vendor_for_request(request)
    if request.method == "POST":
        form = PackageInvoiceForm(request.POST)
        if form.is_valid():
            invoice = form.save()
            audit_log(
                request,
                action="invoice.create",
                vendor=invoice.vendor,
                target_model="PackageInvoice",
                target_id=invoice.id,
            )
            messages.success(request, "Invoice created.")
            return _redirect_with_vendor(request, "vendorapp:invoice-list")
    else:
        initial = {}
        if selected_vendor:
            initial["vendor"] = selected_vendor
            initial["currency_code"] = selected_vendor.currency_code
        form = PackageInvoiceForm(initial=initial)

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "form": form,
        "title": "Create Invoice",
    }
    return render(request, "vendorapp/invoice_form.html", context)


@vendor_staff_required
def invoice_toggle_paid(request, token):
    blocked = _superadmin_only(request)
    if blocked:
        return blocked

    invoice = get_object_or_404(PackageInvoice.objects.select_related("vendor"), pk=_decode_pk_token(token))
    invoice.is_paid = not invoice.is_paid
    invoice.save(update_fields=["is_paid"])
    audit_log(
        request,
        action="invoice.toggle_paid",
        vendor=invoice.vendor,
        target_model="PackageInvoice",
        target_id=invoice.id,
        details=f"is_paid={invoice.is_paid}",
    )
    messages.success(request, "Invoice payment status updated.")
    return _redirect_with_vendor(request, "vendorapp:invoice-list")


@vendor_staff_required
def ticket_list(request):
    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "settings", "view")
    if blocked:
        return blocked

    tickets = SupportTicket.objects.select_related("vendor", "created_by", "assigned_to").filter(
        vendor__in=user_vendor_queryset(request.user)
    )
    if selected_vendor:
        tickets = tickets.filter(vendor=selected_vendor)

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "tickets": tickets,
    }
    return render(request, "vendorapp/ticket_list.html", context)


@vendor_staff_required
def ticket_create(request):
    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "settings", "view")
    if blocked:
        return blocked

    if request.method == "POST":
        form = SupportTicketForm(request.POST)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.vendor = selected_vendor
            ticket.created_by = request.user
            ticket.save()
            audit_log(
                request,
                action="ticket.create",
                vendor=ticket.vendor,
                target_model="SupportTicket",
                target_id=ticket.id,
                details=f"priority={ticket.priority}",
            )
            messages.success(request, "Ticket created.")
            return _redirect_with_vendor(request, "vendorapp:ticket-list")
    else:
        form = SupportTicketForm()

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "form": form,
        "title": "Create Ticket",
    }
    return render(request, "vendorapp/ticket_form.html", context)


@vendor_staff_required
def ticket_update(request, token):
    selected_vendor = get_vendor_for_request(request)
    ticket_qs = SupportTicket.objects.select_related("vendor").filter(vendor__in=user_vendor_queryset(request.user))
    if not request.user.is_superuser and selected_vendor:
        ticket_qs = ticket_qs.filter(vendor=selected_vendor)
    ticket = get_object_or_404(ticket_qs, pk=_decode_pk_token(token))

    blocked = _require_module_perm(request, ticket.vendor, "settings", "update")
    if blocked:
        return blocked

    if request.method == "POST":
        form = SupportTicketForm(request.POST, instance=ticket)
        if form.is_valid():
            ticket = form.save()
            audit_log(
                request,
                action="ticket.update",
                vendor=ticket.vendor,
                target_model="SupportTicket",
                target_id=ticket.id,
                details=f"status={ticket.status}",
            )
            messages.success(request, "Ticket updated.")
            return _redirect_with_vendor(request, "vendorapp:ticket-list")
    else:
        form = SupportTicketForm(instance=ticket)

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor or ticket.vendor,
        "theme": get_theme_values(selected_vendor or ticket.vendor),
        "form": form,
        "title": "Update Ticket",
    }
    return render(request, "vendorapp/ticket_form.html", context)


@vendor_staff_required
def invite_list(request):
    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "staff", "view")
    if blocked:
        return blocked

    now = timezone.now()
    invites_qs = StaffInvite.objects.select_related("vendor", "created_by").filter(vendor__in=user_vendor_queryset(request.user))
    expired_qs = invites_qs.filter(status=StaffInvite.STATUS_PENDING, expires_at__lt=now)
    expired_qs.update(status=StaffInvite.STATUS_EXPIRED)

    if selected_vendor:
        invites_qs = invites_qs.filter(vendor=selected_vendor)

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "invites": invites_qs,
    }
    return render(request, "vendorapp/invite_list.html", context)


@vendor_staff_required
def invite_create(request):
    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "staff", "create")
    if blocked:
        return blocked

    if request.method == "POST":
        form = StaffInviteForm(request.POST, user=request.user, selected_vendor=selected_vendor)
        if form.is_valid():
            invite = form.save(commit=False)
            if selected_vendor and not request.user.is_superuser:
                invite.vendor = selected_vendor
            invite.created_by = request.user
            invite.token = secrets.token_urlsafe(24)
            invite.status = StaffInvite.STATUS_PENDING
            invite.save()
            audit_log(
                request,
                action="invite.create",
                vendor=invite.vendor,
                target_model="StaffInvite",
                target_id=invite.id,
                details=f"email={invite.email}",
            )
            messages.success(request, "Invite created.")
            return _redirect_with_vendor(request, "vendorapp:invite-list")
    else:
        initial = {}
        if selected_vendor:
            initial["vendor"] = selected_vendor
        form = StaffInviteForm(user=request.user, selected_vendor=selected_vendor, initial=initial)

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "form": form,
        "title": "Create Staff Invite",
    }
    return render(request, "vendorapp/invite_form.html", context)


@vendor_staff_required
def permissions_matrix(request):
    blocked = _superadmin_only(request)
    if blocked:
        return blocked

    selected_vendor = get_vendor_for_request(request)
    vendor_block = _require_vendor(request, selected_vendor)
    if vendor_block:
        return vendor_block

    if request.method == "POST":
        form = StaffPermissionForm(request.POST, user=request.user, selected_vendor=selected_vendor)
        if form.is_valid():
            perm = form.save()
            audit_log(
                request,
                action="permission.override",
                vendor=perm.membership.vendor,
                target_model="StaffPermission",
                target_id=perm.id,
                details=f"{perm.module}.{perm.action}={perm.is_allowed}",
            )
            messages.success(request, "Permission override saved.")
            return _redirect_with_vendor(request, "vendorapp:permissions")
    else:
        form = StaffPermissionForm(user=request.user, selected_vendor=selected_vendor)

    memberships = VendorStaff.objects.select_related("user", "vendor").filter(vendor=selected_vendor, is_active=True)
    permissions = (
        StaffPermission.objects.select_related("membership", "membership__user", "membership__vendor")
        .filter(membership__vendor=selected_vendor)
        .order_by("membership__user__username", "module", "action")
    )

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "memberships": memberships,
        "permissions": permissions,
        "form": form,
    }
    return render(request, "vendorapp/permissions.html", context)


@vendor_staff_required
def audit_log_list(request):
    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "analytics", "view")
    if blocked:
        return blocked

    logs = AuditLog.objects.select_related("vendor", "user")
    if request.user.is_superuser:
        if selected_vendor:
            logs = logs.filter(vendor=selected_vendor)
    else:
        logs = logs.filter(vendor__in=user_vendor_queryset(request.user))
        if selected_vendor:
            logs = logs.filter(vendor=selected_vendor)

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "logs": logs[:200],
    }
    return render(request, "vendorapp/audit_logs.html", context)


@vendor_staff_required
def saved_filter_list(request):
    selected_vendor = get_vendor_for_request(request)
    filters_qs = SavedFilter.objects.filter(user=request.user, vendor__in=user_vendor_queryset(request.user)).order_by(
        "page", "name"
    )
    if selected_vendor:
        filters_qs = filters_qs.filter(vendor=selected_vendor)

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "filters": filters_qs,
    }
    return render(request, "vendorapp/saved_filter_list.html", context)


@vendor_staff_required
def saved_filter_create(request):
    selected_vendor = get_vendor_for_request(request)
    vendor_block = _require_vendor(request, selected_vendor)
    if vendor_block:
        return vendor_block

    if request.method == "POST":
        form = SavedFilterForm(request.POST)
        if form.is_valid():
            saved_filter = form.save(commit=False)
            saved_filter.vendor = selected_vendor
            saved_filter.user = request.user
            saved_filter.save()
            audit_log(
                request,
                action="saved_filter.create",
                vendor=selected_vendor,
                target_model="SavedFilter",
                target_id=saved_filter.id,
                details=f"page={saved_filter.page} name={saved_filter.name}",
            )
            messages.success(request, "Saved filter created.")
            return _redirect_with_vendor(request, "vendorapp:saved-filter-list")
    else:
        form = SavedFilterForm(initial={"query_string": request.GET.urlencode()})

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "form": form,
        "title": "Create Saved Filter",
    }
    return render(request, "vendorapp/saved_filter_form.html", context)


@vendor_staff_required
def saved_filter_apply(request, token):
    selected_vendor = get_vendor_for_request(request)
    saved_filter = get_object_or_404(
        SavedFilter.objects.select_related("vendor"),
        pk=_decode_pk_token(token),
        user=request.user,
        vendor__in=user_vendor_queryset(request.user),
    )

    route_map = {
        "products": "vendorapp:product-list",
        "orders": "marketapp:order-list",
        "categories": "vendorapp:category-list",
        "staff": "vendorapp:staff-list",
        "tickets": "vendorapp:ticket-list",
    }
    route_name = route_map.get(saved_filter.page, "vendorapp:dashboard")

    url = reverse(route_name)
    query_parts = []
    if saved_filter.query_string:
        query_parts.append(saved_filter.query_string.lstrip("?"))
    if selected_vendor:
        query_parts.append(f"vendor={encrypt_int(selected_vendor.id)}")

    audit_log(
        request,
        action="saved_filter.apply",
        vendor=saved_filter.vendor,
        target_model="SavedFilter",
        target_id=saved_filter.id,
    )

    if query_parts:
        return redirect(f"{url}?{'&'.join(query_parts)}")
    return redirect(url)


@vendor_staff_required
def saved_filter_delete(request, token):
    saved_filter = get_object_or_404(
        SavedFilter.objects.select_related("vendor"),
        pk=_decode_pk_token(token),
        user=request.user,
        vendor__in=user_vendor_queryset(request.user),
    )
    vendor = saved_filter.vendor
    filter_id = saved_filter.id
    saved_filter.delete()
    audit_log(
        request,
        action="saved_filter.delete",
        vendor=vendor,
        target_model="SavedFilter",
        target_id=filter_id,
    )
    messages.success(request, "Saved filter deleted.")
    return _redirect_with_vendor(request, "vendorapp:saved-filter-list")


@vendor_staff_required
def review_list(request):
    selected_vendor = get_vendor_for_request(request)
    blocked = _require_module_perm(request, selected_vendor, "products", "view")
    if blocked:
        return blocked

    reviews = (
        ProductReview.objects.select_related("product", "product__vendor", "user")
        .filter(product__vendor__in=user_vendor_queryset(request.user))
        .order_by("-created_at")
    )
    if selected_vendor:
        reviews = reviews.filter(product__vendor=selected_vendor)

    context = {
        "vendors": user_vendor_queryset(request.user),
        "selected_vendor": selected_vendor,
        "theme": get_theme_values(selected_vendor),
        "reviews": reviews[:300],
    }
    return render(request, "vendorapp/review_list.html", context)


@vendor_staff_required
def review_delete(request, token):
    selected_vendor = get_vendor_for_request(request)
    review_qs = ProductReview.objects.select_related("product", "product__vendor").filter(
        product__vendor__in=user_vendor_queryset(request.user)
    )
    if selected_vendor:
        review_qs = review_qs.filter(product__vendor=selected_vendor)
    review = get_object_or_404(review_qs, pk=_decode_pk_token(token))

    blocked = _require_module_perm(request, review.product.vendor, "products", "delete")
    if blocked:
        return blocked

    vendor = review.product.vendor
    review_id = review.id
    review.delete()
    audit_log(
        request,
        action="review.delete",
        vendor=vendor,
        target_model="ProductReview",
        target_id=review_id,
    )
    messages.success(request, "Review deleted.")
    return _redirect_with_vendor(request, "vendorapp:review-list")
