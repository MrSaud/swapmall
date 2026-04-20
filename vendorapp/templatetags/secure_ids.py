from django import template

from SWAPMALL.secure_params import encrypt_int, encrypt_value

register = template.Library()


@register.filter(name="encid")
def encid(value):
    if value in (None, ""):
        return ""
    return encrypt_int(int(value))


@register.filter(name="enctext")
def enctext(value):
    if value in (None, ""):
        return ""
    return encrypt_value(str(value))
