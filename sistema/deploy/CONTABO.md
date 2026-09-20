# Publicación MASISCAM — Ubuntu 24.04 en Contabo

Documento para el operador. **Ninguno de los siguientes pasos fue ejecutado en servidor remoto.** El producto es MASISCAM independiente. No modificar otra instalación ni importar bases de otros productos. Datos dominio/IP/Git/rama/release/puerto SSH deben confirmarse; no se conocen aquí. No usar contraseñas en comandos o URLs Git. Todos los comandos Linux asumen bash.

Orden: A → B → C → E → F → G → H → I → J (bootstrap) → L (DNS manual) → K (Certbot) → M (HTTPS) → N (backup) → O (rollback si hace falta). Drive permanece desactivado inicialmente y se habilita solo después de validar la aplicación normal. Certbot webroot requiere DNS resuelto; no emitir antes de L. No ejecutar ningún paso hasta sustituir sus placeholders.

Resultado esperado por paso: no continuar si comando falla. Mantener consola Contabo abierta al configurar SSH/firewall. La guía prepara una instalación nueva en un solo VPS, web pública y panel en Django. MASISCAM no incluye ERP, SRI, GPS, PWA SYSCloud ni APK SYSCloud.

## A. NUEVO CONTABO

**Ejecutar en VPS NUEVO, consola/root inicial.**

```bash
cat /etc/os-release
# Debe decir Ubuntu 24.04. Si es Debian, NO usar repositorio Docker Ubuntu.
sudo apt-get update
sudo apt-get upgrade -y
sudo apt-get install -y ca-certificates curl git rsync openssl python3 nginx certbot ufw fail2ban dnsutils sysstat
sudo timedatectl set-timezone America/Guayaquil
timedatectl status
if test -f /var/run/reboot-required; then echo 'Reiniciar SOLO VPS NUEVO desde consola antes de seguir'; fi
sudo adduser masiscam-admin
sudo usermod -aG sudo masiscam-admin
sudo install -d -m 700 -o masiscam-admin -g masiscam-admin /home/masiscam-admin/.ssh
sudoedit /home/masiscam-admin/.ssh/authorized_keys
# Pegar solo clave PÚBLICA propia; nunca clave privada.
sudo chown masiscam-admin:masiscam-admin /home/masiscam-admin/.ssh/authorized_keys
sudo chmod 600 /home/masiscam-admin/.ssh/authorized_keys
```

**PC LOCAL:** probar nueva conexión SSH como masiscam-admin, con puerto REAL, y `sudo -v`. No cerrar consola/sesión inicial hasta confirmar.

**VPS NUEVO, tras acceso por clave confirmado:**

```bash
sudo tee /etc/ssh/sshd_config.d/00-masiscam.conf >/dev/null <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
EOF
sudo sshd -t
sudo systemctl reload ssh
sudo sshd -T | grep -E '^(port|permitrootlogin|passwordauthentication|pubkeyauthentication) '
```

Si valores efectivos difieren, revisar Include/Match/cloud-init antes de cerrar acceso. Si falla acceso, revertir drop-in desde consola. Puerto SSH no se cambia ni se supone 22.

