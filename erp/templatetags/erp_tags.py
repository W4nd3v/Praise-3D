from decimal import Decimal
from django import template
from django.utils.formats import number_format

register = template.Library()


@register.filter
def brl(value):
    try:
        value = Decimal(value or 0)
    except Exception:
        value = Decimal("0")
    return f"R$ {number_format(value, 2, use_l10n=True)}"


@register.filter
def qty(value):
    try:
        value = Decimal(value or 0)
    except Exception:
        value = Decimal("0")
    return number_format(value, 3, use_l10n=True).rstrip("0").rstrip(",")


@register.filter
def status_class(value):
    value = str(value or "").lower()
    if value in {"paid", "received", "ready", "completed", "converted", "approved", "ok"}:
        return "success"
    if value in {"cancelled", "expired", "critical", "blocked"}:
        return "danger"
    if value in {"warning", "pending", "waiting", "partial"}:
        return "warning"
    return "info"


@register.filter
def get_item(mapping, key):
    return mapping.get(str(key), mapping.get(key, "")) if mapping else ""

