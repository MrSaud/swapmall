from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone


class Vendor(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True)
    description = models.TextField(blank=True)
    currency_code = models.CharField(
        max_length=3,
        default="USD",
        validators=[RegexValidator(r"^[A-Z]{3}$", "Currency must be a 3-letter ISO code (e.g. USD).")],
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class VendorSettings(models.Model):
    THEME_CHOICES = [
        ("classic", "Classic"),
        ("sunrise", "Sunrise"),
        ("forest", "Forest"),
        ("ocean", "Ocean"),
    ]

    vendor = models.OneToOneField(Vendor, on_delete=models.CASCADE, related_name="settings")
    theme_name = models.CharField(max_length=40, choices=THEME_CHOICES, default="classic")
    primary_color = models.CharField(max_length=7, default="#1B4D3E")
    secondary_color = models.CharField(max_length=7, default="#F4A259")
    background_color = models.CharField(max_length=7, default="#F5F7F4")
    text_color = models.CharField(max_length=7, default="#1C1C1C")
    vendor_logo = models.ImageField(upload_to="vendors/logos/", blank=True, null=True)
    instagram_url = models.URLField(blank=True)
    facebook_url = models.URLField(blank=True)
    tiktok_url = models.URLField(blank=True)
    x_url = models.URLField(blank=True)
    youtube_url = models.URLField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.vendor.name} Theme"


class Package(models.Model):
    vendor = models.OneToOneField(Vendor, on_delete=models.CASCADE, related_name="package")
    name = models.CharField(max_length=80)
    max_products = models.PositiveIntegerField(default=50)
    starts_on = models.DateField(default=timezone.localdate)
    ends_on = models.DateField()
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["vendor__name"]

    def __str__(self) -> str:
        return f"{self.vendor.name} - {self.name}"

    def is_expired(self) -> bool:
        return self.ends_on < timezone.localdate()


class VendorStaff(models.Model):
    ROLE_STAFF = "staff"
    ROLE_MANAGER = "manager"
    ROLE_CHOICES = [
        (ROLE_STAFF, "Staff User"),
        (ROLE_MANAGER, "Manager"),
    ]

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="staff_members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vendor_memberships")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_STAFF)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("vendor", "user")
        ordering = ["vendor__name", "user__username"]

    def __str__(self) -> str:
        return f"{self.user.username} @ {self.vendor.name}"


class VendorCategory(models.Model):
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("vendor", "name")
        ordering = ["vendor__name", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.vendor.name})"


class Product(models.Model):
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="products")
    category_ref = models.ForeignKey(
        VendorCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="products"
    )
    name = models.CharField(max_length=140)
    sku = models.CharField(max_length=60)
    category = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    main_thumbnail = models.ImageField(upload_to="products/main/", blank=True, null=True)
    is_digital = models.BooleanField(default=False)
    digital_file = models.FileField(upload_to="products/digital/", blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock_quantity = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("vendor", "sku")
        ordering = ["vendor__name", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.vendor.name})"


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="gallery")
    image = models.ImageField(upload_to="products/gallery/")
    alt_text = models.CharField(max_length=160, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return f"Gallery image for {self.product.name}"


class Offer(models.Model):
    TYPE_PRODUCT = "product"
    TYPE_COLLECTION = "collection"
    TYPE_CHOICES = [
        (TYPE_PRODUCT, "Single Product"),
        (TYPE_COLLECTION, "Collection"),
    ]

    DISCOUNT_PERCENT = "percent"
    DISCOUNT_FIXED = "fixed"
    DISCOUNT_CHOICES = [
        (DISCOUNT_PERCENT, "Percent (%)"),
        (DISCOUNT_FIXED, "Fixed Amount"),
    ]

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="offers")
    name = models.CharField(max_length=120)
    offer_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_PRODUCT)
    products = models.ManyToManyField(Product, related_name="offers", blank=True)
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_CHOICES, default=DISCOUNT_PERCENT)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.vendor.name} - {self.name}"


class HeroSlide(models.Model):
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="hero_slides")
    title = models.CharField(max_length=120)
    subtitle = models.CharField(max_length=280, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return f"{self.vendor.name} - {self.title}"


class StaffPermission(models.Model):
    MODULE_CHOICES = [
        ("products", "Products"),
        ("offers", "Offers"),
        ("orders", "Orders"),
        ("staff", "Staff"),
        ("packages", "Packages"),
        ("settings", "Settings"),
        ("analytics", "Analytics"),
    ]
    ACTION_CHOICES = [
        ("view", "View"),
        ("create", "Create"),
        ("update", "Update"),
        ("delete", "Delete"),
        ("bulk", "Bulk"),
    ]

    membership = models.ForeignKey(VendorStaff, on_delete=models.CASCADE, related_name="permissions")
    module = models.CharField(max_length=30, choices=MODULE_CHOICES)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    is_allowed = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("membership", "module", "action")
        ordering = ["membership__vendor__name", "membership__user__username", "module", "action"]

    def __str__(self) -> str:
        return f"{self.membership} {self.module}.{self.action}={self.is_allowed}"


class AuditLog(models.Model):
    vendor = models.ForeignKey(Vendor, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=80)
    target_model = models.CharField(max_length=80, blank=True)
    target_id = models.CharField(max_length=80, blank=True)
    details = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["action", "created_at"])]

    def __str__(self) -> str:
        return f"{self.action} ({self.created_at})"


class StockMovement(models.Model):
    TYPE_ADJUSTMENT = "adjustment"
    TYPE_ORDER = "order"
    TYPE_RETURN = "return"
    TYPE_CHOICES = [
        (TYPE_ADJUSTMENT, "Adjustment"),
        (TYPE_ORDER, "Order"),
        (TYPE_RETURN, "Return"),
    ]

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="stock_movements")
    movement_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_ADJUSTMENT)
    quantity_delta = models.IntegerField()
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.product.name} {self.quantity_delta:+d}"


class SupportTicket(models.Model):
    STATUS_OPEN = "open"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_RESOLVED = "resolved"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_RESOLVED, "Resolved"),
        (STATUS_CLOSED, "Closed"),
    ]
    PRIORITY_LOW = "low"
    PRIORITY_MEDIUM = "medium"
    PRIORITY_HIGH = "high"
    PRIORITY_CHOICES = [
        (PRIORITY_LOW, "Low"),
        (PRIORITY_MEDIUM, "Medium"),
        (PRIORITY_HIGH, "High"),
    ]

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="tickets")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    title = models.CharField(max_length=160)
    description = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default=PRIORITY_MEDIUM)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_tickets"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"{self.vendor.name} - {self.title}"


class PackageInvoice(models.Model):
    package = models.ForeignKey(Package, on_delete=models.CASCADE, related_name="invoices")
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="package_invoices")
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency_code = models.CharField(max_length=3, default="USD")
    due_date = models.DateField()
    is_paid = models.BooleanField(default=False)
    notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Invoice {self.vendor.name} {self.amount} {self.currency_code}"


class SavedFilter(models.Model):
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="saved_filters")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_filters")
    name = models.CharField(max_length=80)
    page = models.CharField(max_length=40, default="products")
    query_string = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("vendor", "user", "name", "page")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user} {self.page} {self.name}"


class StaffInvite(models.Model):
    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_EXPIRED, "Expired"),
    ]

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="staff_invites")
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=VendorStaff.ROLE_CHOICES, default=VendorStaff.ROLE_STAFF)
    token = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    expires_at = models.DateTimeField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["email", "status"])]

    def __str__(self) -> str:
        return f"{self.email} @ {self.vendor.name}"