**VPS NUEVO — Docker oficial:**

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release; printf '%s' "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker masiscam-admin
sudo docker version
sudo docker compose version
```

Grupo docker equivale a acceso administrativo. Reconectar sesión para grupo; no dar acceso a usuarios del producto. [Fuente Docker](https://docs.docker.com/engine/install/ubuntu/).

**VPS NUEVO — firewall:**

```bash
export SSH_PORT='PENDIENTE_DE_CONFIRMAR'
test "$SSH_PORT" != PENDIENTE_DE_CONFIRMAR || { echo 'Confirmar puerto SSH real'; exit 1; }
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow "$SSH_PORT/tcp"
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
sudo tee /etc/fail2ban/jail.d/masiscam-sshd.local >/dev/null <<EOF
[sshd]
enabled = true
port = $SSH_PORT
backend = systemd
EOF
sudo fail2ban-client -t
sudo systemctl enable --now fail2ban
sudo fail2ban-client status sshd
sudo install -d -m 750 -o masiscam-admin -g masiscam-admin /srv/masiscam
sudo install -d -m 700 -o masiscam-admin -g masiscam-admin /srv/masiscam/backups
```

No abrir 5432/6379/8000/8080 ni UDP443. Docker puede eludir reglas UFW para puertos publicados: comprobar loopback del backend además del firewall. Si SSH deja de funcionar, consola para corregir regla real.

## B. SUBIR/CLONAR MASISCAM

**VPS NUEVO como masiscam-admin — datos por confirmar:**

```bash
export REPO_URL='PENDIENTE_DE_CONFIRMAR'
export PROD_BRANCH='PENDIENTE_DE_CONFIRMAR'
export PROD_COMMIT='PENDIENTE_DE_CONFIRMAR'
export PROD_RELEASE='1.0.0-20260919'
export DOMAIN='PENDIENTE_DE_CONFIRMAR'
export WWW='' # nombre www REAL si existe; vacío si no se usa
export NEW_IP='PENDIENTE_DE_CONFIRMAR'
export CERT_EMAIL='PENDIENTE_DE_CONFIRMAR'
for key in REPO_URL PROD_BRANCH PROD_COMMIT DOMAIN NEW_IP CERT_EMAIL; do
  test "${!key}" != PENDIENTE_DE_CONFIRMAR || { echo "Confirmar $key"; exit 1; }
