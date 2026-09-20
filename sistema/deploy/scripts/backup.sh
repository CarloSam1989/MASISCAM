#!/usr/bin/env bash
set -euo pipefail
umask 077
PROJECT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
BACKUP_ROOT=${BACKUP_ROOT:-/srv/masiscam/backups}
RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-30}
MODE=${1:-online}
[[ "$MODE" == online || "$MODE" == --writers-stopped ]] || { echo "Uso: backup.sh [--writers-stopped]"; exit 1; }
[[ "$RETENTION_DAYS" =~ ^[0-9]+$ && "$RETENTION_DAYS" -ge 1 ]] || exit 1
[[ "$BACKUP_ROOT" = /* && "$BACKUP_ROOT" != / ]] || { echo "Backup root debe ser absoluto y dedicado"; exit 1; }
mkdir -p -- "$BACKUP_ROOT"
BACKUP_ROOT=$(realpath -- "$BACKUP_ROOT")
chmod 700 "$BACKUP_ROOT"
cd -- "$PROJECT_DIR"
[[ -f .env ]] || { echo ".env ausente"; exit 1; }
exec 9>"$BACKUP_ROOT/.backup.lock"
flock -n 9 || { echo "Ya hay un backup en ejecución"; exit 1; }
if [[ "$MODE" == --writers-stopped ]]; then
    if docker compose ps --status running --services | grep -Eq '^(web|worker)$'; then
        echo "Detener escritores web/worker y productores externos antes de snapshot final"; exit 1
    fi
fi
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$BACKUP_ROOT/backup-$STAMP.partial"
mkdir -m 700 -- "$OUT"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$OUT/masiscam.dump"
test -s "$OUT/masiscam.dump"
docker compose exec -T db pg_restore --list < "$OUT/masiscam.dump" > "$OUT/pg-toc.txt"
docker compose run -T --rm --no-deps --entrypoint tar web -C /app/media -czf - . > "$OUT/media.tar.gz"
# Hash the archived bytes, not a second live directory walk that could race uploads.
python3 - "$OUT/media.tar.gz" > "$OUT/media-sha256.json" <<'PY'
import hashlib,json,sys,tarfile
from pathlib import PurePosixPath
manifest={}
with tarfile.open(sys.argv[1]) as archive:
    for member in archive:
        name=PurePosixPath(member.name)
        if name.is_absolute() or '..' in name.parts or member.issym() or member.islnk():
            raise SystemExit('Backup media contiene una ruta o enlace inseguro')
        if member.isdir():continue
        if not member.isfile():raise SystemExit('Tipo de archivo media no permitido')
        digest=hashlib.sha256()
        with archive.extractfile(member) as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
        if name.as_posix() in manifest:raise SystemExit('Ruta duplicada en archivo media')
        manifest[name.as_posix()]=digest.hexdigest()
print(json.dumps(manifest,sort_keys=True))
PY
printf 'mode=%s\ncreated_utc=%s\n' "$MODE" "$STAMP" > "$OUT/metadata.txt"
(cd "$OUT" && sha256sum masiscam.dump media.tar.gz media-sha256.json metadata.txt > SHA256SUMS && sha256sum -c SHA256SUMS)
touch "$OUT/.masiscam-backup"
FINAL="$BACKUP_ROOT/backup-$STAMP"
mv -- "$OUT" "$FINAL"
# Solo rotar nuestros directorios completos, con marker y dentro del root resuelto.
while IFS= read -r -d '' old; do
    resolved=$(realpath -- "$old")
    [[ "$resolved" == "$BACKUP_ROOT"/backup-* && -f "$resolved/.masiscam-backup" && ! -L "$old" ]] || continue
    rm -rf -- "$resolved"
done < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'backup-*' ! -name '*.partial' -mtime "+$RETENTION_DAYS" -print0)
echo "Backup privado completo: $FINAL. Replicar cifrado fuera del VPS y ensayar restore."
# Online: DB consistente, DB/media NO atómicos. Final requiere --writers-stopped.
