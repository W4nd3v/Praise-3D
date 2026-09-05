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
    if value == "art":
        return "art"
    if value in {"paid", "received", "ready", "completed", "converted", "approved", "effected", "ok"}:
        return "success"
    if value in {"cancelled", "expired", "critical", "blocked", "inactive"}:
        return "danger"
    if value in {"warning", "pending", "waiting", "partial", "material"}:
        return "warning"
    return "info"


@register.filter
def get_item(mapping, key):
    return mapping.get(str(key), mapping.get(key, "")) if mapping else ""


@register.filter
def stock_label(value):
    """Translate display keys without changing stock thresholds or quantities."""
    return {"ok": "Disponível", "warning": "Atenção", "critical": "Crítico", "inactive": "Inativo", "paused": "Sem alertas"}.get(str(value), value)
