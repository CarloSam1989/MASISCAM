"""Generate a private local .env; never prints secret values or overwrites a file."""
import argparse
import re
import secrets
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--development", action="store_true")
    parser.add_argument("--domain")
    parser.add_argument("--www", default="")
    parser.add_argument("--release", default="local")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    target = root / ".env"
    if target.exists():
        parser.error(".env ya existe; no se sobrescribe.")
    if not args.development and not args.domain:
        parser.error("Producción requiere --domain.")
    for host in [args.domain, args.www]:
        if host and ("__" in host or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host)):
            parser.error("Dominio inválido; use un hostname real sin esquema/puerto.")
    template = ".env.example" if args.development else ".env.production.example"
    content = (root / template).read_text(encoding="utf-8")
    password = secrets.token_hex(32)
    values = {"SECRET_KEY": secrets.token_urlsafe(64), "POSTGRES_PASSWORD": password,
              "DATABASE_URL": f"postgres://masiscam:{password}@db:5432/masiscam", "MASISCAM_RELEASE": args.release}
    if not args.development:
        hosts = [args.domain] + ([args.www] if args.www else [])
        values.update(ALLOWED_HOSTS=",".join(hosts + ["localhost", "127.0.0.1"]),
                      CSRF_TRUSTED_ORIGINS=",".join("https://"+h for h in hosts),
                      MASISCAM_PUBLIC_BASE_URL="https://"+args.domain)
    lines = []
    for line in content.splitlines():
        name = line.split("=", 1)[0]
        lines.append(f"{name}={values[name]}" if name in values else line)
    import os
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        output.write("\n".join(lines)+"\n")
    target.chmod(0o600)
    print(".env creado de forma privada. Configure Drive antes de iniciar producción.")


if __name__ == "__main__":
    main()
