import math
from typing import Optional

from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Allow dict[variable_key] lookups in templates: {{ my_dict|get_item:key }}"""
    return dictionary.get(key)


@register.inclusion_tag('mountains/summit/tags/prominence/node.html')
def prominence_tree(nodes, tree):
    return {
        'nodes': nodes,
        'tree': tree,
    }


@register.inclusion_tag('mountains/summit/tags/isolation/node.html')
def isolation_tree(nodes, tree):
    return {
        'nodes': nodes,
        'tree': tree,
    }


@register.inclusion_tag('mountains/summit/tags/slope/node.html')
def slope_tree(nodes, tree):
    return {
        'nodes': nodes,
        'tree': tree,
    }


@register.inclusion_tag('mountains/summit/tags/horizon/node.html')
def horizon_tree(nodes, tree):
    return {
        'nodes': nodes,
        'tree': tree,
    }


@register.inclusion_tag('mountains/summit/tags/prominence/lineage.html')
def render_prominence_lineage(node, encirclement_parent_pk):
    return {
        'node': node,
        'encirclement_parent_pk': encirclement_parent_pk,
    }


@register.inclusion_tag('mountains/summit/tags/lineage-prominence-encirclement.html')
def render_encirclement_lineage(node):
    return {
        'node': node,
    }


@register.inclusion_tag('mountains/summit/tags/isolation/lineage.html')
def render_isolation_lineage(node):
    return {
        'node': node,
    }


@register.inclusion_tag('mountains/summit/tags/slope/lineage.html')
def render_slope_lineage(node):
    return {
        'node': node,
    }


@register.inclusion_tag('mountains/summit/tags/horizon/lineage.html')
def render_horizon_lineage(node):
    return {
        'node': node,
    }


@register.filter
def multiply(x, y):
    return x * y


@register.filter
def degrees(angle):
    return angle * 180 / math.pi


@register.filter
def distance(dist_m):
    if not dist_m:
        return "?"
    return f"{dist_m / 1000:.3f}"


@register.filter
def altitude(alt_m: Optional[float]):
    if not alt_m:
        return "?"
    return f"{alt_m:.1f}"


@register.filter
def diff_altitude(dh):
    if not dh:
        return "?"
    return f"{dh:+.1f}"


@register.filter
def slope(angle):
    if not angle:
        return "?"
    return f"{degrees(angle):+.2f}"


@register.filter
def slope_colour(angle):
    if not angle:
        return "grey";
    return f"hsl({120 - 4 * degrees(angle)}, 60%, 40%)"