from django import template

register = template.Library()


def _format_money(amount, currency_code):
    value = f"{amount}"
    code = (currency_code or "USD").upper()
    symbols = {
        "USD": "$",
        "EUR": "EUR",
        "GBP": "GBP",
        "KWD": "KWD",
        "AED": "AED",
    }
    if code in ("$", "USD"):
        return f"${value}"
    symbol = symbols.get(code, code)
    return f"{value} {symbol}"


@register.filter(name="money")
def money(amount, vendor):
    currency_code = getattr(vendor, "currency_code", "USD") if vendor else "USD"
    return _format_money(amount, currency_code)


@register.simple_tag
def money_with_code(amount, currency_code="USD"):
    return _format_money(amount, currency_code)
