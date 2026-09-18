import re

from django import template
from django.utils import timezone
from django.utils.html import urlize
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter(is_safe=True)
def time_ago(value):
    if not value:
        return ''
    seconds = int((timezone.now() - value).total_seconds())
    if seconds < 60:
        return 'agora'
    minutes = seconds // 60
    if minutes < 60:
        return 'há {} min'.format(minutes)
    hours = minutes // 60
    if hours < 24:
        return 'há {} h'.format(hours)
    return 'há {} dia(s)'.format(hours // 24)


@register.filter(is_safe=True)
def get_item(value, key):
    if isinstance(value, dict):
        return value.get(key, '')
    return value


@register.filter(is_safe=True)
def richtext(value):
    if not value:
        return value
    value = re.sub(r'<br\s*/?>', '\n', str(value), flags=re.IGNORECASE)
    html = urlize(value, autoescape=True)
    html = re.sub(r'<a (?![^>]*target=)', '<a target="_blank" rel="noopener" ', html)
    html = html.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br>')
    return mark_safe(html)