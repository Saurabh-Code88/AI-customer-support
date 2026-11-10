"""Small database readiness probe for the configured PostgreSQL instance."""
from .config import DATABASE_CONNECT_TIMEOUT, DATABASE_URL


def database_ready() -> bool:
    if not DATABASE_URL:
        return False
    try:
        from psycopg import connect

        with connect(DATABASE_URL, connect_timeout=DATABASE_CONNECT_TIMEOUT) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() == (1,)
    except Exception:  # noqa: BLE001 - readiness must fail closed without leaking DSN details
        return False
