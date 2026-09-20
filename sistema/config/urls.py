from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from accounts import public_views
from config.health import health
from masiscam.urls import urlpatterns as private_patterns
from masiscam.public_urls import urlpatterns as public_patterns

# Un namespace mantiene los reverse() existentes con prefijos propios.
masiscam_patterns = [path("app/", include(private_patterns)), path("consulta/", include(public_patterns))]
urlpatterns = [
    path("health/", health, name="health"),
    path("terminos/", public_views.terminos, name="terminos"),
    path("privacidad/", public_views.privacidad, name="privacidad"),
    path("", include(([path("", public_views.home, name="home"), path("consulta/", public_views.consulta, name="consulta")], "web"), namespace="web")),
    path("app/cuentas/", include("accounts.urls")),
    path("admin/", admin.site.urls),
    path("masiscam/", include((public_patterns, "legacy_public"), namespace="legacy_public")),
    path("", include((masiscam_patterns, "masiscam"), namespace="masiscam")),
]
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
# Los archivos privados no se exponen como /media/. Fotos y documentos se entregan
# por las vistas existentes, que validan token/estado/visibilidad.
