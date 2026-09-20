# Validacion realizada en la preparacion

- 33 pruebas Django aprobadas con migraciones reales en base temporal SQLite.
- `check`: sin errores.
- `makemigrations --check --dry-run`: sin cambios pendientes.
- `check --deploy`: sin incidencias con configuracion segura de ejemplo en memoria, sin crear .env.
- `collectstatic`: correcto, incluyendo logos, Bootstrap y fuentes locales.
- WSGI, ASGI y registro de tareas Celery: correctos.
- Docker Compose: configuracion validada; no se construyeron ni desplegaron contenedores.
- JavaScript local: sintaxis correcta.
- Navegador a 390, 768 y 1440 px: web adjunta, login al dashboard, crear/editar, precarga, ratio, etiqueta en pesta?a nueva con 100x100 mm y consulta publica correctos; sin errores JS ni scroll horizontal en web/formulario.
- Importacion selectiva: validacion sin guardar, importacion atomica, PK/tokens/enlaces Drive conservados y rechazo de sobrescritura.
- Drive remoto simulado: raiz/razon social/REDUCTORES/fecha-tipo; reutilizacion y reintentos sin duplicados ni mover/borrar carpetas.
- Los 61 archivos fuente registrados en SOURCE_MANIFEST.json conservan su hash original.
- Sin base de produccion, archivos privados ni credenciales reales en esta carpeta.

Pendiente de verificar en destino: construccion y arranque Docker/Linux, PostgreSQL real, TLS/dominio, importacion de datos reales y conexion a Google Drive con credenciales entregadas por separado.
