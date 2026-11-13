from django.contrib import admin
from .models import Empresa, Perfil

# Administracion tecnica solo para superusuarios; los roles usan el sistema privado.
def acceso_admin(request):
    from masiscam.access import rol_masiscam_usuario
    rol = rol_masiscam_usuario(usuario=request.user, empresa=getattr(request, 'empresa_activa', None))
    return bool(request.user.is_active and request.user.is_superuser and rol and rol.rol == 'ADMIN')


admin.site.has_permission = acceso_admin
admin.site.site_header = "Administracion MASISCAM"
admin.site.site_title = "MASISCAM"
admin.site.register((Empresa, Perfil))
