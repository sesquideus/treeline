from typing import Optional

from django import template
from django.template.defaultfilters import floatformat

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Allow dict[variable_key] lookups in templates: {{ my_dict|get_item:key }}"""
    return dictionary.get(key)


@register.inclusion_tag('mountains/tags/prominence-node.html')
def prominence_tree(nodes, children_map):
    return {
        'nodes': nodes,
        'children_map': children_map,
    }


@register.inclusion_tag('mountains/tags/isolation-node.html')
def isolation_tree(nodes, children_map):
    return {
        'nodes': nodes,
        'children_map': children_map,
    }

@register.inclusion_tag('mountains/tags/lineage-prominence.html')
def render_summit_lineage(node, encirclement_parent_pk):
    return {
        'node': node,
        'encirclement_parent_pk': encirclement_parent_pk,
    }


@register.inclusion_tag('mountains/tags/lineage-prominence-encirclement.html')
def render_encirclement_lineage(node):
    return {
        'node': node,
    }


@register.inclusion_tag('mountains/tags/lineage-isolation.html')
def render_isolation_lineage(node):
    return {
        'node': node,
    }


@register.filter
def multiply(x, y):
    return x * y


@register.filter
def distance(dist_m):
    return f"{dist_m / 1000:.3f} km"


@register.filter
def altitude(alt_m: Optional[float]):
    if not alt_m:
        return "? m"
    return f"{alt_m:.1f} m"