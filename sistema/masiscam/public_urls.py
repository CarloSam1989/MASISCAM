from django.urls import path
from . import views

urlpatterns = [
    path("equipo/<str:token>/documentos/<slug:archivo_id>/ver/", views.equipo_documento_drive, name="equipo_documento_drive"),
    path("equipo/<str:token>/", views.equipo_publico, name="equipo_publico"),
    path("equipo/<str:token>/foto/<str:tipo>/", views.equipo_foto_publica, name="equipo_foto_publica"),
    path("publico/<str:token>/", views.publico, name="publico"),
    path("publico/<str:token>/imagen/<str:tipo>/<int:objeto_pk>/", views.imagen_proyecto_publica, name="imagen_proyecto_publica"),
    path("publico/<str:token>/documentos/<int:documento_pk>/", views.documento_publico, name="documento_publico"),
]
