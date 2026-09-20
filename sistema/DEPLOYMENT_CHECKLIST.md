# Checklist de publicación MASISCAM

Seguir deploy/CONTABO.md. Este archivo registra pasos pendientes, no significa que se haya desplegado.

## A. VPS

- [ ] Ubuntu24.04 confirmado, arquitectura/recursos dimensionados.
- [ ] Actualizaciones/reinicio nuevo host y timezone America/Guayaquil.
- [ ] Administrador con clave y sudo probado; consola recuperación.
- [ ] SSH root/password deshabilitados y valores efectivos revisados.
- [ ] Puerto SSH REAL permitido; UFW80/443 y fail2ban probados.
- [ ] Docker Engine/Compose oficiales instalados, acceso Docker restringido.

## B–D. Fuentes y entorno

- [ ] URL repo/rama/commit o paquete/hash confirmados; ruta /srv/masiscam/sistema.
- [ ] .env generado privado0600, sin placeholders ni SQLite producción.
- [ ] Dominio/www/origins/public base QR exactos; SECRET_KEY/PG password privados.
- [ ] Drive habilitado solo con JSON externo/root y Shared Drive si aplica.
- [ ] JSON UID10001/0400 y secrets0700; verificar_drive después de build/DB.
- [ ] Si no se usa Drive, GOOGLE_DRIVE_ENABLED=false.
- [ ] Responsable/contacto/plazos legales revisados y completados.

## E–H. Datos y aplicación

- [ ] Compose config --quiet correcto, único build/imagen app.
- [ ] Build Linux realizado; id10001, healthchecks/runtime comprobados.
- [ ] db/redis sanos, ningún puerto público PG/Redis/Gunicorn.
- [ ] Elegida rama nueva / dump independiente / exportación histórica selectiva.
- [ ] Restauración/importación solamente en destino vacío; dry-run revisado.
- [ ] Administradores importados revisados; no usuarios extra creados automáticamente.
- [ ] Tokens/PK/DriveIDs/relaciones/timestamps/conteos preservados.
- [ ] Media copiada/rutas/hash/owner10001/lectura comprobados.
- [ ] migrate plan/noinput y showmigrations/migrate --check aprobados.
- [ ] collectstatic completo, ninguna makemigrations en VPS.
- [ ] web/worker/nginx healthy; worker único, ningún Beat agregado.

## I–M. Publicación

- [ ] Smoke local health/home/login/consulta/static/media404.
- [ ] Backend127.0.0.1:8080 inaccesible remoto, no socket Docker.
- [ ] Bootstrap HTTP503/ACME antes del certificado.
- [ ] A/www/AAAA/TTL/CAA antiguos registrados; cambio manual por operador.
- [ ] Certificado NUEVO emitido solo tras challenge/DNS válido.
- [ ] Config TLS instalada tras existir archivos, nginx -t antes de reload.
- [ ] SAN/cadena/HTTP2/redirect/www/origins/cookies/headers correctos sin curl -k.
- [ ] Renovación webroot/timer/deploy hook y dry-run aprobados.
- [ ] Login/selección empresa/roles y acceso cruzado negativo verificados.
- [ ] Clientes/equipos/proyectos/documentos/fotos/registros/histórico/QR funcionales.
- [ ] /media/404; vistas privadas autorizadas, públicas solo token/visibilidad/estado.
- [ ] Drive diagnóstico y prueba autorizada de carga/reintento, sin duplicados.
- [ ] Enlaces QR antiguos hostname y alias conservados.

## N–O. Operación

- [ ] Backup -Fc/media/hashes completo, script restore-check aprobado.
- [ ] Backup externo cifrado y restore funcional aislado, RPO/RTO/retención acordados.
- [ ] Timer diario/alertas/rotación de logs y espacio monitorizados.
- [ ] Corte final con mantenimiento/drenaje/productores detenidos y escritor único.
- [ ] Monitoreo primeras horas sin5xx/OOM/restarts/errores Drive pendientes.
- [ ] Rollback preparado; no volver snapshot viejo si hay datos nuevos sin reconciliar.
- [ ] Infraestructura anterior conservada varios días; limpieza final autorizada.

## Limitaciones de validación local

- [ ] Construcción y runtime Docker/Linux pendientes si motor local no disponible.
- [ ] Drive real solo se valida con credencial del operador en destino.
- [ ] Revisión jurídica/contacto/titular no se inventan.
- [ ] Git remoto/rama/release/dominio/IP/DNS reales por confirmar.
