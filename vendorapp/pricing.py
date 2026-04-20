from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

from .models import Offer

_TWO_DP = Decimal("0.01")


def _to_money(value):
    return Decimal(value).quantize(_TWO_DP, rounding=ROUND_HALF_UP)


def _discount_amount(base_price, offer):
    base = _to_money(base_price)
    if base <= 0:
        return Decimal("0.00")

    if offer.discount_type == Offer.DISCOUNT_PERCENT:
        pct = Decimal(offer.discount_value or 0)
        if pct <= 0:
            return Decimal("0.00")
        if pct > 100:
            pct = Decimal("100")
        return _to_money(base * (pct / Decimal("100")))

    fixed = Decimal(offer.discount_value or 0)
    if fixed <= 0:
        return Decimal("0.00")
    if fixed > base:
        fixed = base
    return _to_money(fixed)


def apply_best_offer_to_products(products, at_time=None):
    now = at_time or timezone.now()
    product_list = list(products)
    if not product_list:
        return product_list

    product_ids = [p.id for p in product_list]
    product_by_id = {p.id: p for p in product_list}
    vendor_ids = {p.vendor_id for p in product_list}

    offers = (
        Offer.objects.filter(
            vendor_id__in=vendor_ids,
            products__id__in=product_ids,
            is_active=True,
            starts_at__lte=now,
            ends_at__gte=now,
        )
        .prefetch_related("products")
        .distinct()
    )

    best_by_product = {}
    for offer in offers:
        for offered_product in offer.products.all():
            pid = offered_product.id
            if pid not in product_ids:
                continue
            product = product_by_id.get(pid)
            if not product:
                continue
            discount = _discount_amount(product.price, offer)
            if discount <= 0:
                continue
            current = best_by_product.get(pid)
            if not current or discount > current["discount_amount"]:
                best_by_product[pid] = {"offer": offer, "discount_amount": discount}

    for product in product_list:
        original = _to_money(product.price)
        best = best_by_product.get(product.id)
        if best:
            discount = best["discount_amount"]
            final_price = _to_money(max(Decimal("0.00"), original - discount))
            product.display_price = final_price
            product.original_price = original
            product.discount_amount = discount
            product.has_discount = final_price < original
            product.active_offer_name = best["offer"].name
        else:
            product.display_price = original
            product.original_price = original
            product.discount_amount = Decimal("0.00")
            product.has_discount = False
            product.active_offer_name = ""
    return product_list


def product_effective_price(product, at_time=None):
    apply_best_offer_to_products([product], at_time=at_time)
    return getattr(product, "display_price", _to_money(product.price))
