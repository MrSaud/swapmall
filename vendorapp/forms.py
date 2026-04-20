from datetime import timedelta

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.images import get_image_dimensions
from django.utils import timezone

from .access import user_vendor_queryset
from .currencies import ISO_4217_CODE_SET, ISO_4217_CURRENCIES
from .models import (
    HeroSlide,
    Offer,
    Package,
    PackageInvoice,
    Product,
    SavedFilter,
    StaffPermission,
    StaffInvite,
    SupportTicket,
    Vendor,
    VendorCategory,
    VendorSettings,
    VendorStaff,
)

User = get_user_model()


class VendorSettingsForm(forms.ModelForm):
    MAX_LOGO_SIZE_BYTES = 100 * 1024
    MIN_LOGO_WIDTH = 300
    MIN_LOGO_HEIGHT = 100
    MAX_LOGO_WIDTH = 1000
    MAX_LOGO_HEIGHT = 1000
    currency_code = forms.CharField(max_length=3, label="Currency")

    class Meta:
        model = VendorSettings
        fields = [
            "theme_name",
            "primary_color",
            "secondary_color",
            "background_color",
            "text_color",
            "vendor_logo",
            "instagram_url",
            "facebook_url",
            "tiktok_url",
            "x_url",
            "youtube_url",
        ]
        widgets = {
            "primary_color": forms.TextInput(attrs={"type": "color"}),
            "secondary_color": forms.TextInput(attrs={"type": "color"}),
            "background_color": forms.TextInput(attrs={"type": "color"}),
            "text_color": forms.TextInput(attrs={"type": "color"}),
        }

    def __init__(self, *args, **kwargs):
        self.vendor = kwargs.pop("vendor", None)
        super().__init__(*args, **kwargs)
        self.fields["vendor_logo"].help_text = "Logo must be between 300x100 and 1000x1000 px, and 100 KB or less."
        self.fields["currency_code"].help_text = "Use 3-letter ISO code (examples: USD, EUR, KWD, AED)."
        self.fields["currency_code"].widget.attrs.update(
            {"placeholder": "USD", "style": "text-transform:uppercase;", "list": "currency-code-list"}
        )
        self.currency_options = ISO_4217_CURRENCIES
        if self.vendor:
            self.fields["currency_code"].initial = self.vendor.currency_code
        for color_field in ("primary_color", "secondary_color", "background_color", "text_color"):
            self.fields[color_field].widget.attrs.setdefault("style", "width:64px;height:42px;padding:4px;")

    def clean_vendor_logo(self):
        logo = self.cleaned_data.get("vendor_logo")
        if not logo:
            return logo

        if logo.size > self.MAX_LOGO_SIZE_BYTES:
            raise ValidationError("Logo file is too large. Maximum size is 100 KB.")

        try:
            width, height = get_image_dimensions(logo)
        except Exception as exc:
            raise ValidationError("Upload a valid image file.") from exc
        finally:
            if hasattr(logo, "seek"):
                logo.seek(0)

        if width < self.MIN_LOGO_WIDTH or height < self.MIN_LOGO_HEIGHT:
            raise ValidationError("Logo is too small. Minimum dimensions are 300x100 pixels.")
        if width > self.MAX_LOGO_WIDTH or height > self.MAX_LOGO_HEIGHT:
            raise ValidationError("Logo is too large in dimensions. Maximum is 1000x1000 pixels.")

        return logo

    def save(self, commit=True):
        settings_obj = super().save(commit=commit)
        if self.vendor:
            self.vendor.currency_code = self.cleaned_data["currency_code"]
            if commit:
                self.vendor.save(update_fields=["currency_code"])
        return settings_obj

    def clean_currency_code(self):
        code = (self.cleaned_data.get("currency_code") or "").strip().upper()
        legacy_map = {"$": "USD", "AEU": "AED"}
        code = legacy_map.get(code, code)
        if len(code) != 3 or not code.isalpha():
            raise ValidationError("Currency must be a valid 3-letter ISO code (e.g. USD, EUR, KWD).")
        if code not in ISO_4217_CODE_SET:
            raise ValidationError("Unknown currency code. Pick a valid ISO 4217 code from the list.")
        return code


