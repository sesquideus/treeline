from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Allow dict[variable_key] lookups in templates: {{ my_dict|get_item:key }}"""
    return dictionary.get(key)


@register.inclusion_tag('mountains/tags/tree-node.html')
def render_summit_tree(nodes, children_map):
    return {
        'nodes': nodes,
        'children_map': children_map,
    }

@register.inclusion_tag('mountains/tags/lineage-prominence.html')
def render_summit_lineage(node):
    return {
        'node': node,
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