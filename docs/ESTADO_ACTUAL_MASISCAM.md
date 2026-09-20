# Estado actual de MASISCAM

Fecha de auditoría: 2026-09-19
Ruta auditada: `c:\Users\carlo\OneDrive\Desktop\SOFTWARE\MASISCAM_WEB_COMPLETA\dist`
Producto auditado: `dist/sistema`
Release validada: `1.0.0-20260919`; inventario SHA-256: `sistema/RELEASE_MANIFEST.sha256` (136 archivos limpios).

## Dictamen

**LISTO PARA DESPLIEGUE** como MASISCAM independiente. **MASISCAM PUEDE FUNCIONAR SIN SYSCLOUD: SÍ.** La validación local Linux/Docker está completa; quedan datos reales del operador (dominio, DNS, certificados y credenciales) antes de ejecutar Contabo. La release validada es `1.0.0-20260919`. MASISCAM no incluye ERP, SRI, GPS, PWA SYSCloud ni APK SYSCloud; esos componentes quedan fuera de alcance.

## 1. Independencia de SYSCloud

La ejecución normal entra por `manage.py` y `config.settings`, y registra únicamente `accounts` y `masiscam` en `INSTALLED_APPS` (`sistema/config/settings.py:12-16`). Las URLs cargan `accounts` y `masiscam` (`sistema/config/urls.py:1-26`). Las migraciones reales dependen de `accounts`, `masiscam`, `auth`, `admin`, `contenttypes`, `sessions` y el usuario configurable; no dependen de `core`, `erp_project` ni una app CloudSYS (`sistema/accounts/migrations/0001_initial.py:8-10`, `sistema/masiscam/migrations/0001_initial.py:10-13`).

Búsqueda acotada al código fuente de `sistema/` excluyendo `.venv`, `staticfiles`, cachés y artefactos: no encontró referencias runtime a `SYSCloud`, `CloudSYS`, `erp_project`, dominios/IP antiguas ni imports `core.*`. Las únicas referencias funcionales heredadas son:

| Referencia | Clasificación | Evidencia |
|---|---|---|
| `importar_syscloud` | Herramienta de migración inicial | `sistema/accounts/management/commands/importar_syscloud.py:1-133`; no es llamado desde settings, URLs, middleware, señales ni tareas |
| `core.usuario`, `core.empresa`, `core.perfil` | Compatibilidad de formato de importación | Alias locales a `auth.user`, `accounts.empresa`, `accounts.perfil` en `importar_syscloud.py:58-60` |
| `SYSCloud` en pruebas de importación | Histórico/prueba de compatibilidad | `sistema/accounts/test_importacion.py:14-23`; verifica que el dato histórico pueda importarse, no que el runtime lo necesite |
| `SOURCE_MANIFEST.json` y documentación | Histórica/documental | Declaran adaptación/conservación de fuentes; no son módulos importables |
| `core.`/`erp_project.` fuera del código fuente | Código muerto o fixture/documentación | No aparecen en el camino de ejecución de MASISCAM; revisar cualquier archivo externo antes de empaquetar |

Conclusión: eliminar SYSCloud, su base de datos y sus servicios no impide arrancar Django, aplicar migraciones, autenticar usuarios, seleccionar empresa, administrar clientes/proyectos/equipos/registros/documentos ni servir consultas QR. La migración selectiva desde una exportación compatible sí requiere ejecutar una vez el comando, pero eso no convierte a SYSCloud en dependencia runtime.

## 2. `importar_syscloud.py`

Acepta un JSON de objetos Django con `model`, `pk` y `fields`; no lee una base SYSCloud ni llama una API externa (`:28-33`, `:54-60`). Acepta los modelos `auth.user`, `accounts.empresa`, `accounts.perfil`, `masiscam.rolmasiscam`, `proyecto`, `cliente`, `equipo`, `registroequipo`, `documento` y `auditoria` (`:12-17`).