class VendorStaffCreateForm(forms.Form):
    vendor = forms.ModelChoiceField(queryset=Vendor.objects.none())
    username = forms.CharField(max_length=150)
    email = forms.EmailField(required=False)
    password = forms.CharField(widget=forms.PasswordInput)
    role = forms.ChoiceField(choices=VendorStaff.ROLE_CHOICES)

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user")
        super().__init__(*args, **kwargs)
        if user.is_superuser:
            self.fields["vendor"].queryset = Vendor.objects.filter(is_active=True)
        else:
            self.fields["vendor"].queryset = Vendor.objects.filter(
                staff_members__user=user, staff_members__is_active=True, is_active=True
            ).distinct()

    def clean_username(self):
        username = self.cleaned_data["username"]
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("This username already exists.")
        return username

    def save(self):
        vendor = self.cleaned_data["vendor"]
        user = User.objects.create_user(
            username=self.cleaned_data["username"],
            email=self.cleaned_data.get("email", ""),
            password=self.cleaned_data["password"],
        )
        membership = VendorStaff.objects.create(vendor=vendor, user=user, role=self.cleaned_data["role"])
        return user, membership


class SuperAdminVendorUserCreateForm(forms.Form):
    vendor_name = forms.CharField(max_length=120)
    vendor_slug = forms.SlugField(max_length=140)
    vendor_description = forms.CharField(widget=forms.Textarea, required=False)
    username = forms.CharField(max_length=150)
    email = forms.EmailField(required=False)
    password = forms.CharField(widget=forms.PasswordInput)
    role = forms.ChoiceField(choices=VendorStaff.ROLE_CHOICES, initial=VendorStaff.ROLE_MANAGER)

    def clean_vendor_slug(self):
        vendor_slug = self.cleaned_data["vendor_slug"]
        if Vendor.objects.filter(slug=vendor_slug).exists():
            raise forms.ValidationError("This vendor slug already exists.")
        return vendor_slug

    def clean_username(self):
        username = self.cleaned_data["username"]
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("This username already exists.")
        return username

    def save(self):
        vendor = Vendor.objects.create(
            name=self.cleaned_data["vendor_name"],
            slug=self.cleaned_data["vendor_slug"],
            description=self.cleaned_data.get("vendor_description", ""),
        )
        VendorSettings.objects.get_or_create(vendor=vendor)
        user = User.objects.create_user(
            username=self.cleaned_data["username"],
            email=self.cleaned_data.get("email", ""),
            password=self.cleaned_data["password"],
        )
        membership = VendorStaff.objects.create(vendor=vendor, user=user, role=self.cleaned_data["role"])
        return vendor, user, membership


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        if not data:
            return []
        if isinstance(data, (list, tuple)):
            return [super().clean(file_obj, initial) for file_obj in data]
        return [super().clean(data, initial)]


class ProductForm(forms.ModelForm):
    additional_images = MultipleFileField(
        required=False,
        help_text="You can select multiple extra images.",
    )

    class Meta:
        model = Product
        fields = [
            "vendor",
            "category_ref",
            "name",
            "sku",
            "description",
            "main_thumbnail",
            "is_digital",
            "digital_file",
            "price",
            "stock_quantity",
            "is_active",
        ]

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user")
        selected_vendor = kwargs.pop("selected_vendor", None)
        super().__init__(*args, **kwargs)
        vendor_qs = user_vendor_queryset(user)
        self.fields["vendor"].queryset = vendor_qs
        category_qs = VendorCategory.objects.filter(vendor__in=vendor_qs, is_active=True)
        if selected_vendor:
            category_qs = category_qs.filter(vendor=selected_vendor)
        self.fields["category_ref"].queryset = category_qs

    def save(self, commit=True):
        product = super().save(commit=False)
        product.category = product.category_ref.name if product.category_ref else ""
        if commit:
            product.save()
        return product

    def clean(self):
        cleaned = super().clean()
        is_digital = cleaned.get("is_digital")
        digital_file = cleaned.get("digital_file")
        if is_digital and not digital_file and not getattr(self.instance, "digital_file", None):
            self.add_error("digital_file", "Digital file is required for digital products.")
        return cleaned


class VendorCategoryForm(forms.ModelForm):
    class Meta:
        model = VendorCategory
        fields = ["vendor", "name", "description", "is_active"]

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user")
        selected_vendor = kwargs.pop("selected_vendor", None)
        super().__init__(*args, **kwargs)
        vendor_qs = user_vendor_queryset(user)
        if selected_vendor:
            vendor_qs = vendor_qs.filter(id=selected_vendor.id)
        self.fields["vendor"].queryset = vendor_qs
        if not user.is_superuser and selected_vendor:
            self.fields["vendor"].initial = selected_vendor
            self.fields["vendor"].widget = forms.HiddenInput()


