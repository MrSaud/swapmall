from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from SWAPMALL.secure_params import InvalidToken, decrypt_int

from .models import AuditLog, Package, StaffPermission, Vendor, VendorSettings, VendorStaff


def sync_vendor_license_status():
    today = timezone.localdate()
    vendors = Vendor.objects.all().prefetch_related("products")
    packages = {pkg.vendor_id: pkg for pkg in Package.objects.select_related("vendor")}

    for vendor in vendors:
        pkg = packages.get(vendor.id)
        if pkg is None:
            # Only enforce auto-activation rules when a package exists.
            continue
        should_be_active = bool(pkg and pkg.is_active and pkg.starts_on <= today <= pkg.ends_on)
        if vendor.is_active != should_be_active:
            vendor.is_active = should_be_active
            vendor.save(update_fields=["is_active"])


def user_vendor_queryset(user):
    sync_vendor_license_status()
    if user.is_superuser:
        return Vendor.objects.all()
    return Vendor.objects.filter(staff_members__user=user, staff_members__is_active=True, is_active=True).distinct()


def user_has_vendor_access(user):
    return user.is_superuser or VendorStaff.objects.filter(user=user, is_active=True, vendor__is_active=True).exists()


def get_vendor_for_request(request):
    vendors = user_vendor_queryset(request.user)
    vendor_id = request.GET.get("vendor")

    if vendor_id:
        decoded_vendor_id = None
        try:
            decoded_vendor_id = decrypt_int(vendor_id)
        except InvalidToken:
            if vendor_id.isdigit():
                decoded_vendor_id = int(vendor_id)
        selected = vendors.filter(id=decoded_vendor_id).first() if decoded_vendor_id else None
        if selected:
            return selected

    if request.user.is_superuser:
        return vendors.first()

    membership = (
        VendorStaff.objects.select_related("vendor")
        .filter(user=request.user, is_active=True, vendor__is_active=True)
        .order_by("id")
        .first()
    )
    if membership:
        return membership.vendor
    return None


def get_vendor_package_status(vendor):
    if not vendor:
        return {
            "can_add_products": False,
            "message": "No vendor selected.",
            "max_products": 0,
            "current_products": 0,
        }

    package = Package.objects.filter(vendor=vendor).first()
    current_products = vendor.products.count()
    today = timezone.localdate()

    if not package or not package.is_active:
        return {
            "can_add_products": False,
            "message": "Vendor package is missing or inactive. Contact superadmin.",
            "max_products": 0,
            "current_products": current_products,
        }
    if package.starts_on > today:
        return {
            "can_add_products": False,
            "message": f"Vendor license starts on {package.starts_on}.",
            "max_products": package.max_products,
            "current_products": current_products,
        }
    if package.is_expired():
        return {
            "can_add_products": False,
            "message": "Vendor license expired. Contact superadmin to renew package.",
            "max_products": package.max_products,
            "current_products": current_products,
        }

    can_add = current_products < package.max_products
    message = ""
    if not can_add:
        message = f"Product limit reached ({current_products}/{package.max_products})."
    return {
        "can_add_products": can_add,
        "message": message,
        "max_products": package.max_products,
        "current_products": current_products,
    }


def get_vendor_package_alarm(vendor, threshold_days=30):
    if not vendor:
        return None
    package = Package.objects.filter(vendor=vendor, is_active=True).first()
    if not package:
        return None
    today = timezone.localdate()
    if package.ends_on < today:
        return None
    days_left = (package.ends_on - today).days
    if days_left > threshold_days:
        return None
    return {
        "vendor_name": vendor.name,
        "days_left": days_left,
        "ends_on": package.ends_on,
        "is_today": days_left == 0,
    }


def get_theme_values(vendor):
    defaults = {
        "theme_name": "classic",
        "primary_color": "#1B4D3E",
        "secondary_color": "#F4A259",
        "background_color": "#F5F7F4",
        "text_color": "#1C1C1C",
    }
    if not vendor:
        return defaults

    settings_obj = VendorSettings.objects.filter(vendor=vendor).first()
    if settings_obj:
        defaults.update(
            {
                "theme_name": settings_obj.theme_name,
                "primary_color": settings_obj.primary_color,
                "secondary_color": settings_obj.secondary_color,
                "background_color": settings_obj.background_color,
                "text_color": settings_obj.text_color,
            }
        )
    return defaults


def vendor_staff_required(view_func):
    @login_required
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not user_has_vendor_access(request.user):
            raise PermissionDenied("You do not have vendor staff access.")
        return view_func(request, *args, **kwargs)

    return _wrapped


def has_module_permission(user, vendor, module, action):
    if user.is_superuser:
        return True
    membership = VendorStaff.objects.filter(user=user, vendor=vendor, is_active=True).first()
    if not membership:
        return False

    role_defaults = {
        VendorStaff.ROLE_MANAGER: {
            "products": {"view", "create", "update", "delete", "bulk"},
            "offers": {"view", "create", "update", "delete"},
            "orders": {"view", "update", "bulk"},
            "staff": {"view", "create", "update"},
            "settings": {"view", "update"},
            "analytics": {"view"},
        },
        VendorStaff.ROLE_STAFF: {
            "products": {"view", "create", "update"},
            "offers": {"view", "create", "update"},
            "orders": {"view", "update"},
            "staff": {"view"},
            "settings": {"view"},
            "analytics": {"view"},
        },
    }
    allowed = role_defaults.get(membership.role, {}).get(module, set())
    if action in allowed:
        return True

    override = StaffPermission.objects.filter(membership=membership, module=module, action=action).first()
    return bool(override and override.is_allowed)


def audit_log(request, action, vendor=None, target_model="", target_id="", details=""):
    ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR")
    AuditLog.objects.create(
        vendor=vendor,
        user=request.user if request.user.is_authenticated else None,
        action=action,
        target_model=target_model,
        target_id=str(target_id or ""),
        details=details or "",
        ip_address=ip or None,
    )
