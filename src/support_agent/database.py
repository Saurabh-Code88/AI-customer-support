"""SQLAlchemy database and LangGraph checkpointer configuration."""
from contextlib import contextmanager
from pathlib import Path
import time
from typing import Iterator

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from .config import DATABASE_CONNECT_TIMEOUT, DATABASE_PATH, DATABASE_URL

_engine: Engine | None = None
_sessions: sessionmaker[Session] | None = None
_database_url: str | None = None


def _normalize_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


def configure_database(url: str | None = None) -> Engine:
    """Configure the process database, primarily useful for per-test SQLite files."""
    global _engine, _sessions, _database_url
    if _engine is not None:
        _engine.dispose()
    _database_url = _normalize_url(
        url if url is not None else DATABASE_URL or f"sqlite:///{DATABASE_PATH}"
    )
    parsed_url = make_url(_database_url)
    connect_args = {"check_same_thread": False} if parsed_url.get_backend_name() == "sqlite" else {}
    if parsed_url.get_backend_name() == "postgresql":
        connect_args["connect_timeout"] = DATABASE_CONNECT_TIMEOUT
    if parsed_url.get_backend_name() == "sqlite" and parsed_url.database not in (None, ":memory:"):
        Path(parsed_url.database).parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(_database_url, connect_args=connect_args, pool_pre_ping=True)
    if parsed_url.get_backend_name() == "sqlite":
        @event.listens_for(_engine, "connect")
        def enable_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    _sessions = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_engine() -> Engine:
    return _engine or configure_database()


def get_session_factory() -> sessionmaker[Session]:
    if _sessions is None:
        configure_database()
    assert _sessions is not None
    return _sessions


def current_database_url() -> str:
    if _database_url is None:
        configure_database()
    assert _database_url is not None
    return _database_url


def is_postgres() -> bool:
    return make_url(current_database_url()).get_backend_name() == "postgresql"


def run_migrations() -> None:
    """Apply checked-in Alembic migrations to the configured database."""
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    with get_engine().connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


@contextmanager
def checkpointer_context() -> Iterator[object]:
    """Yield a persistent saver backed by the same database as application records."""
    url = current_database_url()
    if is_postgres():
        from langgraph.checkpoint.postgres import PostgresSaver

        checkpoint_url = url.replace("postgresql+psycopg://", "postgresql://", 1)
        with PostgresSaver.from_conn_string(checkpoint_url) as saver:
            saver.setup()
            yield saver
    else:
        from langgraph.checkpoint.sqlite import SqliteSaver

        path = make_url(url).database
        if not path or path == ":memory:":
            raise ValueError("SQLite checkpointing requires a persistent DATABASE_PATH")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with SqliteSaver.from_conn_string(path) as saver:
            saver.setup()
            yield saver


def database_ready() -> bool:
    try:
        with get_engine().connect() as connection:
            return connection.execute(text("SELECT 1")).scalar_one() == 1
    except Exception:  # noqa: BLE001 - readiness must fail closed without leaking DSN details
        return False


def create_ticket(ticket_id: str, message: str, email: str) -> None:
    upsert_ticket(ticket_id, message, email, status="processing")


def upsert_ticket(
    ticket_id: str,
    message: str,
    email: str,
    *,
    status: str,
    values: dict | None = None,
    steps: list | None = None,
    created_at: float | None = None,
    audit: list[str] | None = None,
) -> dict:
    """Persist ticket state and replace its ordered audit trail atomically."""
    from sqlalchemy import delete

    from .models import AuditEvent, TicketRecord
    from .logging_config import redact_emails

    values = values or {}
    safe_values = {key: value for key, value in values.items() if key != "__interrupt__"}
    safe_values["audit"] = redact_emails(audit if audit is not None else safe_values.get("audit", []))
    factory = get_session_factory()
    with factory.begin() as session:
        record = session.get(TicketRecord, ticket_id)
        now = created_at or (record.created_at if record else None)
        now = now or time.time()
        resolved_at = record.resolved_at if record else None
        if status in ("resolved", "escalated", "needs_info") and resolved_at is None:
            resolved_at = time.time()
        if record is None:
            record = TicketRecord(ticket_id=ticket_id, message=message, email=email, created_at=now)
            session.add(record)
        record.message = message
        record.email = email
        record.status = status
        record.created_at = now
        record.resolved_at = resolved_at
        record.values_json = safe_values
        record.steps_json = steps if steps is not None else (record.steps_json or [])
        if audit is not None:
            session.execute(delete(AuditEvent).where(AuditEvent.ticket_id == ticket_id))
            session.add_all(
                AuditEvent(ticket_id=ticket_id, position=index, event=redact_emails(entry))
                for index, entry in enumerate(audit)
            )
    return get_ticket(ticket_id)  # type: ignore[return-value]


def get_ticket(ticket_id: str) -> dict | None:
    from .models import AuditEvent, TicketRecord

    with get_session_factory()() as session:
        record = session.get(TicketRecord, ticket_id)
        if record is None:
            return None
        audit = session.query(AuditEvent.event).filter_by(ticket_id=ticket_id).order_by(AuditEvent.position).all()
        return {
            "ticket_id": record.ticket_id,
            "message": record.message,
            "email": record.email,
            "status": record.status,
            "created_at": record.created_at,
            "resolved_at": record.resolved_at,
            "values": record.values_json or {},
            "steps": record.steps_json or [],
            "audit": [row[0] for row in audit],
        }


def list_tickets(status: str | None = None) -> list[dict]:
    from .models import TicketRecord

    with get_session_factory()() as session:
        query = session.query(TicketRecord).order_by(TicketRecord.created_at.desc())
        if status is not None:
            query = query.filter(TicketRecord.status == status)
        records = query.all()
        return [
            {
                "ticket_id": record.ticket_id,
                "message": record.message,
                "email": record.email,
                "status": record.status,
                "created_at": record.created_at,
                "resolved_at": record.resolved_at,
                "values": record.values_json or {},
                "steps": record.steps_json or [],
            }
            for record in records
        ]


def analytics() -> dict:
    tickets = list_tickets()
    total = len(tickets)
    auto_resolved = sum(
        item["status"] == "resolved"
        and not item["values"].get("policy_result", {}).get("needs_approval")
        for item in tickets
    )
    escalated = sum(item["status"] == "escalated" for item in tickets)
    approval_needed = sum(
        bool(item["values"].get("policy_result", {}).get("needs_approval")) for item in tickets
    )
    approval_completed = sum(
        item["values"].get("approval") in ("approved", "rejected") for item in tickets
    )
    durations = [
        item["resolved_at"] - item["created_at"] for item in tickets if item["resolved_at"]
    ]
    return {
        "total_tickets": total,
        "auto_resolution_rate": round(auto_resolved / total * 100, 1) if total else 0,
        "escalation_rate": round(escalated / total * 100, 1) if total else 0,
        "approval_rate": round(approval_completed / approval_needed * 100, 1)
        if approval_needed
        else 0,
        "average_resolution_seconds": round(sum(durations) / len(durations), 1)
        if durations
        else 0,
    }
