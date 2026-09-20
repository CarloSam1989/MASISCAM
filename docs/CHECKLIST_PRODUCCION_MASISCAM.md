# Checklist de producción MASISCAM

Dictamen actual: **LISTO PARA DESPLIEGUE**. Esta lista describe una instalación nueva en `/srv/masiscam/sistema`; no implica despliegue ejecutado.

Release validada localmente: `1.0.0-20260919`. Inventario: `sistema/RELEASE_MANIFEST.sha256` (136 archivos de release, sin secretos ni datos).

## Independencia y alcance

- [x] MASISCAM funciona sin SYSCloud en ejecución normal.
- [x] `importar_syscloud.py` está aislado como herramienta de migración selectiva.
- [x] Alcance confirmado: MASISCAM no incluye ERP, SRI, GPS, PWA SYSCloud ni APK SYSCloud.
- [x] Estrategia de datos documentada; elegir A o B antes del deploy y no mezclarlas.

## Django y migraciones

- [x] `manage.py`, settings, URLs, middleware, auth, timezone y seguridad identificados.
- [x] `manage.py check` PASS con entorno local simulado.
- [x] `makemigrations --check --dry-run` PASS; no crear migraciones en destino.
- [x] Migraciones sin dependencia CloudSYS.
- [x] Validar en Linux/Python 3.12 la instalación exacta del lock y repetir `check --deploy` con `psycopg2-binary`.
- [x] Ejecutar `migrate --plan`, `migrate --check` y `collectstatic` en imagen Linux aislada.
- [x] `collectstatic --noinput` en imagen Linux: 156 archivos sin cambios en volumen final.
- [x] PostgreSQL 16 vacío: `migrate --plan`, `migrate`, `migrate --check`, `check` y `check --deploy` PASS; solo warnings esperados por entorno HTTP local.

## Datos, auth y multiempresa

- [x] Perfil, empresa activa, rol y permisos backend revisados.
- [x] IDOR y acceso cruzado cubiertos por pruebas existentes.
- [x] Usuarios/empresas inactivos no deben acceder.
- [x] Importación en DB PostgreSQL vacía: dry-run/apply, conteos, PK, FK, tokens QR, IDs Drive, auditoría y secuencias PASS.
- [ ] Revisar administradores importados antes de abrir acceso en el VPS destino.

## PostgreSQL, Redis y Celery

- [x] PostgreSQL 16, volumen y healthcheck declarados.
- [x] Redis 7, AOF, DB0 broker y DB1 resultados declarados.
- [x] Celery worker concurrency 2, retries y timeouts declarados.
- [x] Celery Beat no es necesario ni está declarado.
- [x] DB/Redis/Gunicorn no tienen puertos publicados en Compose.
- [x] Backend loopback confirmado; DB/Redis/Gunicorn sin puertos publicados. Separación de rol PostgreSQL queda como endurecimiento opcional.

## Docker y Nginx

- [x] Servicios declarados: `db`, `redis`, `init`, `web`, `worker`, `nginx`.
- [x] Compose real validado con `docker compose config`.
- [x] Crear `.env.test` sintético, ignorado por `.gitignore`, sin secretos reales; `.env` no existe y no fue sobrescrito.
- [x] `.env` temporal copiado desde `.env.test`, usado solo para pruebas y eliminado al cierre.
- [x] Imagen Linux construida con lock/hash; Python 3.12, UID 10001, Gunicorn, Celery y librerías críticas verificados.
- [ ] Completar dominio/certificados de Nginx host y ejecutar `nginx -t` durante el despliegue Contabo.
- [x] Nginx Docker `nginx -t`/`nginx -T` y smoke static/media/proxy PASS. Nginx host conserva placeholders y se valida en Contabo.
- [x] `/media/` no se publica directamente; mantener vistas autorizadas y static read-only.

## Google Drive

- [x] Drive es opcional y no sustituye a SYSCloud.
- [x] Credencial se diseña fuera de Git/imagen y read-only en contenedor.
- [x] Scope, raíz, retries, timeout, idempotencia y fallo Drive/DB revisados.
- [x] Tests usan mocks; no se contactó Drive real.
- [ ] Si se habilita: credencial externa, raíz autorizada, permisos mínimos y diagnóstico read-only.
- [ ] Probar una carga controlada y reentrega Celery sin duplicados.

## Frontend y pruebas

- [x] Web pública, login, dashboard, clientes, proyectos, equipos, documentos, registros, QR y legales presentes.
- [x] Bootstrap/assets locales y media protegida identificados.
- [x] QA Playwright local en 375/390/768/1366/1920: sin errores JS ni HTTP fallidos.
- [x] Búsqueda de `.url`, `FieldFile` y `/media/`: sin entrega privada directa en templates/JS.
- [x] Tests de importador: 6 PASS.
- [x] Suite focalizada Linux con `GOOGLE_DRIVE_ENABLED=true`: 33 PASS / 0 FAIL.
- [x] Suite completa Linux con dependencias del lock: 52 PASS / 0 FAIL, incluido test PostgreSQL.

## Contabo, backup y operación

- [ ] Ubuntu 24.04, recursos, SSH por clave, UFW y fail2ban confirmados en VPS nuevo.
- [ ] Docker Engine y Compose plugin instalados y versionados.
- [ ] Fuentes verificadas por release/hash en `/srv/masiscam/sistema`.
- [ ] `.env` 0600, secrets 0700/0400 y propietario UID 10001.
- [x] Backup PostgreSQL `-Fc`, media y hashes completado en `backups/local-audit`.
- [x] Restore aislado comprobado con `pg_restore --list`, conteos, PK/FK/tokens/Drive IDs/auditoría y media SHA256.
- [ ] Backup externo cifrado, retención, timer y alertas configurados.
- [x] Backup/restore aislado ejecutado localmente en PostgreSQL 16/Docker.
- [ ] DNS, Certbot, TLS y smoke tests comprobados sin `curl -k`.
- [ ] Verificar que no estén expuestos 5432, 6379, 8000 ni 8080 públicamente.
- [ ] Ventana de corte, escritor único, drenaje Celery y rollback definidos.

## No ejecutar como parte de esta auditoría

- [x] No se hizo deploy.
- [x] No se ejecutó `docker compose up`, `down`, `restart` ni build productivo.
- [x] No se borraron archivos ni datos.
- [x] No se usó CloudSYS como sustituto.
- [x] No se contactó Google Drive real.
- [x] No se hizo push.
