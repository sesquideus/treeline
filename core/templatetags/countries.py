from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def flag(code):
    if not code or len(code) != 2:
        return ''
    return mark_safe(f'<img class="flag" src="https://flagpedia.net/data/flags/mini/{code}.png" alt="{code}"/>')
    return ''.join(chr(0x1F1E6 + ord(c) - ord('A')) for c in code.upper())


@register.filter
def flags(point):
    return mark_safe(''.join([flag(country.code) for country in point.country.all()]))


@register.simple_tag()
def flaglet(code: str):
    return mark_safe(f'<img class="flag" src="https://flagpedia.net/data/flags/mini/{code}.png" alt="{code}"/>')
