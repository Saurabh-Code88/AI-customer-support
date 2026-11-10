"""Production ASGI entry point with JSON-formatted Uvicorn logs."""
import uvicorn

from .config import PORT, WEB_CONCURRENCY
from .logging_config import JSON_LOGGING_CONFIG, configure_logging


def main() -> None:
    configure_logging()
    uvicorn.run(
        "support_agent.api:app",
        host="0.0.0.0",
        port=PORT,
        workers=WEB_CONCURRENCY,
        log_config=JSON_LOGGING_CONFIG,
    )


if __name__ == "__main__":
    main()
