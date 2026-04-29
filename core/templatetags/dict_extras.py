from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """Usage: {{ my_dict|get_item:key_var }}"""
    if isinstance(dictionary, dict):
        return dictionary.get(key, '')
    return ''