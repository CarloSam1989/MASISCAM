# MASISCAM

Producto independiente para identificación de equipos, clientes, proyectos, documentos, fotografías, consulta pública por QR y trazabilidad. La web informativa, login y panel privado pertenecen a la misma aplicación Django. Las cuentas son creadas por administradores; no existe autorregistro ni recuperación de contraseña por correo.

**Fuera de alcance:** MASISCAM no incluye ERP, SRI, GPS, PWA SYSCloud ni APK SYSCloud. Esos componentes requieren su propio repositorio, datos y procedimiento de despliegue.

## Arquitectura

Internet → Nginx host HTTPS → 127.0.0.1:8080 → Nginx Docker → Gunicorn/Django. PostgreSQL 16 y Redis 7 solo en red Docker. Un worker Celery procesa Drive. No hay scheduler periódico de negocio. Las sesiones viven en PostgreSQL; Redis DB0 es broker y DB1 resultados. Drive es almacenamiento externo opcional.

Servicios: `db`, `redis`, `init`, `web`, `worker`, `nginx`. Una imagen `masiscam-app:${MASISCAM_RELEASE:-local}` se construye por `init` y es utilizada por los tres procesos de aplicación. `init` aplica migraciones y collectstatic una vez al iniciar dependencias; reiniciar el proceso web no aplica migraciones. No iniciar toda la app antes de una restauración.

Proyecto Compose `masiscam`: volúmenes `masiscam_postgres`, `masiscam_redis`, `masiscam_media`, `masiscam_staticfiles`, red `masiscam_default`. Un proyecto previo llamado `masiscam-mudanza` debe mantenerse explícitamente con `docker compose -p masiscam-mudanza` o trasladarse con backup/restore revisado; cambiar el nombre no traslada volúmenes. Nunca eliminar volúmenes para actualizar.

## Requisitos

Docker Engine + Compose plugin en Linux; objetivo documentado Ubuntu 24.04, Python 3.12. Para desarrollo nativo Python 3.12, SQLite solo con DEBUG; Drive requiere Redis y worker para procesar tareas. Dependencias directas en requirements.txt, resolución Linux/Python 3.12 con versiones/hashes en requirements.lock. Imágenes base usan tags de versión: fijar digests verificados en release si se necesita reproducibilidad del sistema operativo completo.

## Desarrollo

Desde `sistema/`, Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe tools/setup_env.py --development
```

Para desarrollo SIN Docker: editar `.env`, eliminar DATABASE_URL para SQLite, cambiar MASISCAM_PUBLIC_BASE_URL a `http://localhost:8000`, broker/backend a localhost solo si se usa Redis local. Drive viene deshabilitado en ejemplo de desarrollo.

```powershell
.venv/Scripts/python.exe manage.py migrate
.venv/Scripts/python.exe manage.py preparar_masiscam --empresa "Mi empresa" --username "mi-administrador"
.venv/Scripts/python.exe manage.py runserver
```

El comando preparar_masiscam pide contraseña; no hay usuario/password inicial hardcodeado. preparar_masiscam crea un superusuario con Empresa, Perfil y RolMasiscam ADMIN. No ejecutar además createsuperuser por defecto. La alternativa createsuperuser permite crear el administrador técnico de forma interactiva y luego configurar organización/membresía en /admin/. Usuarios normales reciben Perfil y RolMasiscam; un superusuario también necesita una empresa activa seleccionada para trabajar.

Desarrollo Docker:

```bash
python3 tools/setup_env.py --development
docker compose config --quiet
docker compose build
docker compose up -d db redis
docker compose run --rm --no-deps init
docker compose up -d web worker nginx
```

La credencial real nunca se necesita para pruebas. No ejecutar los scripts de publicación como parte de tests.

## Producción

