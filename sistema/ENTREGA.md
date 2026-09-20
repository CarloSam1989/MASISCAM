# Entrega final — MASISCAM independiente

Fecha: 18 de septiembre de 2026. Solo se modificó la copia local. No hubo despliegue remoto, DNS, credenciales reales ni git push.

## 1. Arquitectura final

Django5.2.17 sirve web pública, login, legales y panel. Compose `masiscam`: PostgreSQL16 `db`, Redis7 AOF `redis`, inicializador `init`, Gunicorn `web`, Celery worker `worker` y Nginx `nginx`. `init/web/worker` comparten `masiscam-app:${MASISCAM_RELEASE:-local}`; solo init construye. Nginx Docker publica127.0.0.1:8080. Nginx host termina TLS/HTTP2. Drive es externo/opcional. No Beat ni servicios ajenos.

## 2. Archivos modificados (36)

- `sistema/.dockerignore`
- `sistema/.env.example`
- `sistema/.gitignore`
- `sistema/accounts/management/commands/importar_syscloud.py`
- `sistema/accounts/management/commands/preparar_masiscam.py`
- `sistema/accounts/models.py`
- `sistema/accounts/public_views.py`
- `sistema/accounts/views.py`
- `sistema/config/settings.py`
- `sistema/config/test_settings.py`
- `sistema/config/urls.py`
- `sistema/deploy/nginx/default.conf`
- `sistema/docker-compose.yml`
- `sistema/Dockerfile`
- `sistema/masiscam/access.py`
- `sistema/masiscam/forms.py`
- `sistema/masiscam/management/commands/reintentar_drive_equipo.py`
- `sistema/masiscam/models.py`
- `sistema/masiscam/services.py`
- `sistema/masiscam/tasks.py`
- `sistema/masiscam/templates/masiscam/base.html`
- `sistema/masiscam/templates/masiscam/equipo_etiqueta.html`
- `sistema/masiscam/templates/masiscam/equipo_publico.html`
- `sistema/masiscam/templates/masiscam/proyecto_detalle.html`
- `sistema/masiscam/templates/masiscam/publico.html`
- `sistema/masiscam/test_drive_equipo.py`
- `sistema/masiscam/urls.py`
- `sistema/masiscam/views.py`
- `sistema/README.md`
- `sistema/requirements.txt`
- `sistema/static/app/private.css`
- `sistema/static/web/styles.css`
- `sistema/templates/accounts/login.html`
- `sistema/templates/web/consulta.html`
- `sistema/templates/web/home.html`
- `sistema/tools/verify_frontend.py`

## 3. Archivos creados (28)

- `sistema/.env.production.example`
- `sistema/accounts/test_import_security.py`
- `sistema/config/health.py`
- `sistema/deploy/CONTABO.md`
- `sistema/deploy/nginx-host/bootstrap.conf.example`
- `sistema/deploy/nginx-host/masiscam.conf.example`
- `sistema/deploy/scripts/backup.sh`
- `sistema/deploy/scripts/restore-check.sh`
- `sistema/deploy/systemd/masiscam-backup.service`
- `sistema/deploy/systemd/masiscam-backup.timer`
- `sistema/DEPLOYMENT_CHECKLIST.md`
- `sistema/masiscam/management/commands/verificar_drive.py`
- `sistema/masiscam/migrations/0008_documento_drive_upload_id.py`
- `sistema/masiscam/templates/masiscam/proyectos.html`
- `sistema/masiscam/test_security.py`
- `sistema/RELEASE_MANIFEST.sha256`
- `sistema/requirements-dev.txt`
- `sistema/requirements.lock`
- `sistema/static/app/identity.css`
- `sistema/static/app/login.css`
- `sistema/templates/web/legal.html`
- `sistema/tools/check_production.py`
- `sistema/tools/media_manifest.py`
- `sistema/tools/render_nginx.py`
- `sistema/tools/setup_env.py`
- `sistema/tools/validate_local.py`
- `sistema/ENTREGA.md`
- `README.md` y `.gitignore` de raíz (contados aquí como dos archivos adicionales)

Movidos, sin borrar, a `auditoria/legacy-web/`: `index.html`, `script.js`, `styles.css`, `styles-v2.css`, `assets/`. Era la demo reemplazada por Django. Logos y fuentes históricas se conservaron.

## 4. Problemas corregidos

Independencia runtime; media sin `.url` ni `/media/` abierto; controles login/empresa/rol/proyecto/token/estado; path traversal y errores internos; PostgreSQL obligatorio en producción; URL QR HTTPS validada; health DB mínimo; Drive read-only diagnóstico e idempotencia con ID reservado; importer dry-run/reporte/FK/empresa/privilegios/atomicidad; upload MIME/extensión/magic/Office/fotos; imagen app única/loopback/health/log limits; lock hashes; Pillow11.3→12.3; backup/restore; login/panel/páginas legales responsive.

## 5. Pendientes

Dominio/IP/www/IPv6, URL/rama/commit Git, puerto SSH, empresa/usuario y tamaño VPS; credencial/root Drive; revisión jurídica/contacto/retención; build/runtime Linux porque daemon local no estaba activo; Nginx/cert/DNS reales; backup externo/monitoring elegidos por operador. `check --deploy` conserva W005/W021 porque HSTS subdomains/preload comienza false. PostgreSQL usa rol init simple; rol mínimo separado es endurecimiento futuro ensayado, no cambio improvisado.