- Conserva PK explícita y reinicia secuencias de los modelos escritos (`:39-49`).
- Importa usuarios; descarta campos desconocidos y desactiva `is_staff`/`is_superuser` salvo `--allow-superusers` (`:69-76`). Un usuario histórico con `activo=false` pasa a `is_active=false`.
- Importa empresas y perfiles, incluyendo empresa activa, membresía activa y relaciones usuario/empresa (`:77-82`).
- Importa roles, clientes, proyectos, equipos, registros, documentos y auditoría si están en la lista aprobada. Conserva campos como tokens QR, timestamps, rutas/IDs de Drive y claves de creación cuando existen en el formato destino.
- Valida PK positivas y no repetidas, campos existentes, valores, rutas de archivo, FK presentes y coherencia empresa/proyecto/cliente/equipo/documento/auditoría (`:84-132`).
- `--dry-run` es el comportamiento por defecto y no escribe. `--apply` exige una lista selectiva validada; rechaza PK ya existentes y usa `transaction.atomic()` (`:34-49`).
- Tiene buena idempotencia contra repetición sobre el mismo destino: no sobrescribe PK existentes. No es un proceso de merge ni reanudable por objeto.
- Riesgo residual: fallo del proceso, fallo de DB o restricción posterior puede requerir volver a ejecutar sobre un destino vacío; los consumos de secuencia no garantizan rollback lógico en todos los motores. Media se copia aparte y no es validada/copiada por el comando. No ejecutarlo sobre una base productiva ocupada.

Pruebas focalizadas ejecutadas: `accounts.test_import_security` y `accounts.test_importacion`: **6 PASS**.

## 3. Django, modelos y migraciones

- Entrada: `sistema/manage.py:1-13`; WSGI/ASGI: `sistema/config/wsgi.py:1-5`, `sistema/config/asgi.py:1-5`.
- Apps reales: Django estándar, `accounts`, `masiscam` (`settings.py:12-16`). Middleware incluye CSRF, auth, mensajes, X-Frame y empresa activa (`settings.py:18-25`).
- Seguridad configurada: DEBUG/SECRET_KEY/ALLOWED_HOSTS desde entorno; PostgreSQL obligatorio fuera de DEBUG; cookies seguras y redirect HTTPS por defecto en producción; HSTS configurable; `X_FRAME_OPTIONS=DENY`, nosniff, Referrer-Policy, límites de upload (`settings.py:8-10`, `26-75`).
- Timezone `America/Guayaquil`, `USE_TZ=True` (`settings.py:45-48`).
- Modelos reales: `Empresa`, `Perfil`, `RolMasiscam`, `Proyecto`, `Cliente`, `Equipo`, `RegistroEquipo`, `Documento`, `Auditoria`. La base vacía se construye con migraciones propias; el plan mostró solo apps estándar, `accounts` y `masiscam`.

Resultados ejecutados con variables de auditoría y SQLite temporal:

| Comando | Resultado |
|---|---|
| `python manage.py check` | PASS, 0 problemas |
| `python manage.py makemigrations --check --dry-run` | PASS, `No changes detected` |
| `python manage.py check --deploy` en imagen Linux/PostgreSQL | PASS con cuatro warnings esperados del entorno HTTP local |
| `python manage.py collectstatic --noinput` en imagen Linux | PASS, 156 archivos sin cambios |
| `tools/verify_frontend.py` | PASS en 375/390/768/1366/1920 px; sin errores JS ni respuestas HTTP >=400 |
| `migrate` + `migrate --check` en PostgreSQL 16 vacío | PASS; todas las migraciones aplican y no quedan pendientes |
| Smoke `django.setup()` + `/`, login y dashboard | PASS; HTTP 200/302/200 y 7/7/11 consultas en base vacía con un usuario |
| `showmigrations --plan` | PASS como inspección; sin migraciones CloudSYS |

## 4. Autenticación y multiempresa

`EmpresaActivaMiddleware` solo selecciona perfiles activos, empresa activa y rol MASISCAM activo (`sistema/accounts/middleware.py:8-28`). Las vistas privadas exigen autenticación, empresa activa, perfil y rol (`sistema/masiscam/access.py:17-53`). Las consultas privadas filtran por `request.empresa_activa`; proyectos, clientes, equipos, documentos, fotografías, registros y auditoría tienen relaciones de empresa verificadas en vistas/forms y pruebas de seguridad. Usuario inactivo queda fuera por el backend de autenticación; empresa/perfil/rol inactivos no obtienen acceso.