done
export CERT_NAME="$DOMAIN" # nombre elegido para certificado NUEVO
export MASISCAM_RELEASE="$PROD_RELEASE"
git clone --branch "$PROD_BRANCH" --single-branch "$REPO_URL" /srv/masiscam/fuentes
test "$(git -C /srv/masiscam/fuentes rev-parse HEAD)" = "$PROD_COMMIT" || { echo 'Release distinta: detener'; exit 1; }
test -f /srv/masiscam/fuentes/sistema/docker-compose.yml || { echo 'Confirmar estructura repo'; exit 1; }
test ! -e /srv/masiscam/sistema || { echo 'Destino ocupado: no sobrescribir'; exit 1; }
cp -a /srv/masiscam/fuentes/sistema /srv/masiscam/sistema
cd /srv/masiscam/sistema
```

La copia no tiene URL/branch reales ni .git; publicar/importar fuentes a Git por el operador antes de clone (no se hizo push). Si repo contiene únicamente sistema, clone directamente a `/srv/masiscam/sistema` y omitir copia. Verificar commit/layout; no asumir main/master.

La release local validada es `1.0.0-20260919`. Antes de construir, comparar `RELEASE_MANIFEST.sha256` con la copia recibida y detenerse si falta o sobra un archivo de release. No incluir `.env`, `secrets/`, `backups/`, `media/`, `staticfiles/`, `.venv`, cachés ni artefactos de auditoría.

**Alternativa PC LOCAL PowerShell, sin Git — empaquetar fuentes limpias:**

```powershell
Set-Location -LiteralPath 'C:\Users\carlo\OneDrive\Desktop\SOFTWARE\MASISCAM_WEB_COMPLETA\dist'
$targetHost='PENDIENTE_DE_CONFIRMAR'
$targetPort='PENDIENTE_DE_CONFIRMAR'
if ($targetHost -eq 'PENDIENTE_DE_CONFIRMAR' -or $targetPort -eq 'PENDIENTE_DE_CONFIRMAR') { throw 'Confirmar IP/puerto SSH nuevo' }
tar -czf auditoria/masiscam-producto.tar.gz --exclude=.env --exclude=.env.simulated --exclude=.venv --exclude=.venv312 --exclude=.python-runtime --exclude=media --exclude=staticfiles --exclude=backups --exclude=artifacts --exclude=__pycache__ --exclude=secrets/*.json --exclude=*.sqlite3 sistema
if ($LASTEXITCODE -ne 0) { throw 'Falló paquete' }
Get-FileHash -Algorithm SHA256 auditoria/masiscam-producto.tar.gz
scp -P $targetPort auditoria/masiscam-producto.tar.gz "masiscam-admin@${targetHost}:/srv/masiscam/"
```

**VPS NUEVO alternativa transferencia:** comparar SHA256, `tar -tzf` a archivo privado y revisar solo sistema/ sin rutas absolutas/../, luego `tar -xzf /srv/masiscam/masiscam-producto.tar.gz -C /srv/masiscam`. Destino vacío. Revisión de secretos previa obligatoria si se añadieron archivos nuevos. No empaquetar auditoria ni web demo archivada como producto.

## C. CONFIGURAR .ENV

**VPS NUEVO, `/srv/masiscam/sistema`:**

```bash
cd /srv/masiscam/sistema
python3 tools/setup_env.py --domain "$DOMAIN" --www "$WWW" --release "$MASISCAM_RELEASE"
chmod 600 .env
nano .env
```

Script genera valores privados sin imprimirlos, falla si .env existe. Nombres exactos en README. No usar placeholders activos. Entorno producción TLS/cookies/DB obligatorios; ALLOWED_HOSTS incluye hostname+localhost/127.0.0.1 health. No añadir variables redundantes. `.env` no se versiona. No configurar SQLite en producción. Mantener `GOOGLE_DRIVE_ENABLED=false` durante el deploy inicial y el smoke test normal.

**Estructura y permisos esperados:** Compose monta `./secrets`, por lo que la credencial privada se ubica en `/srv/masiscam/sistema/secrets/`. Los backups operativos se guardan en `/srv/masiscam/backups/` y el código en `/srv/masiscam/sistema/`.

```bash
sudo install -d -m 750 -o masiscam-admin -g masiscam-admin /srv/masiscam/sistema
sudo install -d -m 700 -o masiscam-admin -g masiscam-admin /srv/masiscam/backups
sudo install -d -m 700 -o 10001 -g 10001 /srv/masiscam/sistema/secrets
sudo chmod 600 /srv/masiscam/sistema/.env
```

El archivo `google-drive.json`, si se habilita, debe ser propiedad de UID/GID 10001 y tener modo 0400. No copiar secretos al informe, al repositorio ni a la imagen.

**Decisión obligatoria de datos antes de F:** elegir exactamente una estrategia:

- **A.** Restaurar una base MASISCAM ya compatible en DB vacía con `pg_restore --no-owner --no-acl` y validar conteos, PK, FK, secuencias, tokens, IDs Drive, auditoría y media.
- **B.** Crear DB vacía, ejecutar migraciones, ejecutar `importar_syscloud.py --dry-run`, revisar conteos y empresas aprobadas, ejecutar `--apply` una sola vez y copiar media separadamente con manifest SHA256.

Nunca restaurar un dump completo de ERP/SYSCloud sobre MASISCAM ni combinar A y B.

## D. GOOGLE DRIVE

**PC LOCAL → VPS NUEVO:** entregar `google-drive.json` por scp con puerto real a ubicación privada de staging. Nunca usar credenciales antiguas sin autorización ni pegarlas en terminal/chat. Archivo no se crea aquí. Necesita unidad compartida/cuenta con acceso permitido para cargas; revisar cuotas.

**VPS NUEVO:**

```bash
cd /srv/masiscam/sistema
sudo install -d -m 700 -o 10001 -g 10001 secrets
# Sustituir ruta real de archivo transferido, no crear JSON ficticio.
export DRIVE_JSON_SOURCE='PENDIENTE_DE_CONFIRMAR'
test "$DRIVE_JSON_SOURCE" != PENDIENTE_DE_CONFIRMAR && test -f "$DRIVE_JSON_SOURCE" || exit 1
sudo install -m 400 -o 10001 -g 10001 "$DRIVE_JSON_SOURCE" secrets/google-drive.json
nano .env
# GOOGLE_DRIVE_ENABLED=true
# GOOGLE_DRIVE_CREDENTIALS_FILE=/run/secrets/google-drive.json
# GOOGLE_DRIVE_ROOT_FOLDER_ID=<ID_REAL>
# GOOGLE_DRIVE_SHARED_DRIVE_ID=<ID_REAL si aplica>
# EJECUTAR diagnóstico SOLO DESPUÉS de build y DB/static (E/F/G):
docker compose run --rm --no-deps web python manage.py verificar_drive
```

Esperado diagnóstico OK sin writes en Drive. Si falla, revisar root, API habilitada, Shared Drive/canAddChildren y lectura UID10001; no chmod777 ni imprimir JSON. Cambios .env necesitan recreación de web/worker después. No habilitar worker sobre raíz real en ensayos que podrían crear objetos no deseados. Fotografías de fichas quedan en media; Documento admite foto/PDF/etc para sincronización.

La instalación inicial conserva `GOOGLE_DRIVE_ENABLED=false`. Habilitar Drive solo después del smoke test normal, con credencial nueva o expresamente autorizada, permisos mínimos, raíz confirmada y diagnóstico read-only.

## E. DOCKER

**VPS NUEVO:**

```bash
cd /srv/masiscam/sistema
docker compose config --quiet
docker compose config --services
docker compose build
docker image inspect "masiscam-app:$MASISCAM_RELEASE" --format '{{.Id}} {{.Size}}'
docker compose run --rm --no-deps --entrypoint id web
docker compose up -d --wait db redis
docker compose ps
docker compose exec -T db sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
docker compose exec -T redis redis-cli PING
```

Esperado uid10001, PG saludable y PONG; un único build init. No mostrar config completo: contiene credenciales. Si falla build, revisar conectividad/hashes/deps/layers; no retirar --require-hashes para improvisar. Si falla DB con volumen existente, confirmar password real: variables initdb no cambian cuentas existentes. No compose down ni eliminar volumen.

## F. BASE DE DATOS

**VPS NUEVO — elegir UNA rama, no combinarlas:**

**F1 nueva sin datos:**

```bash
docker compose run --rm --no-deps init python manage.py migrate --plan
docker compose run --rm --no-deps init python manage.py migrate --noinput
```

**F2 dump de DB MASISCAM ya independiente, transferido a backups/masiscam.dump:**

```bash
cd /srv/masiscam/sistema
test -s /srv/masiscam/backups/masiscam.dump || exit 1
table_count=$(docker compose exec -T db sh -c 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -v ON_ERROR_STOP=1' <<'SQL'
SELECT count(*) FROM pg_tables WHERE schemaname='public';
SQL
) || exit 1
test "$table_count" = 0 || { echo 'DB ocupada: NO restaurar encima'; exit 1; }
docker compose exec -T db pg_restore --list < /srv/masiscam/backups/masiscam.dump > /srv/masiscam/backups/restore-toc.txt
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-acl --exit-on-error --single-transaction' < /srv/masiscam/backups/masiscam.dump
docker compose run --rm --no-deps init python manage.py migrate --plan
docker compose run --rm --no-deps init python manage.py migrate --noinput
```

Usuario/base nuevos creados por PostgreSQL con .env; owners pasan al usuario destino, ACL viejas no se arrastran. Verificar pg_extension/pg_sequences/MAX(id) y owners/grants. No se requieren extensiones extra por código; el dump real debe inventariarse. No --clean/--create/DROP. Migración 0008 es aditiva opcional para documento Drive.

**F3 exportación histórica selectiva:** seguir sección histórica README: primero migrate, luego importer --dry-run con empresas aprobadas, informe, luego --apply. No base completa de otro sistema ni administrador precreado. Exportación/filtros/IDs reales quedan por confirmar con operador. Dry-run no escribe; importer no altera origen.

**VPS NUEVO — media si se restaura/importa:**

```bash
cd /srv/masiscam/sistema
# Archivo propio verificado por SHA256SUMS; revisar lista sin ../, absolutos ni symlinks.
tar -tzf /srv/masiscam/backups/media.tar.gz > /srv/masiscam/backups/media-list.txt
docker compose run --rm --no-deps --user 0 --entrypoint sh -v /srv/masiscam/backups:/backup:ro init -c 'tar -xzf /backup/media.tar.gz -C /app/media && chown -R 10001:10001 /app/media && find /app/media -type d -exec chmod 750 {} \; && find /app/media -type f -exec chmod 640 {} \;'
docker compose run --rm --no-deps --entrypoint python -v /srv/masiscam/backups:/backup:ro init tools/media_manifest.py /backup/media-sha256.json
```

Si no hay backup media omitirá extracción. Si falta archivo referenciado, no declarar restore completo. Comando aplica ownership únicamente /app/media en volumen destino comprobado.

## G. STATIC

**VPS NUEVO:**

```bash
docker compose run --rm --no-deps init python manage.py collectstatic --noinput
docker compose run --rm --no-deps init python manage.py check
docker compose run --rm --no-deps init python manage.py check --deploy
docker compose run --rm --no-deps init python manage.py showmigrations --plan
docker compose run --rm --no-deps init python manage.py migrate --check
```

Esperado migrate sin pendientes y static incluido. HSTS subdominios/preload inicial false produce warnings deliberados W005/W021; no habilitarlos ciegamente para silenciar. No makemigrations en VPS. Corregir demás warnings/errores antes de publicación.

## H. LEVANTAR APP

**VPS NUEVO, una vez datos/static/Drive configurados:**

```bash
docker compose up -d --wait web worker nginx
docker compose ps -a
# Si F1 nueva: crear empresa/administrador de producto de forma interactiva.
export INITIAL_COMPANY='PENDIENTE_DE_CONFIRMAR'
export INITIAL_USERNAME='PENDIENTE_DE_CONFIRMAR'
test "$INITIAL_COMPANY" != PENDIENTE_DE_CONFIRMAR && test "$INITIAL_USERNAME" != PENDIENTE_DE_CONFIRMAR || exit 1
docker compose exec web python manage.py preparar_masiscam --empresa "$INITIAL_COMPANY" --username "$INITIAL_USERNAME"
# preparar_masiscam ya crea superusuario con empresa/perfil/rol; no crear otro automáticamente.
```

Omitir creación si importación ya trajo administrador; no automatizarlos. El preparador pregunta password y no lo muestra. Esperado init exit0, web/worker/nginx healthy; no Beat. `up` depende de init (migrate/static idempotentes), pero no iniciar antes de restore. Si falta readiness, mantener DNS anterior y revisar logs.

## I. PRUEBAS LOCALES SERVIDOR

**VPS NUEVO:**

```bash
curl -i http://127.0.0.1:8080/health/
curl -I -H "Host: $DOMAIN" -H 'X-Forwarded-Proto: https' http://127.0.0.1:8080/
curl -I -H "Host: $DOMAIN" -H 'X-Forwarded-Proto: https' http://127.0.0.1:8080/app/cuentas/login/
curl -I -H "Host: $DOMAIN" -H 'X-Forwarded-Proto: https' http://127.0.0.1:8080/consulta/
curl -I http://127.0.0.1:8080/static/web/assets/masiscam-logo.png
curl -I http://127.0.0.1:8080/media/no-publico.pdf
sudo ss -lntp
docker compose exec -T worker celery -A config inspect ping
```

Esperado health200, home/login/consulta200 con header simulado local, static200 y media404. Petición sin forwarded HTTPS a app redirige HTTPS, es correcto. No confiar en ese header desde Internet: lo reemplaza Nginx host. Puertos 8080 solo127.0.0.1; PG/Redis/Gunicorn sin publicación.

## J. NGINX HOST

**VPS NUEVO — bootstrap solo HTTP; SSL aún NO activo:**

```bash
cd /srv/masiscam/sistema
sudo install -d -m 755 /var/www/letsencrypt
python3 tools/render_nginx.py --domain "$DOMAIN" --www "$WWW" --bootstrap > /srv/masiscam/bootstrap.conf
sudo install -m 644 /srv/masiscam/bootstrap.conf /etc/nginx/sites-available/masiscam.conf
sudo ln -s /etc/nginx/sites-available/masiscam.conf /etc/nginx/sites-enabled/masiscam.conf
# Solo en VPS NUEVO limpio: conservar enlace default para rollback y evitar conflicto default_server final.
if test -L /etc/nginx/sites-enabled/default && test "$(readlink -f /etc/nginx/sites-enabled/default)" = /etc/nginx/sites-available/default; then
  sudo mv /etc/nginx/sites-enabled/default /srv/masiscam/backups/nginx-default.link
fi
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

Bootstrap devuelve503 salvo challenge ACME. No montar SSL antes de tener cert. Si `ln` indica enlace existente, revisar su destino; no sobrescribir archivo desconocido. Si nginx -t falla no reload, corregir conflicto/includes/puertos. Site TLS renderer elimina bloque www cuando no existe y resuelve tokens sin tocar config automáticamente.

## K. CERTBOT

**Preparación; emisión webroot ejecutar SOLO DESPUÉS de L y confirmar DNS/AAAA nuevo:**

```bash
args=(-d "$DOMAIN")
test -z "$WWW" || args+=(-d "$WWW")
sudo certbot certonly --webroot -w /var/www/letsencrypt --cert-name "$CERT_NAME" --email "$CERT_EMAIL" --agree-tos "${args[@]}"
sudo certbot certificates
sudo openssl x509 -in "/etc/letsencrypt/live/$CERT_NAME/fullchain.pem" -noout -dates -ext subjectAltName
```

Certificado nuevo preferido, hostname real no inventado, www opcional incluido antes de redirigir HTTPS. CAA/challenge firewall/AAAA deben permitir emisión. No renovar certificados del servidor actual. Si no se quiere cambiar A antes de emitir, elegir DNS-01 con plugin del proveedor confirmado; alternativa manual `certbot certonly --manual --preferred-challenges dns` sirve prevalidación pero no autorrenueva sin hooks. No declarar SSL terminado con certificado manual sin plan de renovación. [Guía Certbot](https://eff-certbot.readthedocs.io/en/stable/using.html).

## L. DNS

**Proveedor DNS, operador: no comandos API inventados.** Registrar A/www/AAAA/TTL viejos antes de cambio. Si reemplaza servicio existente, bajar TTL 24-48h antes y esperar TTL anterior; no eliminar servidor anterior. Cambiar A al NEW_IP y www según CNAME/A real. AAAA solo si IPv6 nuevo configurado/probado; actualizar/retirar AAAA obsoleto explícitamente. Mantener MX/TXT/SPF/DKIM/DMARC y subdominios ajenos. Conservar dominio/rutas QR existentes; no apuntar hostname compartido de otros productos aquí sin routing plan.

**PC o VPS con dig:**

```bash
dig +short NS "$DOMAIN"
dig "$DOMAIN" A +noall +answer
dig "$DOMAIN" AAAA +noall +answer
dig "$DOMAIN" CAA +noall +answer
dig @1.1.1.1 "$DOMAIN" A +noall +answer
dig @8.8.8.8 "$DOMAIN" A +noall +answer
if test -n "$WWW"; then dig "$WWW" A +noall +answer; dig "$WWW" AAAA +noall +answer; fi
nslookup "$DOMAIN" 1.1.1.1
# Confirmar challenge alcanza nuevo servidor, luego ejecutar emisión K.
curl -I "http://$DOMAIN/"
```

503 bootstrap es esperado antes de M. Resolver autoritativo + resolvers consultados, no “propagación mundial” garantizada. Nuevo despliegue sin datos previos no tiene ventana de DB; traslado desde existente necesita mantenimiento/drenaje/backup final y un solo escritor. Downtime se mide en ensayo; no prometer cero.

## M. VALIDACIÓN Y ACTIVACIÓN HTTPS

**VPS NUEVO, certificados ya existentes:**

```bash
cd /srv/masiscam/sistema
test -f "/etc/letsencrypt/live/$CERT_NAME/fullchain.pem" && test -f "/etc/letsencrypt/live/$CERT_NAME/privkey.pem" || exit 1
python3 tools/render_nginx.py --domain "$DOMAIN" --www "$WWW" --cert-name "$CERT_NAME" > /srv/masiscam/https.conf
sudo install -m 644 /srv/masiscam/https.conf /etc/nginx/sites-available/masiscam.conf
sudo nginx -t
sudo systemctl reload nginx
sudo install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
sudo tee /etc/letsencrypt/renewal-hooks/deploy/masiscam-nginx >/dev/null <<'EOF'
#!/bin/sh
set -eu
nginx -t
systemctl reload nginx
EOF
sudo chmod 750 /etc/letsencrypt/renewal-hooks/deploy/masiscam-nginx
sudo systemctl enable --now certbot.timer
sudo certbot renew --dry-run
sudo systemctl list-timers certbot.timer
```

**PC/VPS — HTTPS sin -k:**

```bash
curl --resolve "$DOMAIN:443:$NEW_IP" -I "https://$DOMAIN/"
curl --resolve "$DOMAIN:443:$NEW_IP" -I "https://$DOMAIN/app/cuentas/login/"
curl --resolve "$DOMAIN:443:$NEW_IP" -I "https://$DOMAIN/static/web/assets/masiscam-logo.png"
curl --resolve "$DOMAIN:443:$NEW_IP" -I "https://$DOMAIN/media/no-publico.pdf"
curl --http2 --resolve "$DOMAIN:443:$NEW_IP" -sS -o /dev/null -w 'http=%{http_version} status=%{http_code} total=%{time_total}\n' "https://$DOMAIN/"
openssl s_client -connect "$NEW_IP:443" -servername "$DOMAIN" -verify_return_error </dev/null
curl -I "http://$DOMAIN/"
if test -n "$WWW"; then curl -I "https://$WWW/"; fi
```

Esperado200 home/login/static,404 media/health público, HTTP301 HTTPS, www canónico si existe, verify0 y HTTP2 si curl soporta. Si falla cert, no ignorar TLS ni publicar login HTTP. Validación funcional con cuenta autorizada: login, selección empresa, dashboard, clientes/productos/equipos/ficha/proyecto/documentos/histórico, roles ADMIN/TÉCNICO/CONSULTA, foto privada, consulta por token/serie, QR/legacy y Drive. Prueba negativa ID de otra empresa 403/404. Usar objeto de prueba autorizado para cambios, no datos empresariales reales al azar.

**VPS NUEVO — agregados/logs/recursos:**

```bash
docker compose ps -a
docker compose logs --since 30m --tail 100 web worker db redis nginx init
docker stats --no-stream
docker compose exec -T nginx nginx -t
docker compose exec -T web python manage.py check --deploy
docker compose exec -T web python manage.py migrate --check
docker compose exec -T web python manage.py showmigrations --plan
docker compose exec -T db sh -c 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' <<'SQL'
SELECT count(*) AS tablas FROM pg_tables WHERE schemaname='public';
SELECT 'usuarios' AS entidad,count(*) FROM auth_user
UNION ALL SELECT 'empresas',count(*) FROM accounts_empresa
UNION ALL SELECT 'clientes',count(*) FROM masiscam_cliente
UNION ALL SELECT 'equipos',count(*) FROM masiscam_equipo
UNION ALL SELECT 'proyectos',count(*) FROM masiscam_proyecto
UNION ALL SELECT 'documentos',count(*) FROM masiscam_documento
UNION ALL SELECT 'registros',count(*) FROM masiscam_registroequipo
UNION ALL SELECT 'auditoria',count(*) FROM masiscam_auditoria;
SELECT max(creado_en) FROM masiscam_equipo;
SELECT max(cargado_en) FROM masiscam_documento;
SELECT extname,extversion FROM pg_extension;
SELECT tablename,tableowner FROM pg_tables WHERE schemaname='public';
SELECT sequencename,last_value FROM pg_sequences WHERE schemaname='public';
SQL
uptime
free -h
df -h
vmstat 1 5
iostat -xz 1 3
sudo tail -n 100 /var/log/nginx/masiscam-access.log
sudo tail -n 100 /var/log/nginx/masiscam-error.log
```

No mostrar logs/datos sensibles en canales públicos. Comparar conteos/tokens/PK/archivos con backup origen compatible o exportación; hashes no sustituyen smoke funcional. Primeras horas mirar 5xx, latencia, OOM/restarts, disco, cola Celery, errores Drive y certificados. Retención/logrotate Nginx host normalmente instalada por paquete: confirmar regla incluye masiscam-*.log; logging Docker rotado10m×3 por servicio.

## N. BACKUP AUTOMÁTICO

**VPS NUEVO:**

```bash
cd /srv/masiscam/sistema
bash deploy/scripts/backup.sh
# Ensayo usando el directorio COMPLETO recién generado, reemplazar valor:
export BACKUP_TO_CHECK='PENDIENTE_DE_CONFIRMAR'
test "$BACKUP_TO_CHECK" != PENDIENTE_DE_CONFIRMAR || exit 1
bash deploy/scripts/restore-check.sh "$BACKUP_TO_CHECK"
sudo install -m 644 deploy/systemd/masiscam-backup.service /etc/systemd/system/masiscam-backup.service
sudo install -m 644 deploy/systemd/masiscam-backup.timer /etc/systemd/system/masiscam-backup.timer
sudo systemctl daemon-reload
sudo systemctl enable --now masiscam-backup.timer
sudo systemctl list-timers masiscam-backup.timer
sudo systemctl start masiscam-backup.service
sudo journalctl -u masiscam-backup.service --since today --no-pager
```

Snapshot diario online no garantiza atomicidad DB/media; traslado final: maintenance/drain externos, `docker compose stop -t 150 web worker`, luego `bash deploy/scripts/backup.sh --writers-stopped`. No parar automáticamente producción en cron. Restore-check en DB nueva aislada sin puertos ni Drive; mantiene ensayo para revisión, no borra volúmenes. Validación archive/manifest/restore completa SQL, luego smoke de app aislada.

Backups privados `/srv/masiscam/backups`, .partial fallidos conservados, completos rotan30d con marker; definir retención legal/RPO/RTO antes de usar. Config .env/secrets/certificados se respalda aparte cifrado, no dentro Git. Google Drive tiene retención/backup independiente. Repositorio externo cifrado (p.ej. solución de backup con credenciales propias), proveedor/ubicación **PENDIENTE DE CONFIRMAR**; hasta replicar y probar, backup mismo VPS no es recuperación ante pérdida de VPS. Alertar timer fallido y replicación; la guía no configura cuenta externa inventada.

## O. ROLLBACK

No destruir servidor anterior ni volumen nuevo. Nueva publicación sin datos: mantener bootstrap503 mientras se corrige; revertir config edge desde copia conservada y nginx -t antes de reload. Error de release con mismo esquema: volver fuentes/tag anterior y recrear app, sin bajar stack DB/Redis.

**VPS afectado — congelar antes de restauración/reconciliación:**

```bash
cd /srv/masiscam/sistema
# Activar mantenimiento edge validado; no existe flag mágico Django.
docker compose stop -t 150 web worker
bash deploy/scripts/backup.sh --writers-stopped
```

Si se sustituye VPS anterior: restaurar A/www/AAAA registrados solo tras decidir escritor único; comprobar TLS viejo con curl --resolve y su salud. En servidor MASISCAM anterior compatible, `docker compose start web worker` únicamente si datos intactos y no se perderán cambios posteriores; retirar mantenimiento real del proxy manualmente. Si hubo nuevas operaciones, congelar ambos y preservar dump/media nuevos y cambios Drive antes de decidir; no devolver DNS a snapshot viejo ni reimportar dos veces. Migraciones nuevas pueden impedir rollback de código: recuperación hacia delante o DB snapshot en destino aislado con reconciliación revisada.

**PC/servidor técnico:**

```bash
export OLD_IP='PENDIENTE_DE_CONFIRMAR'
test "$OLD_IP" != PENDIENTE_DE_CONFIRMAR || exit 1
curl --resolve "$DOMAIN:443:$OLD_IP" -I "https://$DOMAIN/"
dig @1.1.1.1 "$DOMAIN" A +noall +answer
dig "$DOMAIN" AAAA +noall +answer
```

DNS revertido tiene caches: mantener nuevo en mantenimiento para evitar dos escritores. Importación selectiva histórica no tiene importador inverso; los cambios posteriores necesitan proceso específico. Tras varios días estables: retirar infraestructura previa solo con autorización/backup verificado; revocar accesos temporales, rotar credenciales de traslado, revisar DNS/cert/alertas, retirar backups temporales según retención. No ejecutar borrados automáticos fuera de rotación segura acordada.
