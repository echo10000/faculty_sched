# CampusLoad deployment and recovery

CampusLoad is a Django WSGI application backed by PostgreSQL. Deploy the existing `config.wsgi:application` behind a TLS-terminating reverse proxy. The legacy `scheduling` autoscheduler is unmounted; `timetabling` is authoritative. No development seed belongs in production.

## Before deployment

- Use a supported Linux Python and PostgreSQL installation. The capstone verification used Python 3.14, Django 5.2 and PostgreSQL 17 on Windows; repeat a Linux smoke test on the actual host.
- Provision a private PostgreSQL role/database. Install `btree_gist` in that database before migrations, or grant the migration role the required extension privilege. The runtime role should not retain database creation privileges.
- Keep `SECRET_KEY`, database password and private host configuration outside Git. Start from `.env.production.example`, replacing every placeholder. Use `DEBUG=False`, a specific `ALLOWED_HOSTS` list and the deployed HTTPS origin in `CSRF_TRUSTED_ORIGINS`.
- Serve the Gunicorn port only to the reverse proxy. Serve `/static/` from `STATIC_ROOT` after `collectstatic`. There are no user uploads requiring a media service.
- Terminate HTTPS at the proxy. Set `TRUST_PROXY_SSL_HEADER=True` only if that proxy strips any client-supplied `X-Forwarded-Proto` and writes its own value. Otherwise leave it false and let the proxy communicate a secure scheme to WSGI. Apply login rate limiting at the public edge as an additional control.
- Enable HSTS subdomains and preload only after verifying that every affected host uses HTTPS. Defaults intentionally leave both off. Review backup, restore and a rollback plan before each major upgrade.

## Install and start on Linux

Run from the repository root with a private environment already loaded:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python manage.py check
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py check --deploy
.venv/bin/gunicorn --bind 127.0.0.1:8000 --workers 2 --timeout 360 config.wsgi:application
```

The 360-second worker timeout accommodates the existing configurable Phase 5 solver limit of at most 300 seconds. Size workers and request limits for the real host and proxy. Do not use `runserver` in production. Run migrations as a release step before switching traffic; do not run them in every worker. Review `check --deploy` against the actual host settings. The subdomain/preload warnings are intentional unless those policies are enabled after review.

## Backup and restore

Take a database backup before major schema upgrades and after important official schedule decisions. Store it encrypted with restricted access, away from the application host. A PostgreSQL custom-format backup includes official bookings, immutable approval snapshots, workflow history and audit records together:

```sh
pg_dump --format=custom --no-owner --no-privileges --file=campusload.backup "$DB_NAME"
```

Restore into a **new, empty** database for a rehearsal or recovery. Provision its role and extension first, then restore and check the application before moving traffic:

```sh
createdb --owner="$DB_USER" campusload_restore
psql --dbname=campusload_restore -c 'CREATE EXTENSION IF NOT EXISTS btree_gist;'
pg_restore --no-owner --no-privileges --dbname=campusload_restore campusload.backup
DB_NAME=campusload_restore .venv/bin/python manage.py check
DB_NAME=campusload_restore .venv/bin/python manage.py showmigrations
```

Use a database role authorized for the extension and restore operations. Never restore over the only production copy. Rehearse recovery with an isolated database, verify row counts and a scoped official schedule, then document the recovery point and operator. PostgreSQL client tools must match the deployed server family closely enough for the chosen dump format.

## Operational checks

Capture standard-error application logs securely; `django.request` errors and `django.security` warnings go to the process console. Restrict log access and do not log credentials. Monitor HTTP 5xx, database availability, disk space, solver timeout/failure, failed logins and backup completion. Review audit records in the application for business actions; console logs do not replace them. Database-backed login failure counters limit account and source attempts for 15 minutes without storing raw usernames or addresses. Schedule `.venv/bin/python manage.py purge_login_failures` periodically to remove expired counters. `REMOTE_ADDR` is the server-observed address; behind a proxy, its source bucket may group clients, so configure the proxy and its own rate limit deliberately. The current report renderers build one authorized dataset in memory, so size the worker and proxy response limits for institutional report volume and monitor export duration.
