from django import template

from apps.core.validators import sanitize_plain_display_text

register = template.Library()


@register.filter(name="safe_display")
def safe_display(value):
    """Strip unsafe script/HTML tags from legacy untrusted text while keeping autoescaping enabled."""
    return sanitize_plain_display_text(value)