class PackageForm(forms.ModelForm):
    class Meta:
        model = Package
        fields = ["vendor", "name", "max_products", "starts_on", "ends_on", "is_active", "notes"]


class SupportTicketForm(forms.ModelForm):
    class Meta:
        model = SupportTicket
        fields = ["title", "description", "priority", "status", "assigned_to"]


class StaffInviteForm(forms.ModelForm):
    class Meta:
        model = StaffInvite
        fields = ["vendor", "email", "role", "expires_at"]

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user")
        selected_vendor = kwargs.pop("selected_vendor", None)
        super().__init__(*args, **kwargs)
        self.fields["vendor"].queryset = user_vendor_queryset(user)
        if selected_vendor:
            self.fields["vendor"].initial = selected_vendor
        self.fields["expires_at"].initial = timezone.now() + timedelta(days=7)


class SavedFilterForm(forms.ModelForm):
    class Meta:
        model = SavedFilter
        fields = ["name", "page", "query_string"]


class PackageInvoiceForm(forms.ModelForm):
    class Meta:
        model = PackageInvoice
        fields = ["package", "vendor", "amount", "currency_code", "due_date", "is_paid", "notes"]


class StaffPermissionForm(forms.ModelForm):
    class Meta:
        model = StaffPermission
        fields = ["membership", "module", "action", "is_allowed"]

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user")
        selected_vendor = kwargs.pop("selected_vendor", None)
        super().__init__(*args, **kwargs)
        membership_qs = VendorStaff.objects.select_related("vendor", "user").filter(vendor__in=user_vendor_queryset(user))
        if selected_vendor:
            membership_qs = membership_qs.filter(vendor=selected_vendor)
        self.fields["membership"].queryset = membership_qs


class ProductStockAdjustForm(forms.Form):
    quantity_delta = forms.IntegerField(
        label="Stock delta",
        help_text="Use positive number to increase stock, negative to reduce stock.",
    )
    note = forms.CharField(max_length=255, required=False)


class OfferForm(forms.ModelForm):
    starts_at = forms.DateTimeField(widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    ends_at = forms.DateTimeField(widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))

    class Meta:
        model = Offer
        fields = [
            "name",
            "offer_type",
            "products",
            "discount_type",
            "discount_value",
            "starts_at",
            "ends_at",
            "is_active",
        ]
        widgets = {
            "products": forms.SelectMultiple(attrs={"size": 8}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user")
        selected_vendor = kwargs.pop("selected_vendor", None)
        super().__init__(*args, **kwargs)
        vendor_qs = user_vendor_queryset(user)
        if selected_vendor:
            vendor_qs = vendor_qs.filter(id=selected_vendor.id)
        self.fields["products"].queryset = Product.objects.filter(vendor__in=vendor_qs).order_by("name")
        self.fields["products"].label_from_instance = lambda obj: f"{obj.name} | SKU: {obj.sku}"
        if self.instance and self.instance.pk:
            self.fields["starts_at"].initial = self.instance.starts_at.strftime("%Y-%m-%dT%H:%M")
            self.fields["ends_at"].initial = self.instance.ends_at.strftime("%Y-%m-%dT%H:%M")

    def clean(self):
        cleaned = super().clean()
        starts_at = cleaned.get("starts_at")
        ends_at = cleaned.get("ends_at")
        products = cleaned.get("products")
        offer_type = cleaned.get("offer_type")
        discount_type = cleaned.get("discount_type")
        discount_value = cleaned.get("discount_value")

        if starts_at and ends_at and starts_at >= ends_at:
            self.add_error("ends_at", "End time must be later than start time.")
        if products and offer_type == Offer.TYPE_PRODUCT and products.count() != 1:
            self.add_error("products", "Single Product offer must have exactly one product selected.")
        if products and offer_type == Offer.TYPE_COLLECTION and products.count() < 2:
            self.add_error("products", "Collection offer must have at least 2 products selected.")
        if discount_value is not None and discount_value <= 0:
            self.add_error("discount_value", "Discount value must be greater than 0.")
        if discount_type == Offer.DISCOUNT_PERCENT and discount_value and discount_value > 100:
            self.add_error("discount_value", "Percent discount cannot exceed 100.")
        return cleaned


class HeroSlideForm(forms.ModelForm):
    class Meta:
        model = HeroSlide
        fields = ["title", "subtitle", "sort_order", "is_active"]
