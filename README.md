# CampusLoad

Campus-wide faculty workload and scheduling foundation built with Django 5 and PostgreSQL.

## Local setup

1. Install PostgreSQL and create a database named `campusload` (or choose your own name).
2. Copy `.env.example` to `.env` and update the database credentials.
3. Install dependencies: `python -m pip install -r requirements.txt`.
4. Run migrations: `python manage.py migrate`.
5. Load demo data: `python manage.py seed_demo_data`.
6. Optionally create an administrator with `python manage.py createsuperuser`, then run `python manage.py runserver`.

Configuration uses `python-decouple`: it keeps local secrets and database settings in `.env`, outside source control, while providing development defaults.
