#!/usr/bin/env bash
# Restore only into a NEW isolated project/database; never uses production credentials.
set -euo pipefail
umask 077
PROJECT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
[[ $# -eq 1 ]] || { echo "Uso: restore-check.sh /ruta/backup-completo"; exit 1; }
BACKUP=$(realpath -- "$1")
[[ -f "$BACKUP/.masiscam-backup" && -f "$BACKUP/SHA256SUMS" ]] || exit 1
(cd "$BACKUP" && sha256sum -c SHA256SUMS)
cd -- "$PROJECT_DIR"
# Image must already be built. Create a separate workdir to avoid production .env.
TMP=$(mktemp -d)
PROJECT="masiscam-restorecheck-$(date -u +%Y%m%d%H%M%S)-$$"
export RESTORE_PROJECT="$PROJECT"
export RESTORE_DIR="$TMP"
export RESTORE_BACKUP="$BACKUP"
python3 - <<'PY'
import os,secrets
from pathlib import Path
p=Path(os.environ['RESTORE_DIR'])
p.joinpath('.env').write_text('RESTORE_PASSWORD='+secrets.token_hex(32)+'\n')
p.joinpath('.env').chmod(0o600)
PY
# No app/worker/ports and no production volumes; DB user/initdb password is independent.
cat > "$TMP/compose.yml" <<'YAML'
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: masiscam_check
      POSTGRES_USER: masiscam_check
      POSTGRES_PASSWORD: ${RESTORE_PASSWORD:?}
    volumes:
      - check_postgres:/var/lib/postgresql/data
    healthcheck:
      test: [CMD-SHELL, 'pg_isready -U "$${POSTGRES_USER}" -d "$${POSTGRES_DB}"']
      interval: 2s
      timeout: 5s
      retries: 30
volumes:
  check_postgres:
YAML
compose=(docker compose --project-directory "$TMP" -p "$PROJECT" -f "$TMP/compose.yml")
"${compose[@]}" up -d --wait db
"${compose[@]}" exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-acl --exit-on-error --single-transaction' < "$BACKUP/masiscam.dump"
"${compose[@]}" exec -T db sh -c 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' <<'SQL'
SELECT count(*) AS tablas FROM pg_tables WHERE schemaname='public';
SELECT count(*) AS usuarios FROM auth_user;
SELECT count(*) AS equipos FROM masiscam_equipo;
SELECT count(*) AS documentos FROM masiscam_documento;
SQL
# Validate archive contents in temporary directory without trusting traversal/symlinks.
python3 - <<'PY'
import os,tarfile,hashlib,json
from pathlib import Path
backup=Path(os.environ['RESTORE_BACKUP']); dest=Path(os.environ['RESTORE_DIR'])/'media';dest.mkdir()
with tarfile.open(backup/'media.tar.gz') as archive:
    archive.extractall(dest,filter='data')
actual={}
for p in dest.rglob('*'):
    if p.is_symlink():raise SystemExit('Symlink no permitido en backup media')
    if p.is_file():
        h=hashlib.sha256()
        with p.open('rb') as f:
            for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
        actual[p.relative_to(dest).as_posix()]=h.hexdigest()
assert actual==json.loads((backup/'media-sha256.json').read_text()),'Media difiere'
print('Restore DB e integridad media comprobados; falta smoke funcional con app aislada.')
PY
"${compose[@]}" stop db
echo "Ensayo conservado sin borrar: proyecto=$PROJECT directorio=$TMP"
echo "Revisar y limpiar manualmente SOLO el volumen ${PROJECT}_check_postgres cuando ya no se necesite."
