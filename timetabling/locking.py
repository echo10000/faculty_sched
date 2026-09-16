from django.db import connection


SCHEDULING_ADVISORY_LOCK_ID = 74190304


def scheduling_lock() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [SCHEDULING_ADVISORY_LOCK_ID],
        )