Pruebas relevantes: `masiscam.test_security` y `masiscam.test_drive_equipo` cubren IDOR, media privada, empresa inactiva, permisos, tokens, subida y Drive mockeado. En la imagen Linux final, con `GOOGLE_DRIVE_ENABLED=true` y mocks, la suite completa queda en **52 PASS / 0 FAIL** y la focalizada en **33 PASS / 0 FAIL**. Los cuatro fallos Drive iniciales fueron contaminación del entorno de terminal (`GOOGLE_DRIVE_ENABLED=false`).

### Matriz de los cinco fallos observados

| Test | Archivo | Causa comprobada | Clasificación | Test/código | Acción | Riesgo |
|---|---|---|---|---|---|---|
| `test_missing_db_fails_and_sqlite_rejected` | `masiscam/test_security.py:185-193` | El subprocess de producción carga PostgreSQL, pero el venv Windows no tiene `psycopg2`/`psycopg`; además el venv tiene Django 6.1.1/Celery 5.6.3 en lugar del lock | Ambiental/preexistente | Test correcto; código de producción correcto | Validar en imagen Linux del lock | Alto si se confunde con PASS |
| `test_fallo_cola_no_pierde_alta_y_registra_error` | `masiscam/test_drive_equipo.py:110-119` | `GOOGLE_DRIVE_ENABLED=false` persistía en el terminal y el encolador retorna antes del mock | Ambiental, no regresión | Test y código correctos | Ejecutar con entorno explícito | Bajo |
| `test_registro_cola_caida_y_equipo_sin_drive` | `masiscam/test_drive_equipo.py:244-251` | Drive desactivado antes de `apply_async` | Ambiental, no regresión | Test y código correctos | Ejecutar con `GOOGLE_DRIVE_ENABLED=true` | Bajo |
| `test_registro_fallo_drive_guarda_y_reintento_desde_ficha` | `masiscam/test_drive_equipo.py:220-241` | El callback no llegaba al mock por Drive desactivado | Ambiental, no regresión | Test y código correctos | Ejecutar con `GOOGLE_DRIVE_ENABLED=true` | Bajo |
| `test_document_retry_after_remote_success_db_failure_no_duplicate` | `masiscam/test_security.py:150-177` | En la ejecución inicial heredó Drive desactivado; con Drive explícito pasa | Ambiental, no regresión | Test y código correctos | Mantener mock y fijar entorno | Bajo |

Se corrigieron únicamente dos fallos comprobados de preproducción: `deploy/init.sh` tenía CRLF incompatible con BusyBox `sh`, y el importador no rellenaba timestamps `auto_now/auto_now_add` al deserializar objetos con `raw=True`. También se ajustó la lectura de staticfiles compartidos para Nginx (`chmod -R a+rX staticfiles`). No se modificaron tests para ocultar fallos.

## 5. Google Drive

Flujo previsto: Django guarda el objeto y encola Celery mediante señales/servicios; el worker ejecuta `masiscam.tasks`; `GoogleDriveService` crea carpetas/sube archivos y guarda IDs/estado en DB. Configuración y credencial se leen en `sistema/config/settings.py:85-94` y `sistema/masiscam/services.py:19-27`. La credencial debe estar fuera de Git/imagen, montada read-only en `/run/secrets`.

- Scope actual: `https://www.googleapis.com/auth/drive`, amplio; restringir la cuenta y la raíz por permisos de Drive.
- Raíz: `GOOGLE_DRIVE_ROOT_FOLDER_ID`; Shared Drive opcional.
- Reintentos: Celery autoretry con backoff/jitter y máximo 4 (`sistema/masiscam/tasks.py:7-12`); worker concurrency 2 y prefetch 1 (`docker-compose.yml:69-79`, `settings.py:96-104`).
- Idempotencia: carpetas se buscan dentro del padre; documentos reservan `drive_upload_id` antes de subir y reutilizan el mismo ID tras fallo DB (`services.py:73-111`, `tasks.py:57-91`). Hay test con éxito remoto + fallo DB + retry sin duplicado.
- Drive deshabilitado: `GOOGLE_DRIVE_ENABLED=false` evita encolado y API; MASISCAM puede operar sin Drive y sin SYSCloud.
- No se contactó Drive real. Las pruebas usan mocks. `verificar_drive` es diagnóstico read-only.
- Celery Beat no es necesario: no hay tareas periódicas ni servicio Beat declarado.

