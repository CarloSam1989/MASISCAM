from django.conf import settings
from django.db import models


class Empresa(models.Model):
    """Organizacion local para aislar datos; propia del producto MASISCAM."""
    nombre = models.CharField(max_length=200)
    direccion = models.CharField(max_length=300, blank=True)
    activa = models.BooleanField(default=True)

    def __str__(self):
        return self.nombre


class Perfil(models.Model):
    """Membresia local; RolMasiscam define los permisos existentes."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="perfiles")
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    activo = models.BooleanField(default=True)

    class Meta:
        unique_together = (("user", "empresa"),)

    def __str__(self):
        return f"{self.user} / {self.empresa}"