Seguir [deploy/CONTABO.md](deploy/CONTABO.md) de A a O y [DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md). Ruta destino `/srv/masiscam/sistema`; repositorio/branch/commit/dominio/IP son datos del operador, no están definidos en esta copia. El repositorio debe tener `sistema/` en su raíz para los comandos de clonación documentados; si se publica únicamente esta carpeta, usarla directamente en dicha ruta. Esta copia no contiene `.git`, por lo que no se creó ni se hizo push a un repositorio remoto.

Crear entorno privado sin mostrar secretos:

```bash
python3 tools/setup_env.py --domain DOMINIO_REAL --release RELEASE_VALIDADA
# Añadir --www HOST_WWW_REAL solo si existe.
chmod 600 .env
```

El script genera claves aleatorias al ser ejecutado por el operador, no sobrescribe `.env` y no imprime valores. Configure Drive después; para instalación sin Drive establecer GOOGLE_DRIVE_ENABLED=false. No usar placeholders en un entorno activo. No compartir `.env`, hashes de contraseñas, logs, backups o credenciales.

## Variables de entorno

Nombres canónicos actuales, sin aliases duplicados:

| Variable | Uso |
|---|---|
| DEBUG | false en producción; habilita SQLite únicamente en desarrollo |
| SECRET_KEY | Obligatoria y privada en producción; cambio invalida sesiones/firmas |
| ALLOWED_HOSTS | Hostnames exactos, sin esquema; incluir localhost,127.0.0.1 para health interno |
| CSRF_TRUSTED_ORIGINS | Orígenes HTTPS exactos del dominio y www si existe |
| TIME_ZONE | America/Guayaquil; USE_TZ=true |
| MASISCAM_PUBLIC_BASE_URL | URL HTTPS canónica usada para QR; no cambiar tokens durante traslado |
| POSTGRES_DB / POSTGRES_USER | masiscam en nueva instalación; confirmar origen al restaurar |
| POSTGRES_PASSWORD | Privada, generada hex para interpolación segura URL |
| DATABASE_URL | PostgreSQL obligatorio fuera de DEBUG; Compose sustituye con db y variables PG |
| CELERY_BROKER_URL / CELERY_RESULT_BACKEND | redis://redis:6379/0 y /1 en Compose |
| MASISCAM_RELEASE | Tag de la única imagen app; usar release/commit validado |
| HTTP_PORT | 127.0.0.1:8080; no publicar backend globalmente |
| MASISCAM_MAX_UPLOAD_MB | 25 por defecto, Nginx permite 30m multipart |
| GOOGLE_DRIVE_ENABLED | false deshabilita encolado/tareas; true requiere JSON/raíz |
| GOOGLE_DRIVE_CREDENTIALS_FILE | /run/secrets/google-drive.json en Docker |
| GOOGLE_DRIVE_ROOT_FOLDER_ID | Carpeta existente compartida con cuenta de servicio |
| GOOGLE_DRIVE_SHARED_DRIVE_ID | Unidad compartida, si aplica |
| TRUST_PROXY | true solo detrás del proxy host confiable + backend loopback |
| SECURE_SSL_REDIRECT / SESSION_COOKIE_SECURE / CSRF_COOKIE_SECURE | true en producción |
| SECURE_HSTS_SECONDS | 300 inicial; ampliar después de verificar HTTPS estable |
| SECURE_HSTS_INCLUDE_SUBDOMAINS / SECURE_HSTS_PRELOAD | false inicial; activar solo tras revisar todos los subdominios y requisitos |

`REDIS_URL`, `PUBLIC_BASE_URL`, `MASISCAM_DOMAIN` y aliases `DJANGO_*` no son necesarios: usar los nombres de la tabla. La release validada localmente es `1.0.0-20260919`; sustituirla solo por otra release cuyo hash haya sido verificado. Ejemplos separados .env.example y .env.production.example. Cookies HttpOnly/SameSite según Django, nosniff, X-Frame DENY y Referrer-Policy same-origin.

## Google Drive

