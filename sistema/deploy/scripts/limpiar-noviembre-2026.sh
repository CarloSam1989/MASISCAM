#!/usr/bin/env bash
# Uso: bash deploy/scripts/limpiar-noviembre-2026.sh EMPRESA_ID ADMIN_USER_ID [--simular]
# Sin Internet: usa pg_restore de la imagen postgres:16-alpine que ya usa el servicio db.
# --simular: respaldo + restauración verificada + ensayo con rollback; NUNCA borra.
# Sin --simular pide, además, confirmación escrita exacta antes del borrado definitivo.
# Conserva usuarios, roles, credenciales, catálogos, configuración, auditoría y archivos compartidos.
set -euo pipefail
umask 077
EMPRESA=${1:?Uso: limpiar-noviembre-2026.sh EMPRESA_ID ADMIN_USER_ID [--simular]}
USUARIO=${2:?Uso: limpiar-noviembre-2026.sh EMPRESA_ID ADMIN_USER_ID [--simular]}
MODO=${3:-}
[[ "$EMPRESA" =~ ^[0-9]+$ && "$USUARIO" =~ ^[0-9]+$ && ( -z "$MODO" || "$MODO" == --simular ) ]] || { echo "Argumentos inválidos"; exit 1; }
cd -- "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
[[ -f .env ]] || { echo ".env ausente"; exit 1; }
set -a; . ./.env; set +a
BACKUP_ROOT=${BACKUP_ROOT:-/srv/masiscam/backups}
SUFIJO=$$
RUNNER=masiscam-limpieza-run-$SUFIJO
QA=masiscam-limpieza-qa-$SUFIJO
cleanup() { docker rm -f "$QA" "$RUNNER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "== 1/5 Respaldo completo (base de datos + PDF/documentos en media) =="
bash deploy/scripts/backup.sh
BK=$(ls -1d "$BACKUP_ROOT"/backup-* | grep -v '\.partial$' | sort | tail -1)
for f in masiscam.dump media.tar.gz media-sha256.json SHA256SUMS .masiscam-backup; do
  [[ -s "$BK/$f" || "$f" == .masiscam-backup && -f "$BK/$f" ]] || { echo "Respaldo incompleto: falta $f"; exit 1; }
done
(cd "$BK" && sha256sum -c SHA256SUMS >/dev/null) || { echo "Respaldo corrupto"; exit 1; }
CUST="$BK/custodia-limpieza-nov2026-$(date -u +%H%M%S)"
mkdir -p "$CUST"

echo "== 2/5 PostgreSQL 16 aislado (misma imagen que db) y ejecutor temporal =="
# El ejecutor comparte red con la app/db; el QA comparte su red local (127.0.0.1) y no tiene puertos publicados.
docker compose run -d --no-deps --user root --name "$RUNNER" --entrypoint sleep \
  -v "$BK":/backup:ro -v "$CUST":/custodia -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$(command -v docker)":/usr/local/bin/docker:ro web infinity >/dev/null
QAPASS=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')
docker run -d --name "$QA" --network "container:$RUNNER" -e POSTGRES_USER=qa -e POSTGRES_PASSWORD="$QAPASS" \
  -e POSTGRES_DB=masiscam_qa_limpieza postgres:16-alpine >/dev/null
until docker exec "$QA" pg_isready -U qa -d masiscam_qa_limpieza >/dev/null 2>&1; do sleep 1; done
cat > "$CUST/pg_restore" <<SHIM
#!/bin/sh
# pg_restore 16 del contenedor QA; el archivo de respaldo se entrega por stdin.
for last; do :; done
n=\$#; i=0
for a; do i=\$((i+1)); [ \$i -lt \$n ] && set -- "\$@" "\$a"; shift; done
exec docker exec -i -e PGHOST=127.0.0.1 -e PGPORT=5432 -e PGUSER=qa -e PGPASSWORD='$QAPASS' -e PGDATABASE=masiscam_qa_limpieza "$QA" pg_restore "\$@" < "\$last"
SHIM
chmod 755 "$CUST/pg_restore"
QAURL="postgres://qa:${QAPASS}@127.0.0.1:5432/masiscam_qa_limpieza"

mc() {
  docker exec -e GOOGLE_DRIVE_ENABLED=False "$RUNNER" python manage.py depurar_dos_equipos_prueba "$@" \
    --mes 2026-11 --completo --empresa "$EMPRESA" --usuario "$USUARIO"
}

echo "== 3/5 Simulación (solo lectura) =="
SIM=$(mc simular)
HUELLA=$(printf '%s' "$SIM" | python3 -c 'import json,sys;print(json.load(sys.stdin)["huella"])')
printf '%s' "$SIM" | python3 -c '
import json,sys
p=json.load(sys.stdin)["simulacion"];c=p["cantidades"]
print("Registros a eliminar:",c["eliminar"],"| PDF:",c["pdf"],"| archivos:",c["archivos"],"| raíces conservadas:",c["raices_conservadas"])
print(json.dumps(c["por_tabla"],indent=1,sort_keys=True)); print("Bloqueos:",p["bloqueos"] or "ninguno")'
# Todo archivo que se borrará debe estar dentro del respaldo de media, con su sha256.
printf '%s' "$SIM" | python3 -c '
import json,sys
plan=json.load(sys.stdin)["simulacion"]; media=json.load(open(sys.argv[1]))
faltan=[a["nombre"] for a in plan["archivos"] if a["nombre"] not in media]
sys.exit("Archivos fuera del respaldo media: %s" % faltan) if faltan else print("Respaldo media contiene los %d archivos a eliminar" % len(plan["archivos"]))' "$BK/media-sha256.json"

echo "== 4/5 Restauración verificada y ensayo de limpieza con rollback =="
mc verificar-respaldo --huella "$HUELLA" --respaldo /backup/masiscam.dump --qa-url "$QAURL" \
   --pg-restore /custodia/pg_restore --carpeta /custodia --salida /custodia/verificacion.json >/dev/null
echo "Respaldo verificado: $CUST/verificacion.json"
if [[ "$MODO" == --simular ]]; then echo "Simulación completa; no se borró nada."; exit 0; fi

echo "== 5/5 Borrado definitivo =="
CONF="LIMPIAR COMPLETO $EMPRESA 2026-11 $HUELLA"
read -r -p "Escriba exactamente '$CONF' para borrar (otra cosa cancela): " ANS
[[ "$ANS" == "$CONF" ]] || { echo "Cancelado; no se borró nada."; exit 1; }
mc eliminar --huella "$HUELLA" --respaldo /backup/masiscam.dump --verificacion /custodia/verificacion.json \
   --carpeta /custodia --motivo "Limpieza de pruebas de noviembre 2026" --confirmar "$CONF"
