from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from .models import Empresa, Perfil


def empresas_disponibles(user):
    if user.is_superuser:
        return Empresa.objects.filter(activa=True)
    return Empresa.objects.filter(activa=True, perfil__user=user, perfil__activo=True, perfil__rol_masiscam__activo=True).distinct()


class MasiscamLoginView(LoginView):
    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        empresas = empresas_disponibles(self.request.user)
        empresa = empresas.filter(pk=self.request.session.get("empresa_activa_id")).first()
        if not empresa and empresas.count() == 1:
            empresa = empresas.first()
        if empresa:
            self.request.session["empresa_activa_id"] = empresa.pk
            return reverse("masiscam:dashboard")
        return reverse("accounts:seleccionar_empresa")


@login_required
def seleccionar_empresa(request):
    empresas = empresas_disponibles(request.user)
    if request.method == "POST":
        empresa = get_object_or_404(empresas, pk=request.POST.get("empresa_id"))
        request.session["empresa_activa_id"] = empresa.pk
        return redirect("masiscam:dashboard")
    return render(request, "accounts/seleccionar_empresa.html", {"empresas": empresas})
