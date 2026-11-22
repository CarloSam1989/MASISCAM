import hashlib
from functools import lru_cache

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@lru_cache(maxsize=64)
def _huella(ruta, mtime):
    archivo = finders.find(ruta)
    if not archivo:
        return 'nuevo'
    with open(archivo, 'rb') as origen:
        return hashlib.sha256(origen.read()).hexdigest()[:12]


@register.simple_tag
def static_v(ruta):
    """URL estática con la huella del contenido: el navegador nunca mezcla un JS antiguo con una plantilla nueva."""
    archivo = finders.find(ruta)
    import os
    mtime = os.path.getmtime(archivo) if archivo else 0
    return f'{static(ruta)}?v={_huella(ruta, mtime)}'
