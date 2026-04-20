from django import forms

from .models import Order, ProductReview


class OrderStatusForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ["status"]


class ProductReviewForm(forms.ModelForm):
    class Meta:
        model = ProductReview
        fields = ["reviewer_name", "rating", "comment"]
        widgets = {
            "reviewer_name": forms.TextInput(attrs={"placeholder": "Your name"}),
            "rating": forms.NumberInput(attrs={"min": 1, "max": 5}),
            "comment": forms.Textarea(attrs={"rows": 3, "placeholder": "Write your review"}),
        }