## 6. PostgreSQL, Redis y Celery

Compose declara PostgreSQL `16-alpine` con volumen `postgres`, healthcheck `pg_isready`, usuario/db parametrizados y sin `ports` (`docker-compose.yml:16-29`). Redis `7-alpine` usa AOF `everysec`, volumen `redis`, healthcheck y sin `ports` (`:30-44`); DB0 es broker y DB1 resultados. El usuario PostgreSQL inicial coincide con el rol de creación; separación de rol mínimo queda pendiente de endurecimiento.

Celery usa `redis://redis:6379/0` como broker y `/1` como resultados, worker `--concurrency=2`, sin Beat (`docker-compose.yml:69-79`). No hay exposición pública de DB, Redis ni Gunicorn; Nginx es el único servicio publicado y debe quedar en loopback.

## 7. Docker y Nginx

Servicios reales: `db`, `redis`, `init`, `web`, `worker`, `nginx` (`docker-compose.yml:15-113`). `init` aplica migraciones y collectstatic (`deploy/init.sh:1-4`). Dockerfile usa Python 3.12 slim, lock con hashes, UID 10001 y Gunicorn 3 workers (`Dockerfile:1-13`).

`docker compose config` real pasó usando `.env` temporal copiado desde `.env.test`, ambos ignorados por `.gitignore`; el `.env` temporal fue eliminado al finalizar la auditoría. `docker compose build` pasó con Python 3.12.14, lock/hash, `psycopg2-binary`, Gunicorn, Celery, Google API, Pillow y QR. La imagen usa UID/GID 10001 no root. Los servicios locales quedaron healthy: PostgreSQL 16, Redis 7, web, worker y Nginx.

Nginx Docker pasó `nginx -t` y `nginx -T`; smoke: home 200, login 200, static/logo 200 y `/media/` 404. El host propuesto (`deploy/nginx-host/masiscam.conf.example`) contempla 80->443, Certbot webroot, TLS 1.2/1.3, HTTP/2, gzip, rate limits para login/consulta, headers, `/media/` 404, proxy y timeouts 10/120/120; conserva placeholders de dominio/certificado y no fue instalado.

## 8. Variables de entorno

