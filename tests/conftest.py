import pytest
import os

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-for-support-agent-session-tokens")

from support_agent.database import checkpointer_context, configure_database, run_migrations
from support_agent.seed import seed_orders


@pytest.fixture(autouse=True)
def isolated_database(tmp_path):
    engine = configure_database(f"sqlite:///{tmp_path / 'test-database.sqlite'}")
    run_migrations()
    seed_orders(reset=True)
    yield
    engine.dispose()


@pytest.fixture
def checkpointer(isolated_database):
    with checkpointer_context() as saver:
        yield saver