Crear cuenta de servicio, habilitar API Drive y compartir raíz con ella. Las cuentas de servicio no tienen cuota de almacenamiento personal: usar una unidad compartida con permisos apropiados o una configuración de Workspace autorizada. No ampliar permisos globales para resolver un error. La raíz debe permitir añadir archivos. El scope actual permite trabajar con Drive accesible a esa cuenta; restringir por permisos de carpetas/unidad.

JSON: `secrets/google-drive.json`, fuera de Git/imagen, montaje read-only `/run/secrets`. Host Linux directorio y archivo owner UID/GID 10001, directorio 0700, archivo 0400. No incluir JSON en `.env`. Usuario app UID 10001 necesita atravesar el directorio montado.

```bash
docker compose run --rm --no-deps web python manage.py verificar_drive
```

Solo lee credencial y metadatos/permisos de raíz. No crea/mueve/borra archivos ni muestra IDs/claves. Se ejecuta únicamente por operador cuando entregue su credencial real; las pruebas usan mocks.

Carpetas existentes/IDs se preservan. Nuevas estructuras reutilizan nombres dentro del padre; proyectos y operaciones se serializan en DB. Documentos/fotografías cargadas como Documento se suben conservando archivo local. Fotografías de fichas se mantienen en media y NO se sincronizan automáticamente como nueva función.

**Idempotencia documentos:** migración 0008 añade drive_upload_id opcional. Se reserva un ID con Drive y se guarda en una transacción antes de subir. Reintentos consultan el mismo ID, cargan con él o recuperan 409. Si éxito remoto y save final falla, la reserva sigue en DB y se recupera ese archivo; no se vuelve a crear otro. No se convierten archivos a formatos Workspace. IDs existentes no se reemplazan; cambios manuales/archivos borrados en Drive necesitan revisión. Restaurar DB anterior a la reserva puede perder ese vínculo: conservar DB/backups coordinados y no purgar remote.