| VARIABLE | OBLIGATORIA | DEFAULT | USO | PRODUCCIÓN | RIESGO |
|---|---|---|---|---|---|
| `DEBUG` | Sí | false | modo Django/SQLite | false | ALTO si true |
| `SECRET_KEY` | Sí fuera de DEBUG | sin default | firmas/sesiones | privada, aleatoria | CRÍTICO si vacía/reutilizada |
| `ALLOWED_HOSTS` | Sí | localhost,127.0.0.1 | Host header | dominio exacto + health | ALTO si `*` |
| `CSRF_TRUSTED_ORIGINS` | Según dominio | vacío | CSRF HTTPS | orígenes exactos | ALTO si wildcard |
| `TIME_ZONE` | No | America/Guayaquil | Django/Celery | confirmar negocio | MEDIO |
| `MASISCAM_PUBLIC_BASE_URL` | Sí producción | localhost en debug | QR/enlaces públicos | HTTPS canónica | ALTO si placeholder |
| `POSTGRES_DB` | Compose | masiscam | DB init | confirmar backup | MEDIO |
| `POSTGRES_USER` | Compose | masiscam | DB init/URL | ideal separar rol app | MEDIO |
| `POSTGRES_PASSWORD` | Compose | ninguno | DB/URL | secreto privado | CRÍTICO si expuesta |
| `DATABASE_URL` | Sí producción | SQLite solo DEBUG | Django DB | PostgreSQL | CRÍTICO si SQLite |
| `CELERY_BROKER_URL` | No | redis localhost DB0 | broker | redis interno | ALTO si externo |
| `CELERY_RESULT_BACKEND` | No | redis localhost DB1 | resultados | redis interno | MEDIO |
| `MASISCAM_RELEASE` | No | local | tag de imagen | release validada | MEDIO |
| `HTTP_PORT` | No | 127.0.0.1:8080 | publicación nginx | loopback | ALTO si 0.0.0.0 |
| `MASISCAM_MAX_UPLOAD_MB` | No | 25 | límite Django | coordinar con Nginx 30m | MEDIO |
| `GOOGLE_DRIVE_ENABLED` | No | true en settings; false en ejemplo | activar Drive | false si no preparado | ALTO si true sin secretos/raíz |
| `GOOGLE_DRIVE_CREDENTIALS_FILE` | Si Drive | vacío | JSON de servicio | `/run/secrets/google-drive.json` | CRÍTICO si Git |
| `GOOGLE_DRIVE_ROOT_FOLDER_ID` | Si Drive | vacío | raíz Drive | ID autorizado | ALTO |
| `GOOGLE_DRIVE_SHARED_DRIVE_ID` | No | vacío | Shared Drive | solo si aplica | MEDIO |
| `TRUST_PROXY` | No | false | confiar X-Forwarded-Proto | true solo proxy controlado | ALTO si público |
| `SECURE_SSL_REDIRECT` | Producción | true fuera debug | HTTPS | true | ALTO si false |
| `SESSION_COOKIE_SECURE` | Producción | true fuera debug | cookie sesión | true | ALTO si false |
| `CSRF_COOKIE_SECURE` | Producción | true fuera debug | cookie CSRF | true | ALTO si false |
| `SECURE_HSTS_SECONDS` | Recomendable | 0 | HSTS | subir gradualmente | MEDIO |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | No | false | HSTS subdominios | revisar antes | MEDIO |
| `SECURE_HSTS_PRELOAD` | No | false | preload HSTS | no activar sin revisión | MEDIO |
| `POSTGRES_PASSWORD` en Compose | Sí | requerido | interpolación | secreto | CRÍTICO |

No se encontraron aliases runtime `REDIS_URL`, `PUBLIC_BASE_URL`, `MASISCAM_DOMAIN` ni `DJANGO_*`; no deben añadirse como sustitutos.

## 9. Frontend y media

La aplicación Django contiene web pública, login, legales, dashboard, clientes, proyectos, equipos, documentos, registros, consulta QR y etiquetas. QA local con Playwright pasó en cinco tamaños, incluyendo login, creación/edición, etiqueta QR, consulta pública, clientes, proyectos, historial y legales. Se usan Bootstrap y assets CSS/JS locales; no hay dependencia funcional de SYSCloud. La búsqueda de `.url`, `FieldFile` y `/media/` no encontró entregas privadas directas en templates/JS; las coincidencias son `URLField`, rutas de rechazo y `producto.url`. Media privada no debe exponerse como `/media/`; Nginx devuelve 404 y las vistas protegidas entregan archivos con control de sesión/empresa/token.

## 10. Contabo, backup y bloqueantes

La guía `sistema/deploy/CONTABO.md` documenta Ubuntu 24.04, Docker Engine/Compose, Nginx host, Certbot, UFW y destino `/srv/masiscam/sistema`; no se ejecutó ningún paso remoto. El backup local `backups/local-audit/masiscam.dump` pasó `pg_restore --list` y restore en `masiscam_restore`; conteos, FK, PK, tokens, IDs Drive y auditoría coincidieron. Media pasó restore y manifest SHA256. El importador pasó dry-run/apply en `masiscam_import` y rechazó la segunda ejecución por PK existente.

Pendientes operativos antes de ejecutar Contabo: confirmar dominio/IP/SSH/repo/commit, generar `.env` real 0600, entregar secretos autorizados, emitir TLS y configurar DNS. No son fallos de esta entrega y no se realizaron aquí. El backup externo, retención y permisos efectivos del VPS deben completarse en la instalación nueva.

Se usó `docker compose up` únicamente en el entorno local aislado `masiscam`, no hubo despliegue remoto. No se ejecutó `down` ni se borraron volúmenes para conservar la evidencia local. No hubo contacto con Drive real, modificación de datos reales ni push.
