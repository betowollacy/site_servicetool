import re

from django import template
from django.utils.html import urlize
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter(is_safe=True)
def richtext(value):
    if not value:
        return value
    html = urlize(value, autoescape=True)
    html = re.sub(r'<a (?![^>]*target=)', '<a target="_blank" rel="noopener" ', html)
    html = html.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br>')
    return mark_safe(html)