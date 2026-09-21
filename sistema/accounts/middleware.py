from django.shortcuts import redirect
from django.core.exceptions import PermissionDenied
from .models import Empresa, Perfil


class EmpresaActivaMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.empresa_activa = None
        request.perfil_activo = None
        request.cliente_usuario = None
        if request.user.is_authenticated:
            from masiscam.access import acceso_cliente_usuario
            acceso_cliente = acceso_cliente_usuario(request.user)
            if acceso_cliente:
                request.cliente_usuario = acceso_cliente.cliente
                request.session["empresa_activa_id"] = acceso_cliente.perfil.empresa_id
                if request.path.startswith("/admin/") or request.path.startswith("/app/cuentas/empresa/"):
                    raise PermissionDenied("El cliente no tiene acceso a administración.")
            empresa_id = request.session.get("empresa_activa_id")
            perfiles = Perfil.objects.filter(user=request.user, activo=True, empresa__activa=True, rol_masiscam__activo=True).select_related("empresa")
            perfil = perfiles.filter(empresa_id=empresa_id).first() if empresa_id else None
            if not empresa_id and perfiles.count() == 1:
                perfil = perfiles.first()
            if perfil:
                request.empresa_activa = perfil.empresa
                request.perfil_activo = perfil
                request.session["empresa_activa_id"] = perfil.empresa_id
            elif request.user.is_superuser and empresa_id:
                request.empresa_activa = Empresa.objects.filter(pk=empresa_id, activa=True).first()
            if request.path.startswith("/app/") and not request.path.startswith("/app/cuentas/") and request.empresa_activa is None:
                return redirect("accounts:seleccionar_empresa")
        return self.get_response(request)
