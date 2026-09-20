from urllib.parse import urlsplit
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET
from masiscam.models import Equipo, Proyecto


@require_GET
def home(request):
    return render(request, "web/home.html")


@require_GET
def consulta(request):
    code = request.GET.get("code", "").strip()[:200]
    equipo = None
    nota = ""
    if code:
        # Aceptar token o enlace QR, sin redirigir a dominios suministrados.
        token = urlsplit(code).path.rstrip("/").rsplit("/", 1)[-1]
        publicos = Equipo.objects.filter(consulta_publica_activa=True, proyecto__empresa__activa=True).exclude(estado=Equipo.Estado.INACTIVO).exclude(proyecto__estado=Proyecto.Estado.ARCHIVADO)
        equipo = publicos.filter(token_publico=token).first()
        if equipo is None:
            coincidencias = list(publicos.filter(Q(nombre__iexact=code) | Q(numero_serie__iexact=code))[:2])
            if len(coincidencias) == 1:
                equipo = coincidencias[0]
        if equipo:
            return redirect("masiscam:equipo_publico", token=equipo.token_publico)
        nota = "No se encontro una ficha publica unica. Revise el codigo o use el enlace QR."
    return render(request, "web/consulta.html", {"code": code, "nota": nota})


@require_GET
def terminos(request):
    return render(request, "web/legal.html", {"legal_type": "terminos"})


@require_GET
def privacidad(request):
    return render(request, "web/legal.html", {"legal_type": "privacidad"})