## 6. Variables .env

Requeridas: `DEBUG`, `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `TIME_ZONE`, `MASISCAM_PUBLIC_BASE_URL`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `DATABASE_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `MASISCAM_RELEASE`, `HTTP_PORT`, `MASISCAM_MAX_UPLOAD_MB`, `GOOGLE_DRIVE_ENABLED`, `GOOGLE_DRIVE_CREDENTIALS_FILE`, `GOOGLE_DRIVE_ROOT_FOLDER_ID`, `GOOGLE_DRIVE_SHARED_DRIVE_ID`, `TRUST_PROXY`, `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_HSTS_SECONDS`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`. Detalle en README. setup_env genera secreto/password sin mostrarlos. `MASISCAM_INITIAL_PASSWORD` es transitoria/opcional; preferir prompt.

## 7. Docker final

Compose config PASS con env simulado:6 servicios,1build, imagen compartida, backend loopback, internos sin puertos, healthchecks y rotación. `docker build` NO verificado: daemon local inactivo, no fallo Dockerfile probado. Resultado `auditoria/compose-validation/result.json`. Confirmar UID10001/build en Linux con CONTABO E.

## 8. Drive

`verificar_drive` solo lee credencial/root/capabilities/SharedDrive. Worker DB0/backendDB1, prefetch1, timeouts y retries. Documentos reservan `drive_upload_id` antes de upload, consultan/crean/reutilizan el mismo ID y recuperan409. Test fuerza éxito remoto+fallo DB+retry:1create. Carpetas se buscan dentro del padre; IDs históricos no se cambian.

## 9. Seguridad

403/404 multiempresa, empresa activa, tokens no enumerables aleatorios, media no-store/nosniff/attachment, uploads controlados, secrets ignorados/read-only, errores Drive genéricos, privilege downgrade importer, TLS/cookies/proxy/headers, rate limits moderados, logs sin query string, QR filename seguro, Redis/PG/Gunicorn internos y sin Docker socket. Auditoría final lock: **0 avisos conocidos**. Pillow 12.3 se eligió por sus [correcciones oficiales](https://pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html).

## 10. Login

Card960px, fondo industrial sobrio, logo MASISCAM, “Bienvenido”, usuario/contraseña reales, botón ancho, errores accesibles, focus/autocomplete correctos, sin cuenta/recuperación/idioma falsos, footer legal. Capturas 375,390,768,1366 y1920 px en artifacts.

## 11. Paleta

Primary `#34577d`, dark `#16283d`, secondary `#788397`, accent `#9db2c9`, background `#f7f9fb`, surface `#fff`, text `#101820`, muted `#52606d`, border `#dce3ea`, danger `#a42c38`, success `#356249`; central `static/app/identity.css`, derivada de web/logo existente.

## 12. Tests

Suite final: **52 tests PASS**; Django check, control de migraciones y configuraci?n de producci?n simulada PASS; `collectstatic`: 156 archivos. Frontend PASS en cinco tama?os: web/login/dashboard/form/ficha/consulta/clientes/proyectos/documentos/historial/legal, sin errores JavaScript, HTTP ni desbordamiento. AST Python, sintaxis de scripts y Compose config PASS. Drive siempre simulado.

## 13. Docker config/build

Config PASS; build bloqueado por motor inactivo y no se declaró éxito. Comando real `docker compose build` en guía E. Dockerfile UID10001, Python3.12 slim y `--require-hashes`.

## 14. Desarrollo

README contiene venv3.12 + requirements + `tools/setup_env.py --development`, migrate, preparar_masiscam, runserver; y alternativa Docker db/redis → init → app. Nunca test settings en producción.

## 15. Contabo

`deploy/CONTABO.md` tiene comandos A–O, contexto servidor, guardas para marcadores y orden real: OS/SSH/firewall/Docker, clone/transfer, env, Drive, build,3 ramas DB/media, static/app, curl local, bootstrap Nginx, Certbot/DNS, validación, backup y rollback.

## 16. SSL

Certificado NUEVO, bootstrap HTTP/ACME+503 sin paths SSL, Certbot webroot después DNS, verificar SAN/fecha, render TLS, nginx-t/reload, hook y renew dry-run; www opcional. DNS-01 depende proveedor confirmado.

## 17. DNS

Cambio manual A/www y AAAA solo IPv6 probado; conservar MX/TXT/subdominios, registrar viejos/TTL, proteger hostname QR. dig/nslookup/curl exactos. Nada modificado.

## 18. Backup

`backup.sh`: PG-Fc/TOC, media tar, hashes de bytes archivados, checksums/marker/flock/rotación30d. Online DB/media no atómicos; final requiere writers stopped. Timer incluido; config/certs/Drive y réplica externa son respaldos separados.

## 19. Restore

`restore-check.sh`: checksums, PG16 aislado sin puertos/credencial productiva, restore single-transaction/no-owner/no-acl, conteos y extracción tar segura+hash. Conserva ensayo y no borra volúmenes. Restore producto solo DB vacía, guía F2.

## 20. Rollback

Congelar escritor y backup nuevo. Antes de writes: release/DNS anterior verificado. Después: no restaurar snapshot viejo automático; reconciliar DB/media/Drive y mantener un solo escritor. Conservar infraestructura anterior días; ninguna limpieza destructiva automática.
