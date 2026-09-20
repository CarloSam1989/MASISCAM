from django.contrib import admin
from .models import Auditoria, Cliente, Documento, Equipo, Proyecto, RolMasiscam
admin.site.register((RolMasiscam, Cliente, Proyecto, Equipo, Documento, Auditoria))
