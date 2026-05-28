from django import template
from django.utils.text import slugify

register = template.Library()


@register.filter
def to_slug(value):
    """
    Convert a string into a slug.
    """
    return slugify(value)
