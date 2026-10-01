"""Start an isolated Windows PostgreSQL instance; never alter an existing cluster."""
import argparse
import hashlib
import io
from pathlib import Path
import secrets
import subprocess
import tarfile
import urllib.request
import zipfile

import psycopg2
from psycopg2 import sql

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / ".local"
VERSION = "17.6.0"
PACKAGE = f"embedded-postgres-binaries-windows-amd64-{VERSION}.jar"
URL = f"https://repo.maven.apache.org/maven2/io/zonky/test/postgres/embedded-postgres-binaries-windows-amd64/{VERSION}/{PACKAGE}"
PORT = 55432
USER = "campusload_dev"
DATABASE = "campusload_phase1"


def run(*args):
    return subprocess.run([str(a) for a in args], check=True, creationflags=subprocess.CREATE_NO_WINDOW)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop"])
    parser.add_argument("--configure-env", action="store_true", help="Back up .env once and configure this isolated development database.")
    args = parser.parse_args()
    LOCAL.mkdir(exist_ok=True)
    binaries = LOCAL / "postgres"
    data = LOCAL / "pgdata"
    control = binaries / "bin/pg_ctl.exe"
    if args.action == "stop":
        run(control, "-D", data, "-m", "fast", "stop")
        return
    if not control.exists():
        payload = urllib.request.urlopen(URL, timeout=60).read()
        checksum = urllib.request.urlopen(URL + ".sha256", timeout=30).read().decode().split()[0]
        if hashlib.sha256(payload).hexdigest() != checksum:
            raise RuntimeError("PostgreSQL package checksum mismatch.")
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            packed = archive.read("postgres-windows-x86_64.txz")
        with tarfile.open(fileobj=io.BytesIO(packed), mode="r:xz") as archive:
            archive.extractall(binaries, filter="data")
    password_path = LOCAL / "pg-password"
    if not data.exists():
        password_path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        run(binaries / "bin/initdb.exe", "-D", data, "-U", USER, "--pwfile", password_path,
            "--auth=scram-sha-256", "--encoding=UTF8", "--locale=C")
    password = password_path.read_text(encoding="utf-8").strip()
    status = subprocess.run([str(control), "-D", str(data), "status"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if status.returncode:
        run(control, "-D", data, "-l", LOCAL / "postgres.log", "-o", f"-h 127.0.0.1 -p {PORT}", "-w", "start")
    connection = psycopg2.connect(host="127.0.0.1", port=PORT, user=USER, password=password, dbname="postgres")
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname=%s", [DATABASE])
        if not cursor.fetchone():
            cursor.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(DATABASE), sql.Identifier(USER)))
    connection.close()
    if args.configure_env:
        env_path = ROOT / ".env"
        original = env_path.read_text(encoding="utf-8-sig") if env_path.exists() else ""
        backup = LOCAL / "original.env"
        if original and not backup.exists():
            backup.write_text(original, encoding="utf-8")
        values = dict(line.split("=", 1) for line in original.splitlines() if "=" in line and not line.startswith("#"))
        values.update(DB_NAME=DATABASE, DB_USER=USER, DB_PASSWORD=password, DB_HOST="127.0.0.1", DB_PORT=str(PORT), DEBUG="True")
        if not values.get("SECRET_KEY") or len(values["SECRET_KEY"]) < 50:
            values["SECRET_KEY"] = secrets.token_urlsafe(64)
        values.setdefault("TIME_ZONE", "Asia/Manila")
        env_path.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
    print(f"Development PostgreSQL ready on 127.0.0.1:{PORT}; database {DATABASE}. No credentials printed.")


if __name__ == "__main__":
    main()