Pillow se actualizó a 12.3.0 por avisos de seguridad del decoder; [notas oficiales](https://pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html).

Referencia del mecanismo: [Drive IDs pregenerados](https://developers.google.com/workspace/drive/api/guides/manage-uploads#use_a_pre-generated_id_to_upload_files). Las carpetas reutilizadas por nombre no son una garantía global frente a escritores externos; gestionar una sola app escritora y revisar carpetas con nombres iguales antes de importar.

## Base de datos y administrador

Nueva instalación: migrate, preparar_masiscam para organización/administrador de producto, El preparador también concede Django admin. createsuperuser es una alternativa, no un segundo administrador automático. No ejecutar preparador antes de una importación histórica que traiga PK. No crear otro administrador automáticamente si los datos importados ya incluyen uno autorizado.

```bash
# Estrategia recomendada: organización y único administrador inicial
docker compose exec web python manage.py preparar_masiscam --empresa "EMPRESA_REAL" --username "USUARIO_REAL"
```

Compose inicializa rol/base masiscam con imagen PostgreSQL. Ese rol inicial es superusuario; separar rol administrador y aplicación es endurecimiento pendiente que debe hacerse con grants/schema/migraciones probados, no mediante cambio improvisado de password en volumen existente. Cambiar POSTGRES_PASSWORD en .env NO cambia password de una DB ya inicializada. MASISCAM_INITIAL_PASSWORD es una variable opcional del preparador para automatización; no guardarla en Git ni imprimirla, preferir getpass interactivo.

DB independiente compatible: pg_dump -Fc del origen, pg_restore --no-owner --no-acl --exit-on-error --single-transaction en destino vacío ANTES de init/web/worker. Verificar tablas, conteos, secuencias/owners/extensiones y archivos. Nunca ejecutar restore encima de tablas existentes ni importar el JSON dos veces. Procedimiento exacto en CONTABO.

## Migraciones, static y media

```bash
docker compose run --rm --no-deps init python manage.py migrate --plan
docker compose run --rm --no-deps init python manage.py migrate --noinput
docker compose run --rm --no-deps init python manage.py collectstatic --noinput
```

No ejecutar makemigrations en producción. Migrations versionadas, STATIC_ROOT=/app/staticfiles y MEDIA_ROOT=/app/media; volúmenes independientes. Media no se sirve directamente: `/media/` devuelve 404, rutas privadas comprueban login/empresa/rol/proyecto; públicas comprueban token/estado/visibilidad/empresa activa. Descargas privadas no-store, path traversal/symlinks a fuera rechazados, documentos attachment con nombre seguro. No abrir alias media para arreglar imágenes. Fotografías nuevas: JPEG/PNG/WebP, extensión acorde, límite de bytes configurado y 25 megapíxeles; no cambia archivos históricos. Documentos comprueban MIME/extensión, magic en formatos soportados y estructura básica Office; no sustituyen antivirus ni validación exhaustiva CAD/video.

## Celery y Redis

Solo worker, concurrency=2, prefetch=1, timeout blando 270s/duro 300s, retry backoff/jitter hasta 4. Acknowledgement predeterminado temprano conservado; no activar acks_late sin revisar recuperación/operaciones externas. Si worker muere, revisar estados y reintentar por flujo de producto; no prometer ejecución exactamente una vez. Broker0/backend1, Redis AOF everysec y volumen /data; ninguna sesión Django depende de Redis. Redis no tiene puerto público ni ACL en esta configuración; mantener red/host restringidos y evaluar ACL antes de compartir Docker con otros productos.

```bash
docker compose exec worker celery -A config inspect ping
docker compose exec worker celery -A config inspect active
docker compose exec worker celery -A config inspect reserved
docker compose exec worker celery -A config inspect scheduled
```

## Nginx y SSL

Backend Nginx preserva forwarded headers solo porque host reemplaza entradas arbitrarias y backend es loopback. Conservar el par de configs. Host HTTPS añade límites moderados a login/consulta y no al enlace QR directo; `/health/` host no se publica. Logs omiten query strings (rutas pueden seguir incluir tokens QR: proteger acceso/retención). Gzip textos, static 7d sin immutable; no proxy cache de contenido protegido. HTTP/2 TLS1.2/1.3, www opcional; HTTP/3 no implementado. Certificado nuevo por Certbot webroot tras DNS; bootstrap HTTP ofrece ACME y 503 antes de SSL, nunca cargar rutas inexistentes.

## Backups y restauración de ensayo

```bash
bash deploy/scripts/backup.sh
# Snapshot final: mantenimiento/drain externos y detener web/worker antes:
bash deploy/scripts/backup.sh --writers-stopped
bash deploy/scripts/restore-check.sh /srv/masiscam/backups/backup-FECHA_UTC
```

Script utiliza credenciales del contenedor desde .env vía Compose; sin passwords en argumentos/logs. PG -Fc, media, manifest SHA-256 y metadata; flock evita simultáneos. Rotación 30 días por defecto solo directorios con marker dentro del root dedicado. BACKUP_ROOT/BACKUP_RETENTION_DAYS son opciones del script, no settings Django. Online DB/media no son atómicos: final requiere escritores detenidos y productores externos controlados. No es backup de .env/secrets/certificados ni Drive; preservar configuración por canal cifrado separado. Sin Redis como sustituto de DB.

restore-check crea solo PostgreSQL aislado sin puertos, con contraseña generada privada, restaura sin --clean y coteja hashes de media. No inicia app/worker ni contacta Drive; conserva proyecto/volumen para revisión y requiere limpieza manual específica posterior. También probar smoke de aplicación sobre DB/media aisladas para certificar recuperación funcional. Timer diario propuesto en deploy/systemd; instalarlo por operador y replicar backups cifrados FUERA del VPS con alertas. No hay repositorio externo configurado automáticamente.

## Actualizaciones, logs, problemas y rollback

Antes: backup/restore ensayado, release anterior y migraciones revisadas; build con tag nuevo, migrate controlado, static, reemplazo web/worker/nginx y smoke. No rebuild del tag anterior ni borrar volúmenes. Cambios de esquema pueden impedir rollback de código: snapshot o recuperación hacia delante. No restaurar snapshot viejo si ya hay operaciones nuevas sin congelar/reconciliar datos/Drive.

```bash
docker compose ps -a
docker compose logs --since 30m --tail 100 web worker db redis nginx init
docker stats --no-stream
docker compose exec nginx nginx -t
```

HTTPS redirect loop: comprobar TRUST_PROXY + header reemplazado en host y conservado backend. 400 Host: ALLOWED_HOSTS exactos incl health interno. 403 CSRF: origins HTTPS y cookies, no CORS wildcard. Media 404: vista segura/permiso/archivo/montaje, nunca abrir /media. Drive: verificar_drive y permisos Shared Drive; errores UI genéricos, diagnóstico privado sin loguear exception con credenciales. init permisos: volúmenes owner10001 y estado migrations. Docker running sin health: revisar DB/worker/readiness y recursos. Backups parciales se conservan y NO rotan como completos.

## Pruebas

```bash
python manage.py check --settings=config.test_settings
python manage.py makemigrations --check --dry-run --settings=config.test_settings
python manage.py test --settings=config.test_settings
python tools/check_production.py
```

Base SQLite temporal, broker memory y mocks Drive; config.test_settings exclusivamente QA, nunca producción. check_production simula settings sin consultar DB/Drive y no imprime credenciales; warnings HSTS subdominios/preload son deliberados en configuración inicial. QA responsive opcional: instalar playwright y Chromium o Edge, `python tools/verify_frontend.py`, capturas/results en artifacts excluidos de build/Git. Resultados y limitaciones reales en ENTREGA.md; no confundir con VALIDACION.md histórica.

## Migración histórica desde SYSCloud

Única compatibilidad histórica: importer `importar_syscloud` transforma core.usuario/empresa/perfil a auth.user/accounts y admite solo modelos MASISCAM listados. No importa otros sistemas. La exportación fuente debe ser selectiva y contener closure FK completo, revisada por administrador; un dumpdata general de todas las identidades puede incluir datos ajenos. No hay exportador fuente en este proyecto porque no controla instalación anterior; preparar filtro/autorización allí por separado.

```bash
docker compose run --rm --no-deps -v /srv/masiscam/backups:/backup:ro init python manage.py importar_syscloud /backup/masiscam-export.json --dry-run --empresa-id ID_APROBADA
docker compose run --rm --no-deps -v /srv/masiscam/backups:/backup:ro init python manage.py importar_syscloud /backup/masiscam-export.json --apply --empresa-id ID_APROBADA
```

Dry-run hace validación/lecturas sin INSERT/UPDATE/secuencias, no toca origen. Reporta conteos por modelo y número de flags elevados sin datos personales. Rechaza modelos ajenos, PK/fields/FK/rutas inválidas y empresa cruzada. Flags staff/superuser se desactivan por defecto; --allow-superusers solo tras revisión explícita. Conserva roles locales, PK/hashes de contraseña/tokens/Drive IDs/timestamps y relaciones; apply atómico reajusta secuencias y no sobrescribe PK. Validación previa no sustituye restricciones DB: dry-run puede no detectar colisiones de uniques diferentes a PK hasta aplicar; apply revierte datos en caso de error.

No restaurar base completa de SYSCloud aquí. Alias públicos `/masiscam/equipo/<token>/` y `/masiscam/publico/<token>/` conservados para QR antiguos; su hostname físico también debe mantenerse/redirigirse. SOURCE_MANIFEST registra procedencia/hashes históricos, no los hashes actuales del producto adaptado. No hay dependencia runtime/import de core ni carpeta hermana.
