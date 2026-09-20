import logging

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from .models import Empresa, Perfil


logger = logging.getLogger(__name__)


def empresas_disponibles(user):
    if user.is_superuser:
        return Empresa.objects.filter(activa=True)
    return Empresa.objects.filter(activa=True, perfil__user=user, perfil__activo=True, perfil__rol_masiscam__activo=True).distinct()


class MasiscamLoginView(LoginView):
    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def form_invalid(self, form):
        logger.info(
            "Login inválido username=%r form_valid=%s authenticated=%s",
            form.data.get("username", ""),
            form.is_valid(),
            self.request.user.is_authenticated,
        )
        return super().form_invalid(form)

    def form_valid(self, form):
        response = super().form_valid(form)
        empresas = empresas_disponibles(self.request.user)
        logger.info(
            "Login válido username=%r authenticated=%s perfil=%s empresa=%s",
            self.request.user.get_username(),
            self.request.user.is_authenticated,
            empresas.filter(pk=self.request.session.get("empresa_activa_id")).exists(),
            empresas.exists(),
        )
        return response

    def get_success_url(self):
        redirect_url = self.get_redirect_url()
        if redirect_url:
            logger.info("Login redirección username=%r url=%s", self.request.user.get_username(), redirect_url)
            return redirect_url
        empresas = empresas_disponibles(self.request.user)
        empresa = empresas.filter(pk=self.request.session.get("empresa_activa_id")).first()
        if not empresa and empresas.count() == 1:
            empresa = empresas.first()
        if empresa:
            self.request.session["empresa_activa_id"] = empresa.pk
            logger.info("Login redirección username=%r url=/app/", self.request.user.get_username())
            return reverse("masiscam:dashboard")
        messages.error(self.request, "No se pudo completar el acceso a la organización. Contacta al administrador.")
        logger.info("Login redirección username=%r url=seleccionar_empresa", self.request.user.get_username())
        return reverse("accounts:seleccionar_empresa")


@login_required
def seleccionar_empresa(request):
    empresas = empresas_disponibles(request.user)
    if request.method == "POST":
        empresa = get_object_or_404(empresas, pk=request.POST.get("empresa_id"))
        request.session["empresa_activa_id"] = empresa.pk
        return redirect("masiscam:dashboard")
    return render(request, "accounts/seleccionar_empresa.html", {"empresas": empresas})
