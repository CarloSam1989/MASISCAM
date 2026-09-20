from django.contrib import admin
from .models import Empresa, Perfil

# Administracion tecnica solo para superusuarios; los roles usan el sistema privado.
admin.site.has_permission = lambda request: request.user.is_active and request.user.is_superuser
admin.site.site_header = "Administracion MASISCAM"
admin.site.site_title = "MASISCAM"
admin.site.register((Empresa, Perfil))
