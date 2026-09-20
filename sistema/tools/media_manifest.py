"""Private media integrity manifest; invoked by backup and restore-check."""
import hashlib
import json
from pathlib import Path
import sys

root = Path("/app/media").resolve()
manifest = {}
for path in sorted(root.rglob("*")):
    if path.is_symlink():
        raise SystemExit("Media contiene symlinks: revisar manualmente.")
    if path.is_file():
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        manifest[path.relative_to(root).as_posix()] = digest.hexdigest()
if len(sys.argv) > 1:
    expected = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if manifest != expected:
        raise SystemExit("Integridad media incorrecta.")
    print("Integridad media correcta: " + str(len(manifest)) + " archivos")
else:
    print(json.dumps(manifest, sort_keys=True))
