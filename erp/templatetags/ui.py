"""Presentation-only components; no database or workflow logic."""
from django import template
from django.templatetags.static import static
from django.utils.html import format_html

register = template.Library()
ICON_NAMES = frozenset(["menu","plus","search","bell","circle-help","log-out","house","file-plus-2","file-text","shopping-bag","printer","box","package","cylinder","users","store","wallet","chart-no-axes-combined","settings-2","circle-check","x","ellipsis","inbox","arrow-left","arrow-right","calendar-days","pencil","calculator","check","minus","trending-up","arrow-down","arrow-up","shopping-cart","triangle-alert","info","palette","building-2","layers","check-check","chevron-down","chevron-right","loader-circle","sliders-horizontal","save","file-down"])


@register.simple_tag
def icon(name):
    """Render a decorative icon from the local, versioned Lucide sprite."""
    if name not in ICON_NAMES:
        name = "circle-help"
    return format_html(
        '<svg class="icon" width="20" height="20" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="1.75" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" '
        'focusable="false"><use href="{}#{}"></use></svg>',
        static("icons/lucide.svg"), name,
    )
