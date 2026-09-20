from django.apps import AppConfig


class MasiscamConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "masiscam"
    verbose_name = "MASISCAM — Gestión Documental de Proyectos"

    def ready(self):
        from . import signals  # noqa: F401
